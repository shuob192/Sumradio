"""Native-rate capture segmentation; deterministic 16 kHz PCM preprocessing."""

from collections import deque
from dataclasses import dataclass
from math import gcd

import numpy as np
import soundfile as sf
from scipy import signal


def rms(samples):
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64)))) if samples.size else 0.0


def preprocess(samples, rate, settings, profile=None):
    profile = profile or settings.profile
    if profile not in ("raw", "bandpass", "wide-bandpass", "bandpass-denoise"):
        raise ValueError("未知の前処理プロファイル")
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    if not audio.size or not np.isfinite(audio).all():
        raise ValueError("空または不正な音声です")
    divisor = gcd(int(rate), 16000)
    audio = signal.resample_poly(audio, 16000 // divisor, int(rate) // divisor)
    # Raw is the resampling-only baseline; the peak limiter only attenuates over-range input.
    if profile != "raw":
        audio = audio - audio.mean()
        lo, hi = (200, 3800) if profile == "wide-bandpass" else (300, 3400)
        sos = signal.butter(6, [lo, hi], btype="bandpass", fs=16000, output="sos")
        audio = signal.sosfilt(sos, audio)
        if settings.notch_hz:
            b, a = signal.iirnotch(settings.notch_hz, 30, fs=16000)
            audio = signal.lfilter(b, a, audio)
        if profile == "bandpass-denoise" and len(audio) >= 512:
            _, _, spectrum = signal.stft(audio, fs=16000, nperseg=512, noverlap=384)
            magnitude = np.abs(spectrum)
            floor = np.percentile(magnitude, 20, axis=1, keepdims=True)
            gain = np.maximum(0.75, 1 - settings.denoise_strength * floor / (magnitude + 1e-8))
            _, cleaned = signal.istft(spectrum * gain, fs=16000, nperseg=512, noverlap=384)
            audio = cleaned[: len(audio)]
    peak = float(np.max(np.abs(audio)))
    ceiling = 10 ** (-3 / 20)
    if peak > ceiling:
        audio = audio * ceiling / peak
    return audio.astype(np.float32)


def save_pair(directory, identifier, samples, rate, settings):
    directory.mkdir(parents=True, exist_ok=True)
    raw = directory / f"{identifier}.raw.wav"
    processed = directory / f"{identifier}.wav"
    # Preserve native-rate float capture, including channel layout and clipping evidence.
    sf.write(raw, samples, rate, subtype="FLOAT")
    audio = preprocess(samples, rate, settings)
    sf.write(processed, audio, 16000, subtype="PCM_16")
    return audio


@dataclass
class Chunk:
    samples: np.ndarray
    start_sample: int
    end_sample: int


class Segmenter:
    """Feed <=100 ms blocks. Buffers are bounded by max duration plus one input block."""

    def __init__(self, rate, settings, manual=False):
        self.rate = rate
        self.settings = settings
        self.manual = manual
        self.position = 0
        self.pre = deque()
        self.pre_count = 0
        self.active = []
        self.active_count = 0
        self.start_sample = 0
        self.quiet = 0
        self.voiced = 0

    def _remember(self, block):
        self.pre.append(block)
        self.pre_count += len(block)
        keep = int(self.rate * (self.settings.preroll_seconds + self.settings.min_speech_seconds))
        while self.pre and self.pre_count - len(self.pre[0]) >= keep:
            self.pre_count -= len(self.pre.popleft())

    def feed(self, block, recording=False):
        block = np.asarray(block, dtype=np.float32)
        self.position += len(block)
        loud = rms(block) >= self.settings.energy_threshold
        self.voiced = self.voiced + len(block) if loud else 0
        if not self.active:
            self._remember(block)
            triggered = (
                recording
                if self.manual
                else (self.voiced >= round(self.rate * self.settings.min_speech_seconds))
            )
            if not triggered:
                return None
            self.active = list(self.pre)
            self.active_count = self.pre_count
            self.start_sample = self.position - self.active_count
            self.pre.clear()
            self.pre_count = 0
        else:
            self.active.append(block.copy())
            self.active_count += len(block)
        self.quiet = 0 if loud else self.quiet + len(block)
        ended = (
            (not recording)
            if self.manual
            else (self.quiet >= round(self.rate * self.settings.silence_seconds))
        )
        if ended or self.active_count >= round(self.rate * self.settings.max_seconds):
            return self.finish()
        return None

    def window(self):
        if not self.active:
            return None
        return np.concatenate(self.active)[
            -round(self.rate * self.settings.partial_window) :
        ].copy()

    def finish(self):
        if not self.active:
            return None
        chunk = Chunk(np.concatenate(self.active), self.start_sample, self.position)
        self.active = []
        self.active_count = 0
        self.quiet = self.voiced = 0
        return chunk
