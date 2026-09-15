from __future__ import annotations

import sys
from pathlib import Path

import pytest

from sumradio.config import Settings


@pytest.fixture
def codex_factory(tmp_path: Path):
    def create(body: str, name: str = "codex-test") -> Path:
        path = tmp_path / name
        path.write_text(
            f"#!{sys.executable}\n"
            "import json, sys\n"
            "if '--version' in sys.argv:\n"
            "    print('codex-cli test'); sys.exit(0)\n"
            "if '--help' in sys.argv:\n"
            "    print('Usage: codex exec [OPTIONS]'); sys.exit(0)\n"
            "prompt = sys.stdin.read()\n"
            + body + "\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path
    return create


@pytest.fixture
def settings(tmp_path: Path, codex_factory) -> Settings:
    root = Path(__file__).resolve().parents[1]
    return Settings(
        project_root=root,
        data_dir=tmp_path / "data",
        model_cache_dir=tmp_path / "models",
        japanese_phonetic_path=root / "Document_recent" / "japanese_phonetic.md",
        nato_phonetic_path=root / "Document_recent" / "nato_phonetic.md",
        codex_path=str(codex_factory("sys.exit('No extraction response configured for this test')")),
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
        geocoder_enabled=False,
        refresh_places=False,
    )
