import os
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseModel):
    data_dir: Path = Field(
        default_factory=lambda: Path(
            os.environ.get(
                "SUMRADIO_DATA_DIR", str(Path.home() / "Library/Application Support/Sumradio")
            )
        )
    )
    resources: Path = ROOT
    codex_bin: str = Field(
        default_factory=lambda: (
            os.getenv("SUMRADIO_CODEX_BIN")
            or shutil.which("codex")
            or (
                "/Applications/ChatGPT.app/Contents/Resources/codex"
                if Path("/Applications/ChatGPT.app/Contents/Resources/codex").is_file()
                else "codex"
            )
        )
    )
    codex_model: str = Field(
        default_factory=lambda: os.getenv("SUMRADIO_CODEX_MODEL", "gpt-5.6-luna")
    )
    codex_timeout: float = 60
    output_limit: int = 1024 * 1024
    whisper_model: str = "medium"
    model_dir: Path | None = Field(
        default_factory=lambda: (
            Path(os.environ["SUMRADIO_MODEL_DIR"]) if os.getenv("SUMRADIO_MODEL_DIR") else None
        )
    )
    whisper_compute: str = Field(
        default_factory=lambda: os.getenv("SUMRADIO_WHISPER_COMPUTE", "int8")
    )
    whisper_threads: int = Field(
        default_factory=lambda: int(os.getenv("SUMRADIO_WHISPER_THREADS", "4")), ge=1
    )
    whisper_beam: int = Field(default_factory=lambda: int(os.getenv("SUMRADIO_WHISPER_BEAM", "1")))
    silence_seconds: float = 5
    pre_roll_seconds: float = 0.5
    interim_seconds: float = 1.5
    interim_window: float = 20
    # Adjustable sound-level gate; a visible level meter helps match the receiving radio.
    voice_threshold: float = Field(
        default_factory=lambda: float(os.getenv("SUMRADIO_VOICE_THRESHOLD", "0.012"))
    )
    geocoder_url: str = Field(default_factory=lambda: os.getenv("SUMRADIO_GEOCODER_URL", ""))
    geocoder_agent: str = Field(default_factory=lambda: os.getenv("SUMRADIO_GEOCODER_AGENT", ""))
    tile_url: str = Field(
        default_factory=lambda: os.getenv(
            "SUMRADIO_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        )
    )
    port: int = Field(default_factory=lambda: int(os.getenv("SUMRADIO_PORT", "8765")))
