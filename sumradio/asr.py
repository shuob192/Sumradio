from dataclasses import dataclass, field
from threading import Lock
from typing import Protocol

import numpy as np

from .settings import Settings


@dataclass
class Result:
    text: str
    model: str
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    segments: list[dict] = field(default_factory=list)


class ASRBackend(Protocol):
    def transcribe(self, audio: np.ndarray, *, partial: bool = False) -> Result: ...


class FasterWhisperBackend:
    def __init__(self, settings: Settings, prompt=""):
        self.settings = settings
        self.prompt = prompt if settings.use_prompt else None
        self.models = {}
        self.locks = {True: Lock(), False: Lock()}

    def transcribe(self, audio, *, partial=False):
        from faster_whisper import WhisperModel

        name = self.settings.partial_model if partial else self.settings.final_model
        with self.locks[partial]:
            if partial not in self.models:
                self.models[partial] = WhisperModel(
                    name,
                    device=self.settings.device,
                    compute_type="float16" if self.settings.device == "cuda" else "int8",
                    download_root=str(self.settings.model_dir),
                    local_files_only=self.settings.offline,
                )
            segments, _ = self.models[partial].transcribe(
                audio,
                language="ja",
                task="transcribe",
                temperature=0,
                beam_size=1 if partial else 5,
                condition_on_previous_text=False,
                initial_prompt=self.prompt,
                vad_filter=False,
            )
            segments = list(segments)  # faster-whisper executes lazily.
        weights = [max(s.end - s.start, 0.001) for s in segments]
        return Result(
            text="".join(s.text for s in segments).strip(),
            model=name,
            avg_logprob=float(np.average([s.avg_logprob for s in segments], weights=weights))
            if segments
            else None,
            no_speech_prob=float(np.average([s.no_speech_prob for s in segments], weights=weights))
            if segments
            else None,
            segments=[{"start": s.start, "end": s.end, "text": s.text} for s in segments],
        )
