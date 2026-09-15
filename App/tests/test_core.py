import asyncio
from contextlib import suppress

import numpy as np
import pytest
from fastapi.testclient import TestClient

from sumradio.app import create_app
from sumradio.audio import Segmenter, WhisperEngine
from sumradio.config import ROOT, Settings
from sumradio.extraction import validate_extraction
from sumradio.models import (
    Communication,
    ConfirmLocation,
    CorrectRequest,
    Extraction,
    Location,
    PlaceExpression,
    Run,
    TaskCreate,
    TaskEdit,
    TransitionRequest,
)
from sumradio.phonetics import load_tables, read_table
from sumradio.service import Service
from sumradio.storage import Conflict, Store


@pytest.fixture
def service(tmp_path):
    s = Service(Settings(data_dir=tmp_path))
    yield s
    s.store.close()


def body(**kw):
    return TaskCreate(
        kind="request", title="飲料水を手配", manual_reason="本部で要請を確認", actor="担当A", **kw
    )


def place(expression="上野公園", municipality="東京都台東区", detail=None):
    return PlaceExpression(
        expression=expression, search_name="上野恩賜公園", municipality=municipality, detail=detail
    )


def extraction(comm, **changes):
    evidence = {"communication_id": comm.id, "revision": comm.revision, "quote": comm.text}
    data = {
        "sender": "避難所担当",
        "recipient": "本部",
        "situation": "飲料水不足",
        "people": [],
        "phonetic_interpretations": [],
        "notices": [],
        "tasks": [
            {
                "kind": "request",
                "title": "飲料水15箱を手配",
                "description": comm.text,
                "place": {
                    "expression": "上野公園",
                    "search_name": "上野恩賜公園",
                    "municipality": "台東区",
                    "detail": None,
                },
                "map_categories": ["water"],
                "category_evidence": [evidence],
                "quantities": [{"description": "飲料水", "value": 15, "unit": "箱"}],
                "assignee": None,
                "deadline": None,
                "evidence": [evidence],
                "related_task_ids": [],
                "uncertainties": [],
            }
        ],
    }
    data.update(changes)
    return Extraction.model_validate(data)


def add_comm(s, run):
    comm = Communication(
        run_id=run.id,
        original_text="上野公園へ飲料水15箱をお願いします。",
        transcription_status="done",
    )
    s.store.change(run.id, lambda r: r.communications.__setitem__(comm.id, comm))
    return comm


def test_phonetic_tables():
    tables = load_tables(ROOT)
    assert len(tables["nato"]) == 26
    assert next(x for x in tables["japanese"] if x["character"] == "ア")["label"] == "朝日のア"
    assert next(x for x in tables["nato"] if x["character"] == "A")["aliases"] == [
        "アルファ",
        "アルファー",
        "Alpha",
    ]


@pytest.mark.parametrize("bad", ["missing", "columns", "duplicate"])
def test_invalid_phonetics(tmp_path, bad):
    text = (ROOT / "nato_phonetic.md").read_text()
    if bad == "missing":
        text = text.replace("phonetic-table:end", "no-marker")
    if bad == "columns":
        text = text.replace("| A | Alfa |", "| A | extra | Alfa |")
    if bad == "duplicate":
        text = text.replace("| B | Bravo |", "| A | Bravo |")
    path = tmp_path / "table.md"
    path.write_text(text)
    with pytest.raises(ValueError):
        read_table(path)


@pytest.mark.parametrize("pause,ended", [(4.98, False), (5.0, True)])
def test_exact_silence_boundary(pause, ended):
    s = Segmenter(100, 0.1)
    started, _, end = s.feed(np.ones((2, 1)))
    assert started and not end
    for _ in range(round(pause / 0.02)):
        _, _, end = s.feed(np.zeros((2, 1)))
    assert end is ended
    if not ended:
        assert s.feed(np.ones((2, 1)))[0] is False


def test_preroll_long_speech_and_manual():
    s = Segmenter(100, 0.1)
    for _ in range(100):
        s.feed(np.zeros((2, 1)))
    started, chunks, _ = s.feed(np.ones((2, 1)))
    assert started and sum(len(x) for x in chunks) >= 50
    for _ in range(4000):
        assert not s.feed(np.ones((2, 1)))[2]
    s = Segmenter(100, 0.1, manual=True)
    assert s.feed(np.zeros((2, 1)))[0]
    for _ in range(500):
        assert not s.feed(np.zeros((2, 1)))[2]


async def test_real_places_and_demo_no_http(service, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("デモから外部地名検索を呼んではいけません")

    monkeypatch.setattr("httpx.AsyncClient.get", forbidden)
    r = service.resolver
    assert (await r.resolve("tokyo", place())).candidates[0].name == "上野恩賜公園"
    assert (await r.resolve("tokyo", place(municipality="大阪市"))).status == "unresolved"
    assert (
        await r.resolve("tokyo", place("上野公園の未登録入口", detail="未登録入口"))
    ).status == "unresolved"
    assert (await r.resolve("aoba", place())).status == "unresolved"
    assert (
        await r.resolve(
            "tokyo",
            PlaceExpression(
                expression="未知の公園", search_name="未知の公園", municipality=None, detail=None
            ),
        )
    ).status == "unresolved"
    assert (await r.resolve("tokyo", None)).status == "unresolved"


async def test_location_cache_isolation(service):
    loc = Location(
        id="manual1", name="新しい公園", municipality="東京都", lat=35, lon=139, source="manual"
    )
    p = PlaceExpression(
        expression="新しい公園", search_name="新しい公園", municipality="東京都", detail=None
    )
    service.resolver.remember("tokyo", p, loc)
    assert (await service.resolver.resolve("tokyo", p)).status == "candidates"
    assert (await service.resolver.resolve("aoba", p)).status == "unresolved"


def test_manual_transitions_history_and_recovery(service):
    run = service.create_run("tokyo", "実在デモ")
    task = service.create_task(run.id, body())
    assert task.status == "candidate"
    with pytest.raises(ValueError):
        service.transition(
            run.id, task.id, TransitionRequest(actor="人", expected_version=1, status="completed")
        )
    approved = service.transition(
        run.id, task.id, TransitionRequest(actor="人", expected_version=1, status="unhandled")
    )
    with pytest.raises(Conflict):
        service.transition(
            run.id, task.id, TransitionRequest(actor="人", expected_version=1, status="completed")
        )
    finished = service.transition(
        run.id,
        task.id,
        TransitionRequest(actor="人", expected_version=approved.version, status="completed"),
    )
    assert finished.status == "completed"
    with pytest.raises(ValueError):
        service.transition(
            run.id,
            task.id,
            TransitionRequest(actor="人", expected_version=finished.version, status="candidate"),
        )
    service.store.close()
    restored = Store(service.settings.data_dir)
    assert restored.get(run.id).tasks[task.id].status == "completed"
    assert len(restored.get(run.id).history) == 3
    restored.close()


def test_discard_and_location_confirmation(service):
    run = service.create_run("tokyo", "")
    task = service.create_task(run.id, body(place=place()))
    loc = service.profiles["tokyo"].locations[0]

    def candidates(r):
        r.tasks[task.id].location.candidates = [loc]

    service.store.change(run.id, candidates)
    confirmed = service.confirm_location(
        run.id, task.id, ConfirmLocation(actor="人", expected_version=1, candidate_id=loc.id)
    )
    assert confirmed.location.status == "confirmed" and confirmed.status == "candidate"
    discarded = service.transition(
        run.id,
        task.id,
        TransitionRequest(actor="人", expected_version=confirmed.version, status="discarded"),
    )
    assert discarded.location.confirmed.lat == loc.lat


def test_evidence_and_llm_validation(service):
    run = service.create_run("tokyo", "")
    comm = add_comm(service, run)
    run = service.store.get(run.id)
    output = extraction(comm)
    validate_extraction(output, comm, run, service.tables)
    output.tasks[0].evidence[0].quote = "原文に存在しない引用"
    with pytest.raises(ValueError):
        validate_extraction(output, comm, run, service.tables)
    data = extraction(comm).model_dump()
    data["tasks"][0]["lat"] = 35
    with pytest.raises(ValueError):
        Extraction.model_validate(data)
    output = extraction(comm)
    output.tasks[0].related_task_ids = ["unknown_task"]
    with pytest.raises(ValueError):
        validate_extraction(output, comm, run, service.tables)


async def test_correction_stale_result_run_switch_and_retry(service):
    old = service.create_run("tokyo", "old")
    comm = add_comm(service, old)
    gate = asyncio.Event()
    entered = asyncio.Event()

    class Fake:
        async def extract(self, c, r):
            entered.set()
            await gate.wait()
            return extraction(c)

    service.extractor = Fake()
    worker = asyncio.create_task(service.extract_loop())
    service.enqueue_extraction(old.id, comm.id)
    await entered.wait()
    other = service.create_run("aoba", "other")
    latest = service.store.get(old.id).communications[comm.id]
    service.correct(
        old.id,
        comm.id,
        CorrectRequest(
            actor="人", expected_version=latest.version, text="上野公園へ飲料水10箱をお願いします。"
        ),
    )
    gate.set()
    await asyncio.wait_for(service.extraction_queue.join(), 3)
    stored = service.store.get(old.id)
    assert len(stored.tasks) == 1 and len(service.store.get(other.id).tasks) == 0
    assert stored.communications[comm.id].original_text == comm.original_text
    assert stored.communications[comm.id].extraction_attempts[0]["status"] == "stale"
    service.enqueue_extraction(old.id, comm.id)
    assert service.extraction_queue.empty()
    worker.cancel()
    with suppress(asyncio.CancelledError):
        await worker


async def test_failed_extraction_preserves_recording_and_tasks(service):
    run = service.create_run("tokyo", "")
    comm = add_comm(service, run)
    task = service.create_task(run.id, body())

    class Failure:
        async def extract(self, c, r):
            raise RuntimeError("通信断")

    service.extractor = Failure()
    worker = asyncio.create_task(service.extract_loop())
    service.enqueue_extraction(run.id, comm.id)
    await asyncio.wait_for(service.extraction_queue.join(), 3)
    restored = service.store.get(run.id)
    assert restored.communications[comm.id].text == comm.text
    assert restored.communications[comm.id].extraction_status == "failed"
    assert len(restored.tasks) == 1
    service.transition(
        run.id, task.id, TransitionRequest(actor="人", expected_version=1, status="unhandled")
    )
    worker.cancel()
    with suppress(asyncio.CancelledError):
        await worker


def test_edit_preserves_id_status_and_history(service):
    run = service.create_run("tokyo", "")
    task = service.create_task(run.id, body())
    edited = service.edit_task(
        run.id,
        task.id,
        TaskEdit(
            **body().model_dump(),
            expected_version=1,
        ),
    )
    assert edited.id == task.id and edited.status == "candidate"
    assert len(service.store.get(run.id).history) == 2


def test_api_validation_and_run_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(WhisperEngine, "load", lambda self: None)
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        r = client.post("/api/runs", json={"demo_profile_id": "tokyo"}).json()
        t = client.post(f"/api/runs/{r['id']}/tasks", json=body().model_dump()).json()
        assert t["status"] == "candidate"
        other = client.post("/api/runs", json={"demo_profile_id": "aoba"}).json()
        assert client.get(f"/api/runs/{other['id']}").json()["tasks"] == {}
        assert (
            client.post(
                f"/api/runs/{other['id']}/tasks/{t['id']}/transition",
                json={"actor": "人", "expected_version": 1, "status": "unhandled"},
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/runs",
                json={"demo_profile_id": "tokyo"},
                headers={"origin": "https://example.com"},
            ).status_code
            == 403
        )
        assert client.post("/api/runs", json={"demo_profile_id": "unknown"}).status_code == 404


def test_projection_repair(tmp_path):
    s = Store(tmp_path)
    run = Run(demo_profile_id="aoba", title="復旧")
    comm = Communication(run_id=run.id, original_text="保存した原文", transcription_status="done")
    run.communications[comm.id] = comm
    s.add(run)
    md = s.run_dir(run.id) / f"{comm.id}.md"
    md.write_text("途中で壊れた派生ファイル")
    s.close()
    restored = Store(tmp_path)
    assert "保存した原文" in md.read_text()
    restored.close()
