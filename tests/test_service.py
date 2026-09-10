import sys
from types import SimpleNamespace

import numpy as np

from sumradio.asr import FasterWhisperBackend, Result
from sumradio.extraction import Extractor


def test_backend_contract_is_offline_and_iterates_segments(settings, monkeypatch):
    calls = []

    class Model:
        def __init__(self, name, **kwargs):
            calls.append((name, kwargs))

        def transcribe(self, audio, **kwargs):
            calls.append(kwargs)
            segment = SimpleNamespace(
                start=0, end=1, text="訓練", avg_logprob=-0.2, no_speech_prob=0.01
            )
            return iter([segment]), None

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    backend = FasterWhisperBackend(settings, Extractor(settings.config_dir).prompt)
    result = backend.transcribe(np.ones(16000, dtype=np.float32))
    backend.transcribe(np.ones(16000, dtype=np.float32), partial=True)
    assert result.text == "訓練" and result.segments[0]["end"] == 1
    assert calls[0][1]["local_files_only"] is True
    assert calls[0][1]["compute_type"] == "int8"
    assert calls[1]["condition_on_previous_text"] is False
    assert calls[1]["language"] == "ja" and calls[1]["beam_size"] == 5
    assert calls[2][0] == "small" and calls[3]["beam_size"] == 1


def test_partial_never_extracts_tasks_and_stale_result_is_discarded(client, backend):
    svc = client.app.state.service
    samples = np.ones((16000, 1), dtype=np.float32) * 0.1
    svc.partial_token = "current"
    svc._partial(samples, 16000, "current", svc.generation)
    assert svc.live["partial_preview"]
    assert not svc.store.snapshot()["events"]
    assert not svc.store.snapshot()["tasks"]
    before = svc.live.copy()
    backend.result = Result("古い別発話", "fake")
    svc._partial(samples, 16000, "stale", svc.generation)
    assert svc.live == before
    svc._partial(samples, 16000, "current", svc.generation - 1)
    assert svc.live == before


def test_disconnect_does_not_get_cleared_by_final_result(client, backend):
    svc = client.app.state.service
    svc.status(state="disconnected", message="入力デバイス切断")
    svc.pending = 1
    event = svc._event("live_radio")
    import time

    svc._process(event, np.ones((16000, 1), dtype=np.float32) * 0.1, 16000, time.monotonic())
    assert svc.live["state"] == "disconnected"
    assert svc.live["message"] == "入力デバイス切断"
