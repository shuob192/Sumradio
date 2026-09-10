"""Offline benchmark. Reports measured inference latency, never simulated E2E latency."""

import json
import platform
import re
import time
import tracemalloc
import unicodedata
from pathlib import Path

import numpy as np
import soundfile as sf

from .asr import FasterWhisperBackend
from .audio import preprocess
from .extraction import Extractor
from .models import now


def distance(left, right):
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def clean(text):
    return re.sub(r"[\s、。，,.!?！？]", "", unicodedata.normalize("NFKC", text))


def metrics(reference, prediction, slots):
    reference, prediction = clean(reference), clean(prediction)
    return {
        "char_errors": distance(reference, prediction),
        "reference_chars": len(reference),
        "cer": distance(reference, prediction) / max(1, len(reference)),
        "slot_correct": sum(clean(s) in prediction for s in slots),
        "slot_total": len(slots),
        "false_transcription_on_silence": not reference and bool(prediction),
    }


def evaluate_manifest(args, settings):
    manifest = Path(args.manifest).resolve()
    rows = json.loads(manifest.read_text(encoding="utf-8"))
    if not rows:
        raise ValueError("評価セットが空です")
    # Fail before loading a multi-GB model if recordings have not been collected yet.
    for row in rows:
        if not row.get("audio") or not (manifest.parent / row["audio"]).is_file():
            raise ValueError(f"評価音声がありません: {row.get('id', row.get('audio'))}")
    results = []
    aids = [False, True] if args.prompt == "both" else [args.prompt == "on"]
    report = {
        "created_at": now(),
        "platform": platform.platform(),
        "device": settings.device,
        "compute_type": "float16" if settings.device == "cuda" else "int8",
        "latency_scope": "WAV preprocessing + inference; first inference includes cold model load",
        "e2e_latency": None,
        "segmentation_success_rate": None,
        "memory_scope": "Python tracemalloc only; excludes native CTranslate2 and GPU allocations",
        "runs": results,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    for model in args.models or [settings.final_model]:
        for profile in args.profiles:
            for aid in aids:
                config = settings.model_copy(
                    update={"final_model": model, "profile": profile, "use_prompt": aid}
                )
                backend = FasterWhisperBackend(config, Extractor(config.config_dir).prompt)
                samples = []
                tracemalloc.start()
                for row in rows:
                    audio, rate = sf.read(
                        manifest.parent / row["audio"], dtype="float32", always_2d=True
                    )
                    started = time.monotonic()
                    result = backend.transcribe(preprocess(audio, rate, config))
                    latency = (time.monotonic() - started) * 1000
                    item = {
                        "id": row.get("id"),
                        "reference": row["text"],
                        "text": result.text,
                        "latency_ms": latency,
                        **metrics(row["text"], result.text, row.get("slots", [])),
                    }
                    # WER only when a reference includes explicit word tokenization.
                    if "words" in row:
                        predicted = result.text.split()
                        item["wer_whitespace_reference_only"] = distance(
                            row["words"], predicted
                        ) / max(1, len(row["words"]))
                    samples.append(item)
                _, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                latencies = [s["latency_ms"] for s in samples]
                slots = sum(s["slot_total"] for s in samples)
                results.append(
                    {
                        "model": model,
                        "profile": profile,
                        "prompt": aid,
                        "samples": samples,
                        "cer": sum(s["char_errors"] for s in samples)
                        / max(1, sum(s["reference_chars"] for s in samples)),
                        "slot_accuracy": sum(s["slot_correct"] for s in samples) / slots
                        if slots
                        else None,
                        "inference_p50_ms": float(np.percentile(latencies, 50)),
                        "inference_p95_ms": float(np.percentile(latencies, 95)),
                        "python_peak_bytes": peak,
                    }
                )
                output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(f"{model} / {profile} / prompt={aid}: {output}")
                del backend
