"""Internal saved-audio benchmark, never microphone acceptance or an application input API."""

import argparse
import hashlib
import importlib.metadata
import platform
import time
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from faster_whisper import WhisperModel
from scipy.signal import resample_poly

from sumradio.config import Settings
from sumradio.models import now
from sumradio.storage import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--threads", default="8")
    parser.add_argument("--rounds", type=int, default=2)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("既存の測定を保持するため、新しい出力名を指定してください")
    if args.rounds < 1:
        parser.error("roundsは1以上を指定してください")
    settings = Settings()
    data, rate = sf.read(args.audio, dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    divisor = gcd(rate, 16000)
    audio = resample_poly(data, 16000 // divisor, rate // divisor).astype(np.float32)
    report = {
        "mode": "saved_microphone_audio_internal_benchmark_not_live_acceptance",
        "at": now(),
        "audio_name": args.audio.name,
        "audio_sha256": hashlib.sha256(args.audio.read_bytes()).hexdigest(),
        "duration_seconds": len(data) / rate,
        "original_rate": rate,
        "platform": platform.platform(),
        "versions": {
            name: importlib.metadata.version(name) for name in ["faster-whisper", "ctranslate2"]
        },
        "model": settings.whisper_model,
        "compute": settings.whisper_compute,
        "beam": settings.whisper_beam,
        "limitations": "録音・自動区切り・キュー待ち・画面表示・クラウド抽出を含まない。台本や語彙の補助文は与えない。認識内容と時間の比較用で、精度の正解判定は別途行う。",
        "results": [],
    }
    for threads in [int(value) for value in args.threads.split(",")]:
        model = WhisperModel(
            settings.whisper_model,
            device="cpu",
            compute_type=settings.whisper_compute,
            cpu_threads=threads,
            download_root=str(settings.model_dir or settings.data_dir / "models"),
            local_files_only=True,
        )
        for round_index in range(args.rounds):
            # Alternate ordering to expose warm-up/order effects instead of hiding them.
            order = [False, True] if round_index % 2 == 0 else [True, False]
            for without_timestamps in order:
                started = time.perf_counter()
                segments, _ = model.transcribe(
                    audio,
                    language="ja",
                    beam_size=settings.whisper_beam,
                    vad_filter=False,
                    condition_on_previous_text=True,
                    without_timestamps=without_timestamps,
                )
                segments = list(segments)
                result = {
                    "threads": threads,
                    "round": round_index + 1,
                    "without_timestamps": without_timestamps,
                    "seconds": round(time.perf_counter() - started, 4),
                    "text": "".join(segment.text for segment in segments).strip(),
                    "segments": [
                        {
                            "start": segment.start,
                            "end": segment.end,
                            "text": segment.text,
                            "temperature": segment.temperature,
                            "avg_logprob": segment.avg_logprob,
                        }
                        for segment in segments
                    ],
                }
                report["results"].append(result)
                atomic_json(args.output, report)
                print(
                    f"threads={threads} round={round_index + 1} "
                    f"without_timestamps={without_timestamps}: {result['seconds']}s "
                    f"{result['text']}",
                    flush=True,
                )
        del model


if __name__ == "__main__":
    main()
