from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    data_dir: Path
    model_cache_dir: Path
    japanese_phonetic_path: Path
    nato_phonetic_path: Path
    codex_path: str
    codex_model: str
    codex_effort: str
    codex_timeout_seconds: float
    codex_output_limit_bytes: int
    whisper_model: str
    whisper_cpu_threads: int
    voice_rms_threshold: float
    skip_model_load: bool
    host: str
    port: int

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> "Settings":
        root = (project_root or Path(__file__).resolve().parents[2]).resolve()
        configured_codex = os.getenv("SUMRADIO_CODEX_PATH")
        codex_path = configured_codex or shutil.which("codex") or "codex"
        return cls(
            project_root=root,
            data_dir=Path(os.getenv("SUMRADIO_DATA_DIR", root / "data")).resolve(),
            model_cache_dir=Path(
                os.getenv("SUMRADIO_MODEL_DIR", Path(os.getenv("SUMRADIO_DATA_DIR", root / "data")) / "models")
            ).resolve(),
            japanese_phonetic_path=root / "Document" / "japanese_phonetic.md",
            nato_phonetic_path=root / "Document" / "nato_phonetic.md",
            codex_path=codex_path,
            codex_model=os.getenv("SUMRADIO_CODEX_MODEL", "gpt-5.6-luna"),
            codex_effort=os.getenv("SUMRADIO_CODEX_EFFORT", "low"),
            codex_timeout_seconds=float(os.getenv("SUMRADIO_CODEX_TIMEOUT_SECONDS", "60")),
            codex_output_limit_bytes=int(os.getenv("SUMRADIO_CODEX_OUTPUT_LIMIT_BYTES", "1048576")),
            whisper_model="medium",
            whisper_cpu_threads=int(os.getenv("SUMRADIO_WHISPER_CPU_THREADS", "8")),
            voice_rms_threshold=float(os.getenv("SUMRADIO_VOICE_RMS_THRESHOLD", "300")),
            skip_model_load=os.getenv("SUMRADIO_SKIP_MODEL_LOAD", "0") == "1",
            host=os.getenv("SUMRADIO_HOST", "127.0.0.1"),
            port=int(os.getenv("SUMRADIO_PORT", "8000")),
        )
