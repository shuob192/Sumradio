from __future__ import annotations

from pathlib import Path

import pytest

from sumradio.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    root = Path(__file__).resolve().parents[1]
    return Settings(
        project_root=root,
        data_dir=tmp_path / "data",
        model_cache_dir=tmp_path / "models",
        japanese_phonetic_path=root / "Document" / "japanese_phonetic.md",
        nato_phonetic_path=root / "Document" / "nato_phonetic.md",
        codex_path="codex",
        codex_model="gpt-5.6-luna",
        codex_effort="low",
        codex_timeout_seconds=2,
        codex_output_limit_bytes=1_048_576,
        whisper_model="medium",
        whisper_cpu_threads=2,
        voice_rms_threshold=300,
        skip_model_load=True,
        host="127.0.0.1",
        port=8000,
    )
