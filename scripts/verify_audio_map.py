"""Exercise the real Whisper → Codex → task → local map pipeline with training WAV."""
from __future__ import annotations

import argparse
import asyncio
import json
import wave
from dataclasses import replace
from datetime import UTC, datetime
from math import gcd
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

from sumradio.audio import CapturedSegment
from sumradio.config import Settings
from sumradio.geography_models import YOKOHAMA_AREA
from sumradio.service import SumradioService


async def run(path: Path) -> int:
    with wave.open(str(path), "rb") as audio:
        if audio.getnchannels() != 1 or audio.getsampwidth() != 2:
            raise ValueError("16-bit mono PCM WAVを指定してください")
        sample_rate = audio.getframerate()
        original = audio.readframes(audio.getnframes())
    if not original:
        raise ValueError("音声サンプルのないWAVです。音声ファイルの生成結果を確認してください")
    divisor = gcd(sample_rate, 16000)
    samples = np.frombuffer(original, dtype=np.int16).astype(np.float64)
    recognition = np.clip(np.rint(resample_poly(samples, 16000 // divisor, sample_rate // divisor)), -32768, 32767).astype(np.int16).tobytes()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    settings = Settings.from_env()
    output = settings.data_dir / "verification" / f"audio-map-{stamp}"
    service = SumradioService(replace(settings, data_dir=output, skip_model_load=False, geocoder_enabled=False, refresh_places=False))
    await service.start()
    try:
        await service._model_task
        if not service.transcriber.loaded:
            raise RuntimeError(service.whisper_error)
        area = await service.set_area(YOKOHAMA_AREA)
        now = datetime.now(UTC).isoformat()
        await service._process_segment(CapturedSegment(original, sample_rate, recognition, now, now), area)
        await asyncio.wait_for(service._extraction_queue.join(), 120)
        if service._location_tasks:
            await asyncio.wait_for(asyncio.gather(*service._location_tasks.values()), 30)
        state = service.public_state()
        report = {
            "source": "training WAV (no microphone capture)", "audio_path": str(path),
            "audio_seconds": len(original) / 2 / sample_rate,
            "ok": bool(state["tasks"]) and all(task["position"]["selected"] for task in state["tasks"]),
            **state,
        }
        report_path = output / "report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": report["ok"], "report": str(report_path),
            "transcripts": [record["transcript_current"] for record in state["communications"]],
            "places": [task["position"]["selected"] for task in state["tasks"]]}, ensure_ascii=False))
        return 0 if report["ok"] else 1
    finally:
        await service.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="訓練用WAVをWhisperで認識し、文字だけを実Codexへ送り、横浜地点の照合を検証")
    parser.add_argument("audio", type=Path)
    raise SystemExit(asyncio.run(run(parser.parse_args().audio.resolve())))
