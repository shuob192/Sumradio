from __future__ import annotations

from pathlib import Path

import numpy as np


class WhisperUnavailableError(RuntimeError):
    pass


class WhisperTranscriber:
    def __init__(self, model_name: str = "medium", cpu_threads: int = 8, download_root: Path | None = None) -> None:
        self.model_name = model_name
        self.cpu_threads = cpu_threads
        self.download_root = download_root
        self._model = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        try:
            from faster_whisper import WhisperModel
            from faster_whisper.utils import download_model
            from huggingface_hub.errors import LocalEntryNotFoundError

            cache_dir = str(self.download_root) if self.download_root else None
            model_path = self.model_name
            try:
                cached = Path(download_model(self.model_name, cache_dir=cache_dir, local_files_only=True))
            except LocalEntryNotFoundError:
                pass
            else:
                # A snapshot can exist while its large model file is still downloading.
                required = [cached / name for name in ("model.bin", "config.json", "tokenizer.json")]
                if all(path.is_file() and path.stat().st_size > 0 for path in required) and any(cached.glob("vocabulary.*")):
                    model_path = str(cached)

            self._model = WhisperModel(
                model_path,
                device="cpu",
                compute_type="int8",
                cpu_threads=self.cpu_threads,
                num_workers=1,
                download_root=cache_dir,
            )
        except Exception as exc:
            raise WhisperUnavailableError(f"Whisper mediumを読み込めません: {exc}") from exc

    def transcribe_pcm(self, pcm: bytes, *, final: bool) -> str:
        if self._model is None:
            raise WhisperUnavailableError("Whisperモデルが準備できていません")
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = self._model.transcribe(
            audio,
            language="ja",
            task="transcribe",
            # medium + greedy decoding preserved the sample transcript while
            # reducing final latency from 6.7-8.0 s to 4.8-4.9 s on the demo Mac.
            beam_size=1,
            best_of=1,
            condition_on_previous_text=False,
            vad_filter=False,
            temperature=0.0,
        )
        return "".join(segment.text for segment in segments).strip()
