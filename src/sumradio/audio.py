from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

import numpy as np
from scipy.signal import resample_poly


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class CapturedSegment:
    original_pcm: bytes
    original_sample_rate: int
    recognition_pcm: bytes
    started_at: str
    ended_at: str


@dataclass(slots=True)
class FeedResult:
    segment: CapturedSegment | None = None
    partial_pcm: bytes | None = None


class AudioSegmenter:
    """Accumulates 30 ms frames and finalizes after five seconds of silence."""

    def __init__(
        self,
        *,
        frame_ms: int = 30,
        silence_ms: int = 5000,
        preroll_ms: int = 510,
        partial_ms: int = 2000,
        minimum_voice_ms: int = 240,
    ):
        self.frame_ms = frame_ms
        self.silence_ms = silence_ms
        self.partial_ms = partial_ms
        self.minimum_voice_ms = minimum_voice_ms
        self._preroll_frames = max(1, preroll_ms // frame_ms)
        self._preroll: deque[tuple[bytes, bytes]] = deque(maxlen=self._preroll_frames)
        self._raw_frames: list[bytes] = []
        self._recognition_frames: list[bytes] = []
        self._active = False
        self._silence_elapsed = 0
        self._partial_elapsed = 0
        self._voiced_elapsed = 0
        self._started_at: str | None = None
        self._original_sample_rate = 0

    @property
    def active(self) -> bool:
        return self._active

    def feed(
        self,
        *,
        original_frame: bytes,
        recognition_frame: bytes,
        original_sample_rate: int,
        voiced: bool,
        timestamp: str | None = None,
    ) -> FeedResult:
        timestamp = timestamp or _now()
        if not self._active:
            self._preroll.append((original_frame, recognition_frame))
            if not voiced:
                return FeedResult()
            self._active = True
            self._started_at = timestamp
            self._original_sample_rate = original_sample_rate
            self._raw_frames = [pair[0] for pair in self._preroll]
            self._recognition_frames = [pair[1] for pair in self._preroll]
            self._preroll.clear()
            self._silence_elapsed = 0
            self._partial_elapsed = 0
        else:
            self._raw_frames.append(original_frame)
            self._recognition_frames.append(recognition_frame)

        self._silence_elapsed = 0 if voiced else self._silence_elapsed + self.frame_ms
        if voiced:
            self._voiced_elapsed += self.frame_ms
        self._partial_elapsed += self.frame_ms
        partial = None
        if self._partial_elapsed >= self.partial_ms:
            partial = b"".join(self._recognition_frames)
            self._partial_elapsed = 0
        if self._silence_elapsed >= self.silence_ms:
            return FeedResult(segment=self.finalize(timestamp=timestamp), partial_pcm=partial)
        return FeedResult(partial_pcm=partial)

    def finalize(self, *, timestamp: str | None = None) -> CapturedSegment | None:
        if not self._active or not self._recognition_frames:
            self._reset_active()
            return None
        if self._voiced_elapsed < self.minimum_voice_ms:
            self._reset_active()
            return None
        segment = CapturedSegment(
            original_pcm=b"".join(self._raw_frames),
            original_sample_rate=self._original_sample_rate,
            recognition_pcm=b"".join(self._recognition_frames),
            started_at=self._started_at or _now(),
            ended_at=timestamp or _now(),
        )
        self._reset_active()
        return segment

    def _reset_active(self) -> None:
        self._active = False
        self._raw_frames = []
        self._recognition_frames = []
        self._silence_elapsed = 0
        self._partial_elapsed = 0
        self._voiced_elapsed = 0
        self._started_at = None
        self._original_sample_rate = 0


class AudioRecorder:
    def __init__(
        self,
        *,
        on_segment: Callable[[CapturedSegment], None],
        on_partial: Callable[[bytes], None],
        on_level: Callable[[float], None],
        on_error: Callable[[str], None],
        voice_rms_threshold: float = 300.0,
    ) -> None:
        self.on_segment = on_segment
        self.on_partial = on_partial
        self.on_level = on_level
        self.on_error = on_error
        self.voice_rms_threshold = voice_rms_threshold
        self.segmenter = AudioSegmenter()
        self._stream = None
        self._vad = None
        self._sample_rate = 0
        self._lock = threading.RLock()

    @property
    def recording(self) -> bool:
        return self._stream is not None

    @staticmethod
    def devices() -> list[dict]:
        try:
            import sounddevice as sd

            devices = []
            try:
                default_input = int(sd.default.device[0])
            except (TypeError, IndexError):
                default_input = int(sd.default.device)
            for index, device in enumerate(sd.query_devices()):
                if int(device["max_input_channels"]) > 0:
                    devices.append(
                        {
                            "id": index,
                            "name": str(device["name"]),
                            "channels": int(device["max_input_channels"]),
                            "default_sample_rate": int(round(float(device["default_samplerate"]))),
                            "is_default": index == default_input,
                        }
                    )
            return devices
        except Exception as exc:
            raise RuntimeError(f"マイク一覧を取得できません: {exc}") from exc

    def start(self, device_id: int | None = None) -> dict:
        with self._lock:
            if self._stream is not None:
                raise RuntimeError("すでに録音中です")
            try:
                import sounddevice as sd
                import webrtcvad

                device = sd.query_devices(device_id, "input")
                self._sample_rate = int(round(float(device["default_samplerate"])))
                if self._sample_rate <= 0:
                    raise RuntimeError("マイクのサンプルレートを取得できません")
                blocksize = max(1, round(self._sample_rate * 0.03))
                self._vad = webrtcvad.Vad(2)
                self.segmenter = AudioSegmenter()
                self._stream = sd.InputStream(
                    samplerate=self._sample_rate,
                    blocksize=blocksize,
                    device=device_id,
                    channels=1,
                    dtype="int16",
                    callback=self._callback,
                )
                self._stream.start()
                return {
                    "device_id": device_id,
                    "device_name": str(device["name"]),
                    "sample_rate": self._sample_rate,
                }
            except Exception:
                self._stream = None
                self._vad = None
                raise

    def finalize_current(self) -> bool:
        with self._lock:
            segment = self.segmenter.finalize()
        if segment:
            self.on_segment(segment)
            return True
        return False

    def stop(self) -> bool:
        with self._lock:
            stream = self._stream
            self._stream = None
            if stream is not None:
                stream.stop()
                stream.close()
            segment = self.segmenter.finalize()
            self._vad = None
        if segment:
            self.on_segment(segment)
        return stream is not None

    def _callback(self, input_data, frames: int, timing, status) -> None:  # sounddevice callback signature
        del frames, timing
        try:
            if status:
                self.on_error(str(status))
            samples = np.asarray(input_data[:, 0], dtype=np.int16)
            rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
            level = min(1.0, rms / 12000.0)
            self.on_level(level)
            recognition = resample_poly(samples.astype(np.float32), 16000, self._sample_rate)
            recognition = np.clip(np.rint(recognition), -32768, 32767).astype(np.int16)
            expected = 480
            if len(recognition) < expected:
                recognition = np.pad(recognition, (0, expected - len(recognition)))
            elif len(recognition) > expected:
                recognition = recognition[:expected]
            recognition_bytes = recognition.tobytes()
            voiced = bool(
                self._vad
                and rms >= self.voice_rms_threshold
                and self._vad.is_speech(recognition_bytes, 16000)
            )
            result = self.segmenter.feed(
                original_frame=samples.tobytes(),
                recognition_frame=recognition_bytes,
                original_sample_rate=self._sample_rate,
                voiced=voiced,
            )
            if result.partial_pcm:
                self.on_partial(result.partial_pcm)
            if result.segment:
                self.on_segment(result.segment)
        except Exception as exc:
            self.on_error(f"音声処理エラー: {exc}")
