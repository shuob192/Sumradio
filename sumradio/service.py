import asyncio
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from os.path import commonprefix

import numpy as np

from .asr import FasterWhisperBackend, Result
from .audio import Segmenter, preprocess, rms, save_pair
from .extraction import Extractor
from .models import new_id, now

log = logging.getLogger(__name__)


class RadioService:
    def __init__(self, settings, store, backend=None):
        self.settings, self.store = settings, store
        self.extractor = Extractor(settings.config_dir, settings.radio_mode)
        self.backend = backend or FasterWhisperBackend(settings, self.extractor.prompt)
        self.final_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="final-asr")
        self.partial_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="partial-asr")
        self.lock = threading.RLock()
        self.stop_flag = threading.Event()
        self.manual_recording = threading.Event()
        self.capture_thread = None
        self.pending = 0
        self.generation = 0
        self.partial_token = None
        self.partial_future = None
        self.previous_partial = ""
        self.replay_task = None
        self.live = {"state": "idle", "message": "入力待機", "partial_text": "", "source": "none"}
        # A crashed process must not leave permanent 'processing' records on restart.
        for event in store.snapshot()["events"]:
            if event["status"] in ("processing", "recording"):
                event.update(
                    status="failed", error="前回の処理が中断されました。原音を確認してください"
                )
                store.save_event(event)

    def status(self, **changes):
        with self.lock:
            self.live.update(changes)
            self.live["pending"] = self.pending
            self.store.emit("status", dict(self.live))

    def snapshot(self):
        with self.lock:
            return {**self.store.snapshot(), "live": dict(self.live)}

    def _event(self, source, received_at=None, **metadata):
        return {
            "id": new_id(),
            "received_at": received_at or now(),
            "ended_at": now(),
            "raw_text": "",
            "normalized_text": "",
            "status": "processing",
            "source": source,
            "model": self.settings.final_model,
            "preprocess_profile": self.settings.profile,
            "highlights": [],
            "glossary_hits": [],
            "audio_ref": None,
            "raw_audio_ref": None,
            "avg_logprob": None,
            "no_speech_prob": None,
            "latency_ms": None,
            "corrected_text": None,
            "warnings": [],
            **metadata,
        }

    def finalize(self, event, result, elapsed_ms):
        event.update(
            raw_text=result.text,
            model=result.model,
            avg_logprob=result.avg_logprob,
            no_speech_prob=result.no_speech_prob,
            segments=result.segments,
            latency_ms=elapsed_ms,
        )
        if not result.text.strip() or (
            result.no_speech_prob is not None and result.no_speech_prob > 0.8
        ):
            event.update(
                status="failed", error="有効な発話を認識できませんでした（無音・ノイズ候補）"
            )
            self.store.save_event(event)
            return event
        if result.avg_logprob is not None and result.avg_logprob < -1:
            event["warnings"].append("低い平均log probability：原音を重点確認")
        if re.search(r"(.{2,12})\1{3,}", result.text):
            event["warnings"].append("異常な繰り返し：原音を重点確認")
        if elapsed_ms > 5000:
            event["warnings"].append("表示遅延5秒超過")
        if elapsed_ms > 10000:
            event["warnings"].append("軽量モデルまたは保存済み結果の再生への切替を推奨")
        normalized, highlights, hits = self.extractor.annotate(result.text)
        event.update(
            status="needs_review",
            normalized_text=normalized,
            highlights=highlights,
            glossary_hits=hits,
        )
        # Serialize extraction + persistence so consecutive reports see earlier tasks.
        with self.lock:
            tasks = (
                self.extractor.extract(event, self.store.snapshot()["tasks"])
                if self.settings.auto_extract
                else []
            )
            self.store.save_event(event, tasks)
        return event

    def submit(self, samples, rate, source, received_at=None, **metadata):
        with self.lock:
            if self.pending >= 4:
                self.status(
                    state="failed",
                    message="認識待ちが4件に達しました。入力を停止して確認してください",
                )
                raise ValueError("認識キューが満杯です。処理完了を待ってください")
            event = self._event(
                source, received_at, sample_rate=rate, channels=samples.shape[1], **metadata
            )
            event["audio_ref"] = f"local:{event['id']}.wav"
            event["raw_audio_ref"] = f"local:{event['id']}.raw.wav"
            self.pending += 1
            self.store.save_event(event)
            self.status(
                state="processing",
                message="確定字幕を認識中",
                partial_text="",
                partial_preview="",
                source=source,
            )
            self.final_pool.submit(self._process, event, samples.copy(), rate, time.monotonic())
            return event

    def _process(self, event, samples, rate, started):
        try:
            audio = save_pair(
                self.settings.data_dir / "audio", event["id"], samples, rate, self.settings
            )
            if rms(samples) < self.settings.energy_threshold / 2:
                raise ValueError("無音のため認識を省略しました")
            if len(samples) / rate < self.settings.min_speech_seconds:
                raise ValueError("発話が短すぎるため認識を省略しました")
            if np.max(np.abs(samples)) >= 0.999:
                event["warnings"].append("入力クリッピング：入力レベルを下げてください")
            result = self.backend.transcribe(audio)
            self.finalize(event, result, round((time.monotonic() - started) * 1000))
        except Exception as exc:
            log.exception("Transcription failed: %s", event["id"])
            event.update(
                status="failed",
                error=str(exc),
                latency_ms=round((time.monotonic() - started) * 1000),
            )
            self.store.save_event(event)
        finally:
            with self.lock:
                self.pending -= 1
                failed = event["status"] == "failed"
                state = "processing" if self.pending else "idle"
                if self.capture_thread and self.capture_thread.is_alive() and self.partial_token:
                    state = "recording"
                if self.live["state"] == "disconnected":
                    # A completed inference must not erase an outstanding input disconnect.
                    self.status()
                else:
                    self.status(
                        state="failed" if failed else state,
                        message=event.get(
                            "error", "入力待機" if state == "idle" else "入力・処理継続中"
                        ),
                    )

    @staticmethod
    def devices():
        import sounddevice as sd

        return [
            {
                "id": i,
                "name": d["name"],
                "channels": d["max_input_channels"],
                "sample_rate": d["default_samplerate"],
            }
            for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0
        ]

    def start_capture(self, device, mode):
        with self.lock:
            if self.capture_thread and self.capture_thread.is_alive():
                raise ValueError("音声入力はすでに開始しています")
            if self.replay_task and not self.replay_task.done():
                raise ValueError("台本再生を停止してから入力を開始してください")
            if self.pending:
                raise ValueError("認識完了を待ってから入力を開始してください")
            self.stop_flag.clear()
            self.manual_recording.clear()
            self.generation += 1
            self.capture_thread = threading.Thread(
                target=self._capture, args=(device, mode, self.generation), daemon=True
            )
            self.status(
                state="idle", message="音声デバイスを開いています", source="live_radio", mode=mode
            )
            self.capture_thread.start()

    def _capture(self, device, mode, generation):
        try:
            import sounddevice as sd

            info = sd.query_devices(device, "input")
            rate = int(info["default_samplerate"])
            channels = min(2, int(info["max_input_channels"]))
            segmenter = Segmenter(rate, self.settings, manual=mode == "manual")
            origin = datetime.now().astimezone()
            last_partial = 0
            last_meter = 0
            quiet_since = time.monotonic()
            with sd.InputStream(
                device=device,
                samplerate=rate,
                channels=channels,
                dtype="float32",
                blocksize=round(rate * 0.1),
            ) as stream:
                self.status(
                    state="idle",
                    message="入力待機",
                    device=info["name"],
                    sample_rate=rate,
                    capture_active=True,
                )
                while not self.stop_flag.is_set():
                    samples, overflow = stream.read(round(rate * 0.1))
                    if self.stop_flag.is_set():
                        break
                    if overflow:
                        self.status(message="入力オーバーフロー：音切れの可能性があります")
                    stamp = time.monotonic()
                    level = rms(samples)
                    if level >= self.settings.energy_threshold:
                        quiet_since = stamp
                    chunk = segmenter.feed(samples, self.manual_recording.is_set())
                    if stamp - last_meter >= 1:
                        self.status(
                            level=round(level, 4),
                            clipping=bool(np.max(np.abs(samples)) >= 0.999),
                            silence=stamp - quiet_since >= 3,
                        )
                        last_meter = stamp
                    if chunk is not None:
                        with self.lock:
                            self.partial_token = None
                            self.previous_partial = ""
                        self.submit(
                            chunk.samples,
                            rate,
                            "live_radio",
                            (origin + timedelta(seconds=chunk.start_sample / rate)).isoformat(),
                            ended_at=(
                                origin + timedelta(seconds=chunk.end_sample / rate)
                            ).isoformat(),
                            input_device=info["name"],
                            segmentation=mode,
                        )
                    if segmenter.active:
                        with self.lock:
                            if self.partial_token is None:
                                self.partial_token = new_id()
                                self.status(
                                    state="recording",
                                    message="録音中",
                                    partial_text="",
                                    partial_preview="",
                                )
                        if stamp - last_partial >= self.settings.partial_interval:
                            if self.partial_future is None or self.partial_future.done():
                                self.partial_future = self.partial_pool.submit(
                                    self._partial,
                                    segmenter.window(),
                                    rate,
                                    self.partial_token,
                                    generation,
                                )
                                last_partial = stamp
                # Stop discards the current in-memory utterance (emergency/privacy stop).
        except Exception as exc:
            log.exception("Audio input failed")
            self.status(state="disconnected", message=f"音声入力切断／開始失敗: {exc}")
        finally:
            with self.lock:
                self.partial_token = None
                self.status(partial_text="", capture_active=False)

    def _partial(self, samples, rate, token, generation):
        try:
            result = self.backend.transcribe(preprocess(samples, rate, self.settings), partial=True)
            with self.lock:
                if self.partial_token != token or self.generation != generation:
                    return
                stable = commonprefix([self.previous_partial, result.text])
                self.previous_partial = result.text
                self.status(
                    partial_text=stable,
                    partial_preview=result.text,
                    state="recording",
                    message="録音中・暫定字幕（直近の発話窓）",
                )
        except Exception as exc:
            with self.lock:
                if self.partial_token == token and self.generation == generation:
                    self.status(message=f"暫定字幕失敗（確定認識は継続）: {exc}")

    def manual(self, recording):
        with self.lock:
            if not self.capture_thread or not self.capture_thread.is_alive():
                raise ValueError("先に音声入力を開始してください")
            if self.live.get("mode") != "manual":
                raise ValueError("手動モードで開始してください")
            if recording:
                self.manual_recording.set()
            else:
                self.manual_recording.clear()

    def stop_capture(self):
        self.stop_flag.set()
        self.manual_recording.clear()
        with self.lock:
            self.generation += 1
            self.partial_token = None
        if self.capture_thread:
            self.capture_thread.join(timeout=2)
        self.status(
            state="idle",
            message="入力停止・録音途中の音声は破棄",
            partial_text="",
            partial_preview="",
            capture_active=False,
        )

    async def replay(self, rows, interval):
        try:
            for row in rows:
                self.status(
                    state="recording",
                    source="mock",
                    message="保存済み台本を再生中（推論なし）",
                    partial_text=row["text"][: max(1, len(row["text"]) // 2)],
                )
                await asyncio.sleep(interval / 2)
                self.status(partial_text=row["text"])
                await asyncio.sleep(interval / 2)
                event = self._event("mock", replay_original_at=row.get("received_at"))
                event["preprocess_profile"] = "raw"
                self.finalize(event, Result(row["text"], "saved-demo"), 0)
                self.status(partial_text="")
        finally:
            self.status(state="idle", message="台本再生停止", partial_text="")

    def close(self):
        self.stop_capture()
        self.final_pool.shutdown(wait=True, cancel_futures=False)
        self.partial_pool.shutdown(wait=True, cancel_futures=True)
