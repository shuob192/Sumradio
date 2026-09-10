from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE = Path(__file__).parent
Profile = Literal["raw", "bandpass", "wide-bandpass", "bandpass-denoise"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SUMRADIO_", env_file=".env", extra="ignore")
    data_dir: Path = Path("data")
    model_dir: Path = Path("models")
    config_dir: Path = PACKAGE / "config"
    device: Literal["cpu", "cuda"] = "cpu"
    final_model: str = "kotoba-tech/kotoba-whisper-v2.0-faster"
    partial_model: str = "small"
    offline: bool = True
    profile: Profile = "raw"
    silence_seconds: float = Field(1.0, ge=0.8, le=1.2)
    energy_threshold: float = Field(0.012, gt=0, lt=1)
    min_speech_seconds: float = Field(0.2, ge=0.1, le=1)
    preroll_seconds: float = Field(0.3, ge=0, le=1)
    max_seconds: float = Field(30, ge=2, le=30)
    partial_interval: float = Field(0.75, ge=0.5, le=1)
    partial_window: float = Field(6, ge=5, le=8)
    denoise_strength: float = Field(0.15, ge=0, le=0.5)
    notch_hz: Literal[0, 50, 60] = 0
    radio_mode: bool = True
    use_prompt: bool = True
    auto_extract: bool = True
