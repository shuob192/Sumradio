import asyncio
import threading
import time
import warnings
from contextlib import suppress
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from sumradio.audio import SpeechWorker, WhisperEngine
from sumradio.config import Settings
from sumradio.models import Communication, Run
from sumradio.storage import Store


@pytest.fixture
def speech(tmp_path):
    store = Store(tmp_path)
    run = store.add(Run(demo_profile_id="aoba", title="字幕と確定の分離テスト"))
    comm = Communication(
        run_id=run.id,
        original_audio="original.wav",
        recognition_audio="16k.wav",
        transcription_status="queued",
        metrics={"finalized_at_epoch": time.time()},
    )
    store.change(run.id, lambda r: r.communications.__setitem__(comm.id, comm))
    directory = store.run_dir(run.id) / "audio"
    directory.mkdir()
    sf.write(directory / comm.original_audio, np.zeros(16000 * 2), 16000)
    mic = SimpleNamespace(run_id=run.id, comm_id=comm.id, latest=None)
    events, results = [], []
    worker = SpeechWorker(
        Settings(data_dir=tmp_path), store, mic, events.append, lambda *v: results.append(v)
    )
    yield worker, run, comm, events, results
    store.close()


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest.mark.parametrize("fail_partial", [False, True])
async def test_final_does_not_wait_for_running_partial_and_discards_stale_result(
    speech, fail_partial
):
    worker, run, comm, events, results = speech
    loop = asyncio.get_running_loop()
    partial_started, partial_finished = asyncio.Event(), asyncio.Event()
    release_partial = threading.Event()

    class Engine:
        state = "ready"

        def load(self):
            pass

        def transcribe(self, data, rate):
            if len(data) == 16000:
                loop.call_soon_threadsafe(partial_started.set)
                assert release_partial.wait(3)
                loop.call_soon_threadsafe(partial_finished.set)
                if fail_partial:
                    raise RuntimeError("暫定字幕だけ失敗")
                return "古い字幕"
            assert len(data) == 32000 and rate == 16000
            return "交信全体の文字記録"

    worker.engine = Engine()
    worker.mic.latest = (run.id, comm.id, np.zeros(16000), 16000, time.time())
    running = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(partial_started.wait(), 2)
        worker.mic.run_id, worker.mic.comm_id, worker.mic.latest = None, None, None
        worker.enqueue(run.id, comm.id)
        await asyncio.wait_for(worker.finals.join(), 2)
        assert not release_partial.is_set()
        assert results[0][2] == "交信全体の文字記録"
        metrics = worker.store.get(run.id).communications[comm.id].metrics
        assert metrics["speech_queue_wait_ms"] >= 0
        assert metrics["speech_preparation_ms"] >= 0
        release_partial.set()
        await asyncio.wait_for(partial_finished.wait(), 2)
        await until(lambda: not worker.final_active)
        assert not any(event["type"] == "subtitle" for event in events)
    finally:
        release_partial.set()
        running.cancel()
        with suppress(asyncio.CancelledError):
            await running


async def test_queued_finals_have_priority_and_no_new_partial_starts(speech):
    worker, run, comm, _, results = speech
    loop = asyncio.get_running_loop()
    first_started = asyncio.Event()
    release_final = threading.Event()
    calls = []

    class Engine:
        state = "ready"

        def load(self):
            pass

        def transcribe(self, data, rate):
            kind = "partial" if len(data) == 16000 else "final"
            calls.append(kind)
            if kind == "final" and calls.count("final") == 1:
                loop.call_soon_threadsafe(first_started.set)
                assert release_final.wait(3)
            return kind

    worker.engine = Engine()
    worker.enqueue(run.id, comm.id)
    second = comm.model_copy(update={"id": "comm_second"})
    worker.store.change(run.id, lambda r: r.communications.__setitem__(second.id, second))
    worker.enqueue(run.id, second.id)
    worker.mic.latest = (run.id, comm.id, np.zeros(16000), 16000, time.time())
    running = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(first_started.wait(), 2)
        # Yield to the partial loop while the final model is busy; it must stay idle.
        await asyncio.sleep(0.08)
        assert calls == ["final"]
        release_final.set()
        await asyncio.wait_for(worker.finals.join(), 2)
        await until(lambda: len(calls) == 3)
        assert calls == ["final", "final", "partial"]
        assert [value[1] for value in results] == [comm.id, second.id]
    finally:
        release_final.set()
        running.cancel()
        with suppress(asyncio.CancelledError):
            await running


def test_model_load_allocates_two_inference_workers_without_prompts(tmp_path, monkeypatch):
    import faster_whisper

    captured = {}

    class Model:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(faster_whisper, "WhisperModel", Model)
    engine = WhisperEngine(Settings(data_dir=tmp_path, whisper_threads=4))
    engine.load()
    assert engine.state == "ready"
    assert captured["num_workers"] == 2
    assert captured["cpu_threads"] == 4
    assert captured["device"] == "cpu" and captured["compute_type"] == "int8"
    assert captured["local_files_only"] is True


def test_integer_telemetry_serializes_without_warnings():
    comm = Communication(run_id="run_metrics")
    comm.metrics["subtitle_samples"] = 1
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert '"subtitle_samples":1' in comm.model_dump_json()
