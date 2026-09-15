import asyncio
from contextlib import suppress

import httpx
import pytest
from fastapi.testclient import TestClient

from scripts.interpretation_fixture import make_run
from sumradio.app import create_app
from sumradio.audio import WhisperEngine
from sumradio.config import Settings
from sumradio.models import ConfirmInterpretationLocation, CorrectRequest, LocationState, Status
from sumradio.service import Service
from sumradio.storage import Conflict, Store


@pytest.fixture
def service(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("この照合は外部地名検索を使用しません")

    monkeypatch.setattr(httpx.AsyncClient, "get", forbidden)
    s = Service(Settings(data_dir=tmp_path))
    s.store.add(make_run())
    yield s
    s.store.close()


def approve(service, **overrides):
    return service.confirm_interpretation_location(
        "run_interpretationtest",
        "task_interpretationtest",
        ConfirmInterpretationLocation(
            **{
                "actor": "本部担当",
                "expected_version": 1,
                "communication_id": "comm_interpretationtest",
                "revision": 1,
                "interpretation_index": 0,
                "location_id": "tokyo_ueno",
                **overrides,
            }
        ),
    )


def test_approve_keeps_original_and_task_state_saves_history_and_restores(service):
    before = service.store.get("run_interpretationtest")
    view = service.view_run(before.id)
    assert len(view.interpretation_locations) == 1
    option = view.interpretation_locations[0]
    assert option.location.id == "tokyo_ueno"
    assert not view.tasks[option.task_id].location.candidates
    assert not view.tasks[option.task_id].location.confirmed
    task = approve(service)
    after = service.store.get(before.id)
    assert after.communications == before.communications
    for key in ("status", "place", "quantities", "uncertainties", "evidence", "description"):
        assert getattr(task, key) == getattr(before.tasks[task.id], key)
    assert task.status == "candidate"
    assert task.location.confirmed.name == "上野恩賜公園"
    assert task.location.confirmed.precision == "representative"
    assert (
        task.location.interpretation.evidence.quote
        == before.communications[option.communication_id].text
    )
    assert task.location.confirmed_by == "本部担当"
    assert task.version == 2
    assert after.history[-1].action == "location_interpretation_confirmed"
    assert not service.view_run(before.id).interpretation_locations
    assert service.resolver.cache("tokyo")[0].aliases.count("上野恩師公園") == 1
    service.store.close()
    restored = Store(service.settings.data_dir)
    try:
        assert restored.get(before.id) == after
    finally:
        restored.close()


@pytest.mark.parametrize(
    "change",
    [
        "region",
        "municipality",
        "duplicate",
        "unknown",
        "detail",
        "missing_detail",
        "stale",
        "foreign_reference",
        "foreign_task_evidence",
        "unrelated_place",
        "multiple_places",
        "completed",
        "discarded",
        "confirmed",
        "manual",
        "blank",
    ],
)
def test_unsafe_or_ambiguous_proposals_are_not_offered(service, change):
    if change == "duplicate":
        p = service.profiles["tokyo"].locations[0]
        service.profiles["tokyo"].locations.append(p.model_copy(update={"id": "duplicate"}))

    def mutate(run):
        task = run.tasks["task_interpretationtest"]
        comm = run.communications["comm_interpretationtest"]
        interpreted = comm.extraction.transcript_interpretations[0]
        if change == "region":
            run.region.municipality = "渋谷区"
        if change == "municipality":
            task.place.municipality = "東京都渋谷区"
            comm.extraction.tasks[0].place.municipality = "東京都渋谷区"
        if change == "unknown":
            interpreted.possible_meaning = "未登録公園"
        if change == "blank":
            interpreted.possible_meaning = None
        if change == "detail":
            task.place.detail = "未登録の北口"
            comm.extraction.tasks[0].place.detail = task.place.detail
        if change == "missing_detail":
            task.place.expression += "北口"
            comm.extraction.tasks[0].place.expression = task.place.expression
            comm.original_text = comm.original_text.replace("上野恩師公園", task.place.expression)
            interpreted.evidence.quote = comm.original_text
            task.evidence[0].quote = comm.original_text
        if change == "stale":
            comm.revision = 2
        if change == "foreign_reference":
            interpreted.evidence.communication_id = "another_comm"
        if change == "foreign_task_evidence":
            task.evidence[0].communication_id = "another_comm"
        if change == "unrelated_place":
            task.place.expression = "別の公園"
        if change == "multiple_places":
            other = comm.extraction.tasks[0].model_copy(deep=True)
            other.place.expression = "代々木公園"
            comm.extraction.tasks.append(other)
            comm.original_text += "代々木公園にも水を。"
            interpreted.evidence.quote = comm.original_text
        if change in {"completed", "discarded"}:
            task.status = Status(change)
        if change == "confirmed":
            task.location = LocationState(
                status="confirmed", confirmed=service.profiles["tokyo"].locations[1]
            )
        if change == "manual":
            task.origin = "manual"

    service.store.change("run_interpretationtest", mutate)
    assert service.view_run("run_interpretationtest").interpretation_locations == []
    with pytest.raises(Conflict):
        approve(service)


@pytest.mark.parametrize(
    "override",
    [
        {"expected_version": 2},
        {"revision": 2},
        {"communication_id": "another_comm"},
        {"interpretation_index": 3},
        {"location_id": "tokyo_yoyogi"},
    ],
)
def test_forged_or_stale_approval_is_rejected_without_mutation(service, override):
    before = service.store.get("run_interpretationtest")
    with pytest.raises(Conflict):
        approve(service, **override)
    assert service.store.get(before.id) == before


def test_double_click_and_correction_after_display_are_rejected(service):
    approve(service)
    with pytest.raises(Conflict):
        approve(service)
    assert len(service.store.get("run_interpretationtest").history) == 1
    service.store.add(make_run())
    service.correct(
        "run_interpretationtest",
        "comm_interpretationtest",
        CorrectRequest(actor="確認者", expected_version=1, text="別の場所です"),
    )
    with pytest.raises(Conflict):
        approve(service)


async def test_pending_location_job_cannot_overwrite_human_approval(service):
    started, finish = asyncio.Event(), asyncio.Event()

    async def delayed(*args):
        started.set()
        await finish.wait()
        return LocationState(status="unresolved")

    service.resolver.resolve = delayed
    service.store.change(
        "run_interpretationtest",
        lambda r: setattr(r.tasks["task_interpretationtest"], "location", LocationState()),
    )
    service.enqueue_location(
        "run_interpretationtest",
        service.store.get("run_interpretationtest").tasks["task_interpretationtest"],
    )
    worker = asyncio.create_task(service.location_loop())
    try:
        await asyncio.wait_for(started.wait(), 1)
        approve(service)
        finish.set()
        await asyncio.wait_for(service.location_queue.join(), 1)
        assert (
            service.store.get("run_interpretationtest")
            .tasks["task_interpretationtest"]
            .location.status
            == "confirmed"
        )
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker


def test_api_existing_recording_can_be_approved_without_reextraction(service, monkeypatch):
    monkeypatch.setattr(WhisperEngine, "load", lambda self: None)
    with TestClient(create_app(service.settings, service)) as client:
        view = client.get("/api/runs/run_interpretationtest").json()
        option = view["interpretation_locations"][0]
        body = {k: option[k] for k in ("communication_id", "revision", "interpretation_index")}
        body.update(
            actor="本部担当",
            expected_version=option["task_version"],
            location_id=option["location"]["id"],
        )
        url = (
            "/api/runs/run_interpretationtest/tasks/task_interpretationtest/interpretation-location"
        )
        result = client.post(url, json=body)
        assert result.status_code == 200
        assert result.json()["status"] == "candidate"
        assert result.json()["location"]["status"] == "confirmed"
        assert client.post(url, json=body).status_code == 409
