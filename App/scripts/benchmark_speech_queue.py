"""Exercise the real speech worker with saved audio in isolated temporary storage, no mic/LLM."""

import argparse
import asyncio
import hashlib
import shutil
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import soundfile as sf

from sumradio.audio import SpeechWorker
from sumradio.config import Settings
from sumradio.models import Communication, Run, now
from sumradio.storage import Store, atomic_json


async def measure(audio_path, settings):
    with tempfile.TemporaryDirectory(prefix="sumradio-speech-benchmark-") as temporary:
        store = Store(Path(temporary))
        run = store.add(Run(demo_profile_id="tokyo", title="内部性能測定"))
        comm = Communication(
            run_id=run.id,
            original_audio="original.wav",
            recognition_audio="16k.wav",
            transcription_status="recording",
        )
        store.change(run.id, lambda r: r.communications.__setitem__(comm.id, comm))
        directory = store.run_dir(run.id) / "audio"
        directory.mkdir()
        shutil.copyfile(audio_path, directory / comm.original_audio)
        data, rate = sf.read(audio_path, dtype="float32")
        if len(data) <= 20 * rate:
            raise ValueError("暫定と確定の処理を識別するため、20秒を超える録音を指定してください")
        mic = SimpleNamespace(run_id=run.id, comm_id=comm.id, latest=None)
        completed = asyncio.Event()
        partial_started = asyncio.Event()
        loop = asyncio.get_running_loop()
        calls, published, transcripts = [], [], []

        def on_transcript(*values):
            transcripts.append({"text": values[2], "recognition_seconds": values[3]})
            completed.set()

        worker = SpeechWorker(settings, store, mic, published.append, on_transcript)
        transcribe = worker.engine.transcribe

        def observed(data, rate):
            kind = "partial" if len(data) / rate <= 20 else "final"
            entry = {
                "kind": kind,
                "started": time.perf_counter(),
                "audio_seconds": len(data) / rate,
            }
            calls.append(entry)
            if kind == "partial":
                loop.call_soon_threadsafe(partial_started.set)
            try:
                return transcribe(data, rate)
            finally:
                entry["ended"] = time.perf_counter()

        worker.engine.transcribe = observed
        task = asyncio.create_task(worker.run())
        try:
            async with asyncio.timeout(60):
                while worker.engine.state == "loading":
                    await asyncio.sleep(0.05)
                if worker.engine.state != "ready":
                    raise RuntimeError(worker.engine.error)
                mic.latest = (run.id, comm.id, np.copy(data[-20 * rate :]), rate, time.time())
                await partial_started.wait()
                # Simulate manual stop immediately after a partial recognition has started.
                mic.run_id, mic.comm_id, mic.latest = None, None, None
                finalized = time.perf_counter()
                store.change(
                    run.id,
                    lambda r: r.communications[comm.id].metrics.update(
                        {"finalized_at_epoch": time.time()}
                    ),
                )
                worker.enqueue(run.id, comm.id)
                await completed.wait()
                delay = time.perf_counter() - finalized
                await worker.finals.join()
                while any("ended" not in call for call in calls):
                    await asyncio.sleep(0.05)
                final = next(call for call in calls if call["kind"] == "final")
                return {
                    "final_delay_seconds": round(delay, 4),
                    "pre_recognition_seconds": round(final["started"] - finalized, 4),
                    "text": transcripts[0]["text"],
                    "calls": [
                        {
                            "kind": call["kind"],
                            "audio_seconds": call["audio_seconds"],
                            "started_after_finalize_seconds": round(call["started"] - finalized, 4),
                            "duration_seconds": round(call["ended"] - call["started"], 4),
                        }
                        for call in calls
                    ],
                    "stale_subtitles_published": sum(e["type"] == "subtitle" for e in published),
                    "metrics": store.get(run.id).communications[comm.id].metrics,
                }
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            store.close()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--threads", type=int, default=Settings().whisper_threads)
    parser.add_argument("--rounds", type=int, default=2)
    args = parser.parse_args()
    if args.output.exists() or args.rounds < 1:
        parser.error("未使用の出力名と1回以上のroundsを指定してください")
    report = {
        "mode": "saved_audio_real_worker_simulated_stop_not_microphone_acceptance",
        "at": now(),
        "audio_sha256": hashlib.sha256(args.audio.read_bytes()).hexdigest(),
        "threads": args.threads,
        "source_sha256": hashlib.sha256(Path("sumradio/audio.py").read_bytes()).hexdigest(),
        "limitations": "暫定字幕の処理直後に手動確定を模擬。モデルロード・実録音・無音待ち・画面描画・Codexは時間に含まない。生成データは一時保存先のみ。",
        "results": [],
    }
    for index in range(args.rounds):
        result = await measure(args.audio, Settings(whisper_threads=args.threads))
        report["results"].append(result)
        atomic_json(args.output, report)
        print(f"round={index + 1}: {result['final_delay_seconds']}s {result['text']}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
