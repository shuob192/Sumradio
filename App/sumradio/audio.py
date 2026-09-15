"""Native microphone only. Test doubles are injected by tests, never exposed as input APIs."""

import asyncio
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from math import gcd

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from .config import Settings
from .models import Communication


@dataclass
class Segmenter:
    rate: int
    threshold: float
    silence_seconds: float = 5
    pre_roll_seconds: float = 0.5
    manual: bool = False

    def __post_init__(self):
        self.active = False
        self.quiet = 0
        self.pre: deque = deque()
        self.pre_samples = 0

    def feed(self, frames: np.ndarray) -> tuple[bool, list[np.ndarray], bool]:
        voice = float(np.sqrt(np.mean(frames.astype(np.float64) ** 2))) >= self.threshold
        started = False
        if not self.active:
            self.pre.append(frames)
            self.pre_samples += len(frames)
            while (
                self.pre and self.pre_samples - len(self.pre[0]) > self.rate * self.pre_roll_seconds
            ):
                self.pre_samples -= len(self.pre.popleft())
            if not voice and not self.manual:
                return False, [], False
            self.active = True
            started = True
            pieces = list(self.pre)
            self.pre.clear()
            self.pre_samples = 0
        else:
            pieces = [frames]
        self.quiet = 0 if voice else self.quiet + len(frames)
        ended = not self.manual and self.quiet >= self.rate * self.silence_seconds
        if ended:
            self.active = False
            self.quiet = 0
        return started, pieces, ended


class WhisperEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = None
        self.state = "loading"
        self.error: str | None = None

    def load(self):
        try:
            from faster_whisper import WhisperModel

            self.model = WhisperModel(
                self.settings.whisper_model,
                device="cpu",
                compute_type=self.settings.whisper_compute,
                cpu_threads=self.settings.whisper_threads,
                # Two callers share weights: a running partial must not block final recognition.
                num_workers=2,
                download_root=str(self.settings.model_dir or self.settings.data_dir / "models"),
                local_files_only=True,
            )
            self.state, self.error = "ready", None
        except Exception as exc:
            self.state, self.error = "unavailable", f"モデルを準備してください: {exc}"

    def transcribe(self, data: np.ndarray, rate: int) -> str:
        if self.model is None:
            raise RuntimeError(self.error or "Whisperモデルが未準備です")
        if data.ndim > 1:
            data = data.mean(axis=1)
        if rate != 16000:
            divisor = gcd(rate, 16000)
            data = resample_poly(data, 16000 // divisor, rate // divisor).astype(np.float32)
        segments, _ = self.model.transcribe(
            data,
            language="ja",
            beam_size=self.settings.whisper_beam,
            vad_filter=False,
            condition_on_previous_text=True,
        )
        # Do not pass initial_prompt, phonetics, location names or scripts to Whisper.
        return "".join(segment.text for segment in segments).strip()


class Microphone:
    def __init__(self, settings: Settings, store, publish, on_final):
        self.settings, self.store, self.publish, self.on_final = settings, store, publish, on_final
        self.stream = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.frames: queue.Queue = queue.Queue(maxsize=1000)
        self.run_id: str | None = None
        self.comm_id: str | None = None
        self.error: str | None = None
        self.latest: tuple | None = None
        self.level = 0.0
        self.loop = None
        self.rate = 48000
        self.stop_lock = asyncio.Lock()

    def devices(self):
        import sounddevice as sd

        return [
            {"id": i, "name": d["name"], "sample_rate": d["default_samplerate"]}
            for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0
        ]

    def start(self, run_id: str, device: int | None, manual: bool):
        import sounddevice as sd

        if self.run_id:
            raise ValueError("既に録音中です")
        info = sd.query_devices(device, "input")
        self.rate = int(info["default_samplerate"])
        self.frames = queue.Queue(maxsize=1000)
        self.stop_event.clear()
        self.error = None
        self.loop = asyncio.get_running_loop()
        self.run_id = run_id
        try:
            self.stream = sd.InputStream(
                device=device,
                samplerate=self.rate,
                channels=1,
                dtype="float32",
                blocksize=int(self.rate * 0.02),
                callback=self._callback,
            )
            self.thread = threading.Thread(target=self._consume, args=(run_id, manual), daemon=True)
            self.thread.start()
            self.stream.start()
        except Exception:
            self.stop_event.set()
            if self.thread:
                self.thread.join(timeout=3)
            if self.stream:
                self.stream.close()
            self.run_id, self.stream = None, None
            raise

    def _callback(self, indata, frames, time_info, status):
        if status:
            self.error = f"音声入力で欠落を検出: {status}"
        try:
            self.frames.put_nowait((indata.copy(), time.time()))
        except queue.Full:
            self.error = "音声の保存が追いつかず録音を停止しました"
            self.stop_event.set()

    def emit(self, event: dict):
        self.loop.call_soon_threadsafe(self.publish, event)

    def _consume(self, run_id: str, manual: bool):
        segmenter = Segmenter(
            self.rate,
            self.settings.voice_threshold,
            self.settings.silence_seconds,
            self.settings.pre_roll_seconds,
            manual,
        )
        recording = None
        comm = None
        rolling: deque = deque()
        rolling_samples = 0
        last_partial, last_level, last_voice = 0.0, 0.0, 0.0

        def finish(reason: str):
            nonlocal recording, comm, rolling_samples
            if recording is None:
                return
            recording.flush()
            recording.close()
            finalized = time.time()
            captured_id = comm.id

            def update(run):
                c = run.communications[captured_id]
                c.transcription_status = "queued"
                c.version += 1
                c.metrics.update(
                    {
                        "segmentation": reason,
                        "finalized_at_epoch": finalized,
                        "speech_ended_at_epoch": last_voice or finalized,
                    }
                )
                if self.error:
                    c.error = self.error
                    c.metrics["audio_warning"] = self.error

            run = self.store.change(run_id, update)
            self.emit({"type": "state", "run_id": run_id, "version": run.version})
            self.loop.call_soon_threadsafe(self.on_final, run_id, captured_id)
            recording, comm, self.comm_id, self.latest = None, None, None, None
            rolling.clear()
            rolling_samples = 0

        try:
            while not self.stop_event.is_set() or not self.frames.empty():
                try:
                    frames, captured_at = self.frames.get(timeout=0.05)
                except queue.Empty:
                    continue
                self.level = float(np.sqrt(np.mean(frames**2)))
                if self.level >= self.settings.voice_threshold:
                    last_voice = captured_at
                if captured_at - last_level >= 0.15:
                    self.emit({"type": "level", "run_id": run_id, "level": self.level})
                    last_level = captured_at
                started, pieces, ended = segmenter.feed(frames)
                if started:
                    comm = Communication(run_id=run_id, transcription_status="recording")
                    comm.original_audio = f"{comm.id}-original.wav"
                    comm.recognition_audio = f"{comm.id}-16k.wav"
                    comm.received_at = datetime.fromtimestamp(
                        captured_at - self.settings.pre_roll_seconds, timezone.utc
                    ).isoformat()
                    directory = self.store.run_dir(run_id) / "audio"
                    directory.mkdir(parents=True, exist_ok=True)
                    recording = sf.SoundFile(
                        directory / comm.original_audio,
                        mode="w",
                        samplerate=self.rate,
                        channels=1,
                        subtype="PCM_16",
                    )
                    run = self.store.change(
                        run_id, lambda r: r.communications.__setitem__(comm.id, comm)
                    )
                    self.comm_id = comm.id
                    self.emit({"type": "state", "run_id": run_id, "version": run.version})
                for piece in pieces:
                    recording.write(piece)
                    rolling.append(piece)
                    rolling_samples += len(piece)
                while (
                    rolling
                    and rolling_samples - len(rolling[0])
                    >= self.settings.interim_window * self.rate
                ):
                    rolling_samples -= len(rolling.popleft())
                if recording and captured_at - last_partial >= self.settings.interim_seconds:
                    recording.flush()
                    self.latest = (
                        run_id,
                        comm.id,
                        np.concatenate(rolling).copy(),
                        self.rate,
                        captured_at,
                    )
                    last_partial = captured_at
                if ended:
                    finish("automatic")
            finish("manual" if not self.error else "interrupted")
        except Exception as exc:
            self.error = str(exc)
            self.stop_event.set()
            if recording:
                recording.close()
            self.emit({"type": "audio_error", "run_id": run_id, "error": self.error})
        finally:
            self.latest = None
            if self.stop_event.is_set() and self.error:
                self.loop.call_soon_threadsafe(lambda: asyncio.create_task(self.stop_failed()))

    async def stop_failed(self):
        await self.stop()
        self.publish({"type": "health"})

    async def stop(self):
        async with self.stop_lock:
            if self.stream:
                await asyncio.to_thread(self.stream.stop)
                self.stream.close()
            self.stop_event.set()
            if self.thread:
                await asyncio.to_thread(self.thread.join)
            self.stream, self.thread, self.run_id, self.comm_id = None, None, None, None


class SpeechWorker:
    def __init__(self, settings, store, mic, publish, on_transcript):
        self.settings, self.store, self.mic = settings, store, mic
        self.publish, self.on_transcript = publish, on_transcript
        self.engine = WhisperEngine(settings)
        self.finals: asyncio.Queue = asyncio.Queue()
        self.final_active = False

    def enqueue(self, run_id: str, comm_id: str):
        self.finals.put_nowait((run_id, comm_id))

    async def run(self):
        await asyncio.to_thread(self.engine.load)
        self.publish({"type": "health"})
        async with asyncio.TaskGroup() as group:
            group.create_task(self._final_loop())
            group.create_task(self._partial_loop())

    async def _final_loop(self):
        while True:
            run_id, comm_id = await self.finals.get()
            self.final_active = True
            try:
                self.set_status(run_id, comm_id, "running")
                comm = self.store.get(run_id).communications[comm_id]
                prepared = time.perf_counter()
                data, rate = await asyncio.to_thread(
                    sf.read,
                    self.store.run_dir(run_id) / "audio" / comm.original_audio,
                    dtype="float32",
                )
                if data.ndim > 1:
                    data = data.mean(axis=1)
                divisor = gcd(rate, 16000)
                converted = await asyncio.to_thread(
                    resample_poly, data, 16000 // divisor, rate // divisor
                )
                await asyncio.to_thread(
                    sf.write,
                    self.store.run_dir(run_id) / "audio" / comm.recognition_audio,
                    converted,
                    16000,
                    subtype="PCM_16",
                )
                preparation_ms = (time.perf_counter() - prepared) * 1000
                self.store.change(
                    run_id,
                    lambda r: r.communications[comm_id].metrics.update(
                        {"speech_preparation_ms": preparation_ms}
                    ),
                )
                started = time.perf_counter()
                text = await asyncio.to_thread(
                    self.engine.transcribe, converted.astype(np.float32), 16000
                )
                self.on_transcript(run_id, comm_id, text, time.perf_counter() - started)
            except Exception as exc:
                self.set_status(run_id, comm_id, "failed", str(exc))
            finally:
                self.final_active = False
                self.finals.task_done()

    async def _partial_loop(self):
        while True:
            # Do not start more partial work while any final is queued or running.
            # One already-running partial may finish independently; its late result is discarded.
            if (
                not self.final_active
                and self.finals.empty()
                and self.mic.latest
                and self.engine.state == "ready"
            ):
                partial = self.mic.latest
                self.mic.latest = None
                run_id, comm_id, data, rate, captured_at = partial
                try:
                    text = await asyncio.to_thread(self.engine.transcribe, data, rate)
                    # A result that completed after the utterance ended is never shown as live.
                    if self.mic.run_id == run_id and self.mic.comm_id == comm_id:
                        self.publish(
                            {
                                "type": "subtitle",
                                "run_id": run_id,
                                "communication_id": comm_id,
                                "text": text,
                                "captured_at": captured_at,
                                "latency_ms": (time.time() - captured_at) * 1000,
                            }
                        )
                except Exception as exc:
                    self.publish({"type": "audio_error", "run_id": run_id, "error": str(exc)})
            else:
                await asyncio.sleep(0.05)

    def set_status(self, run_id, comm_id, status, error=None):
        def update(run):
            comm = run.communications[comm_id]
            comm.transcription_status, comm.error = status, error
            comm.version += 1
            if status == "running":
                started = time.time()
                comm.metrics.update(
                    {
                        "speech_started_at_epoch": started,
                        "speech_queue_wait_ms": max(
                            0, started - float(comm.metrics.get("finalized_at_epoch", started))
                        )
                        * 1000,
                    }
                )

        run = self.store.change(run_id, update)
        self.publish({"type": "state", "run_id": run_id, "version": run.version})
