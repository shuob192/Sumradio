import io
import json
import time

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from sumradio.app import create_app, sse_frame
from sumradio.asr import Result
from sumradio.store import Store


def wait_for(client, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = client.get("/api/snapshot").json()
        if predicate(snapshot):
            return snapshot
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for service")


def add_result(client, text):
    svc = client.app.state.service
    event = svc._event("mock")
    return svc.finalize(event, Result(text, "test"), 10)


def wav(silent=False):
    audio = np.zeros(16000) if silent else 0.1 * np.sin(np.arange(16000) * 2 * np.pi * 440 / 16000)
    buffer = io.BytesIO()
    sf.write(buffer, audio, 16000, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def test_demo_five_events_three_runs(client):
    for run in range(3):
        assert client.post("/api/demo/start?interval=0.1").status_code == 200
        snapshot = wait_for(
            client,
            lambda s, run=run: len(s["events"]) == (run + 1) * 5 and s["live"]["state"] == "idle",
        )
        assert all(
            e["status"] == "needs_review" and e["source"] == "mock" for e in snapshot["events"]
        )
        assert len(snapshot["tasks"]) == run + 1
        assert all(t["status"] == "candidate" for t in snapshot["tasks"])


def test_human_review_task_lifecycle_and_history(client, settings, backend):
    event = add_result(client, "訓練、青葉避難所へ飲料水15箱を手配願う")
    (task,) = client.get("/api/snapshot").json()["tasks"]
    patch = client.patch(
        f"/api/events/{event['id']}",
        json={"actor": "A", "corrected_text": "訓練、飲料水16箱", "status": "confirmed"},
    )
    assert patch.status_code == 200
    assert patch.json()["raw_text"] == event["raw_text"]
    assert patch.json()["corrected_by"] == "A"
    for status in ["open", "done", "open", "dismissed", "candidate"]:
        response = client.patch(
            f"/api/tasks/{task['id']}",
            json={"actor": "B", "status": status, "assignee": "支援二班", "due_at": "14時"},
        )
        assert response.status_code == 200
        assert bool(response.json()["completed_at"]) == (status == "done")
    rows = client.get(f"/api/history/{task['id']}").json()
    assert len(rows) == 6
    assert rows[-1]["actor"] == "B"
    assert rows[-1]["before"]["status"] == "dismissed"
    db = Store(settings.data_dir / "sumradio.sqlite3")
    assert db.get("events", event["id"])["status"] == "confirmed"
    assert db.get("tasks", task["id"])["due_at"] == "14時"
    db.close()


def test_completion_evidence_is_linked_only_by_human(client):
    event = add_result(client, "訓練、青葉避難所へ飲料水を手配願う")
    (task,) = client.get("/api/snapshot").json()["tasks"]
    completion = add_result(client, "訓練、青葉避難所へ飲料水手配、完了")
    assert completion["completion_suggestions"]
    assert client.get("/api/snapshot").json()["tasks"][0]["status"] == "candidate"
    updated = client.patch(
        f"/api/tasks/{task['id']}",
        json={"actor": "A", "status": "done", "source_event_ids": [event["id"], completion["id"]]},
    ).json()
    assert updated["status"] == "done"
    assert len(updated["source_event_ids"]) == 2


def test_manual_task_validates_sources_and_nulls(client):
    event = add_result(client, "訓練、要支援者15名")
    request = {"actor": "A", "title": "人数を確認", "source_event_ids": [event["id"]]}
    task = client.post("/api/tasks", json=request).json()
    assert task["status"] == "candidate"
    assert (
        client.post("/api/tasks", json={**request, "source_event_ids": ["missing"]}).status_code
        == 404
    )
    for bad in [
        {"status": None},
        {"title": None},
        {"title": "   "},
        {"source_event_ids": None},
        {"status": "invalid"},
        {"raw_text": "tamper"},
    ]:
        assert client.patch(f"/api/tasks/{task['id']}", json={"actor": "A", **bad}).status_code in (
            400,
            422,
        )
    assert (
        client.patch(
            f"/api/events/{event['id']}", json={"actor": " ", "status": "confirmed"}
        ).status_code
        == 422
    )


def test_wav_pipeline_and_audio_links(client, backend):
    response = client.post("/api/audio", files={"file": ("radio.wav", wav(), "audio/wav")})
    assert response.status_code == 202
    snapshot = wait_for(
        client, lambda s: s["events"] and s["events"][0]["status"] == "needs_review"
    )
    (event,) = snapshot["events"]
    assert event["source"] == "recorded_radio"
    assert backend.calls == [(16000, False)]
    for variant in ["raw", "processed"]:
        response = client.get(f"/api/audio/{event['id']}/{variant}")
        assert response.status_code == 200
        assert sf.info(io.BytesIO(response.content)).samplerate == 16000
    assert client.get(f"/api/audio/{event['id']}/invalid").status_code == 404


@pytest.mark.parametrize("failure", ["silent", "empty", "exception", "noise"])
def test_asr_failure_remains_visible_without_tasks(client, backend, failure):
    if failure == "empty":
        backend.result = Result("", "test")
    if failure == "exception":
        backend.error = RuntimeError("model unavailable")
    if failure == "noise":
        backend.result = Result("幻覚の字幕", "test", -2, 0.95)
    client.post("/api/audio", files={"file": ("radio.wav", wav(failure == "silent"), "audio/wav")})
    snapshot = wait_for(client, lambda s: s["events"] and s["events"][0]["status"] == "failed")
    assert not snapshot["tasks"]
    assert snapshot["events"][0]["error"]
    if failure == "silent":
        assert not backend.calls


def test_sse_order_resume_atomicity_and_restart(client, settings, backend):
    snapshot = client.get("/api/snapshot").json()
    add_result(client, "訓練、青葉避難所へ飲料水を手配願う")
    store = client.app.state.service.store
    rows = store.since(snapshot["cursor"])
    assert [r["kind"] for r in rows] == ["event", "task"]
    assert rows[0]["seq"] < rows[1]["seq"]
    assert store.since(rows[0]["seq"]) == [rows[1]]
    assert sse_frame(rows[0]).startswith(f"id: {rows[0]['seq']}\nevent: event\ndata: ")
    assert json.loads(sse_frame(rows[0]).split("data: ")[1].strip())["status"] == "needs_review"
    with TestClient(create_app(settings, backend)) as restored:
        restored_snapshot = restored.get("/api/snapshot").json()
        assert len(restored_snapshot["events"]) == len(restored_snapshot["tasks"]) == 1
        assert restored_snapshot["cursor"] >= rows[-1]["seq"]


def test_crash_recovery(client, settings, backend):
    service = client.app.state.service
    event = service._event("recorded_radio")
    service.store.save_event(event)
    with TestClient(create_app(settings, backend)) as restored:
        assert restored.get("/api/snapshot").json()["events"][0]["status"] == "failed"


def test_local_api_security_and_invalid_audio(client):
    assert client.get("/", headers={"host": "evil.example"}).status_code == 403
    assert (
        client.post("/api/demo/start", headers={"origin": "https://evil.example"}).status_code
        == 403
    )
    assert (
        client.post("/api/demo/start", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    )
    assert (
        client.post("/api/audio", files={"file": ("x.wav", b"bad", "audio/wav")}).status_code == 400
    )
    assert client.get("/api/stream", headers={"last-event-id": "invalid"}).status_code == 400
    assert client.get("/static/app.js").status_code == 200
    assert "frame-ancestors 'none'" in client.get("/").headers["content-security-policy"]


def test_export_and_replay_cancel(client):
    client.post("/api/demo/start?interval=1")
    assert client.post("/api/demo/start").status_code == 400
    assert client.post("/api/demo/stop").status_code == 200
    add_result(client, "訓練、青葉避難所へ飲料水を手配願う")
    rows = [json.loads(line) for line in client.get("/api/export").text.splitlines() if line]
    assert {row["kind"] for row in rows} == {"events", "tasks"}
