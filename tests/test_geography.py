from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from sumradio.app import create_app
from sumradio.geography import Geography, parse_yokohama_csv
from sumradio.geography_models import (
    AreaInput, Bounds, LocationConfirmInput, LocationStatus, MapExtraction,
    MasterPlace, OperatingArea, PlaceCandidate, PlaceExpression, YOKOHAMA_AREA,
)
from sumradio.models import (
    ManualTaskInput, TaskEditInput, TaskKind, TaskRecord, TaskSource, TaskState,
    TaskTransitionInput,
)
from sumradio.service import SumradioService
from sumradio.storage import DataStore, VersionConflictError


def task(name="公園", area=None):
    return TaskRecord(
        id="task_geo", kind="request", source="manual", title="確認", action="状況を確認する",
        location=name, area=area or OperatingArea.from_input(YOKOHAMA_AREA),
        created_at="2026-09-15T00:00:00Z", updated_at="2026-09-15T00:00:00Z",
    )


def place(name="公園", **changes):
    return PlaceCandidate.model_validate({
        "id": "place-1", "name": name, "address": "神奈川県横浜市中区", "lat": 35.45,
        "lon": 139.63, "source": "master", **changes,
    })


def remote_place(**changes):
    return {
        "place_id": 1, "osm_type": "way", "osm_id": 10, "name": "公園", "display_name": "横浜市 公園",
        "lat": "35.45", "lon": "139.63", "boundingbox": ["35.44", "35.46", "139.62", "139.64"], **changes,
    }


def make_geo(settings, handler, **options):
    configured = replace(settings, geocoder_enabled=True, geocoder_url="https://geocoder.test/search", geocoder_interval_seconds=0)
    geo = Geography(configured, transport=httpx.MockTransport(handler), **options)
    geo.initialize()
    geo.master = []
    return geo


@pytest.mark.parametrize("values", [{"lat": 91}, {"lon": -181}, {"lat": float("nan")}, {"lon": float("inf")}])
def test_coordinates_must_be_finite_and_in_range(values):
    with pytest.raises(ValidationError):
        place(**values)


def test_area_bounds_and_manual_confirmation_validation():
    with pytest.raises(ValidationError):
        Bounds(south=36, north=35, west=139, east=140)
    with pytest.raises(ValidationError):
        LocationConfirmInput(version=1, candidate_id="x", lat=35, lon=139, name="x")
    with pytest.raises(ValidationError):
        LocationConfirmInput(version=1, lat=35)


def test_official_csv_encoding_and_coordinates():
    csv = "Type,Definition,Name,Address,Lat,Lon,Kana,Ward,WardCode\n地域防災拠点,,本町小学校,神奈川県横浜市中区花咲町,35.4506,139.6285,ホンチョウショウガッコウ,中区,04\n"
    rows = parse_yokohama_csv(csv.encode("cp932"))
    assert rows[0].lat == 35.4506
    assert "横浜市立本町小学校" in rows[0].aliases
    assert rows[0].source_url.startswith("https://www.city.yokohama.lg.jp/")
    with pytest.raises(ValueError):
        parse_yokohama_csv(b"name,latitude\nx,1\n")


def test_broken_downloaded_catalog_falls_back_to_bundled_places(settings):
    root = settings.data_dir / "geography"
    root.mkdir(parents=True)
    (root / "yokohama-places.json").write_text("invalid json")
    geo = Geography(settings)
    geo.initialize()
    assert any(place.name == "本町小学校" for place in geo.master)
    assert len(geo.master) >= 627


@pytest.mark.asyncio
async def test_local_aliases_precede_cache_and_network_and_keep_ambiguity(settings):
    calls = []
    geo = make_geo(settings, lambda request: calls.append(request) or httpx.Response(200, json=[]))
    first = MasterPlace(**place().model_dump(), aliases=["中央公園"])
    second = MasterPlace(**place(id="place-2", lat=35.46).model_dump(), aliases=["中央公園"])
    outside = MasterPlace(**place(id="outside", lat=36).model_dump(), aliases=["中央公園"])
    geo.master = [first, second, outside]
    results = await geo.resolve(task("中央公園"))
    assert {entry.id for entry in results} == {"place-1", "place-2"}
    assert not calls


@pytest.mark.asyncio
async def test_explicit_district_is_not_ignored_by_local_match(settings):
    geo = make_geo(settings, lambda request: httpx.Response(200, json=[]))
    geo.master = [MasterPlace(**place().model_dump())]
    current = task()
    current.map_info = MapExtraction(location=PlaceExpression(source_text="西区の公園", search_name="公園", district="西区"), map_category=["rescue"], evidence_quotes=["公園"])
    assert geo.local_candidates(current) == []


@pytest.mark.asyncio
async def test_unknown_extracted_place_does_not_search_freeform_location(settings):
    geo = make_geo(settings, lambda request: pytest.fail("uncertain place must not be sent"))
    current = task("入口付近・場所不明")
    current.map_info = MapExtraction(location=None, map_category=["rescue"], evidence_quotes=["場所不明"])
    assert await geo.resolve(current) == []


@pytest.mark.asyncio
async def test_human_cache_shared_between_manual_and_extracted_place(settings):
    geo = make_geo(settings, lambda request: pytest.fail("must use the human-confirmed place"))
    current = task("未登録公園")
    current.position.selected = place("未登録公園", source="manual", precision="exact")
    current.position.status = LocationStatus.CONFIRMED
    geo.remember(current)
    current.map_info = MapExtraction(location=PlaceExpression(source_text="未登録公園", search_name="未登録公園"), map_category=["rescue"], evidence_quotes=["未登録公園"])
    assert await geo.resolve(current) == [current.position.selected]


@pytest.mark.asyncio
async def test_concurrent_identical_queries_share_one_request_and_survive_restart(settings):
    requests = []
    geo = make_geo(settings, lambda request: requests.append(request) or httpx.Response(200, json=[remote_place()]))
    results = await asyncio.gather(geo.resolve(task()), geo.resolve(task()))
    assert len(requests) == 1 and results[0] == results[1]
    restored = Geography(geo.settings, transport=httpx.MockTransport(lambda request: pytest.fail("must use disk cache")))
    restored.initialize()
    restored.master = []
    assert await restored.resolve(task()) == results[0]
    assert requests[0].url.params["bounded"] == "1"
    assert requests[0].headers["user-agent"].startswith("Sumradio/")


@pytest.mark.asyncio
async def test_region_changes_have_separate_cache_keys_and_results_are_filtered(settings):
    requests = []
    geo = make_geo(settings, lambda request: requests.append(request) or httpx.Response(200, json=[remote_place(), remote_place(lat="40", place_id=2)]))
    assert len(await geo.resolve(task())) == 1
    other = OperatingArea.from_input(AreaInput(name="別地域", bounds=Bounds(south=36, north=37, west=139, east=140)))
    assert await geo.resolve(task(area=other)) == []
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_request_does_not_contain_task_or_medical_information(settings):
    requests = []
    geo = make_geo(settings, lambda request: requests.append(request) or httpx.Response(200, json=[]))
    current = task("公園")
    current.action = "山田太郎さんの負傷者3名の搬送"
    current.map_info = MapExtraction(
        location=PlaceExpression(source_text="公園の入口に負傷者3名", search_name="公園", detail="入口に負傷者3名"),
        map_category=["rescue"], evidence_quotes=["負傷者3名"],
    )
    await geo.resolve(current)
    assert "公園" in requests[0].url.params["q"]
    query = requests[0].url.params["q"]
    assert all(word not in query for word in ["山田", "負傷", "搬送", "入口"])
    assert "detail" not in requests[0].url.params and requests[0].content == b""


@pytest.mark.asyncio
async def test_negative_cache_and_search_failures(settings):
    calls = []
    geo = make_geo(settings, lambda request: calls.append(request) or httpx.Response(200, json=[]))
    assert await geo.resolve(task()) == []
    assert await geo.resolve(task()) == []
    assert len(calls) == 1
    geo.transport = httpx.MockTransport(lambda request: httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await geo.resolve(task("別の公園"))


@pytest.mark.asyncio
async def test_public_rate_limit_cannot_be_disabled_and_survives_restart(settings):
    now, sleeps, requests = [1000.0], [], []
    async def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
    handler = lambda request: requests.append(now[0]) or httpx.Response(200, json=[remote_place()])
    config = replace(settings, geocoder_enabled=True, geocoder_interval_seconds=0)
    geo = Geography(config, transport=httpx.MockTransport(handler), clock=lambda: now[0], sleep=sleep)
    geo.initialize(); geo.master = []
    await geo.resolve(task("一番"))
    await geo.resolve(task("二番"))
    assert sleeps == [15]
    restored = Geography(config, transport=httpx.MockTransport(handler), clock=lambda: now[0], sleep=sleep)
    restored.initialize(); restored.master = []
    await restored.resolve(task("三番"))
    assert requests == [1000, 1015, 1030]


@pytest.mark.asyncio
async def test_catalog_refresh_failure_keeps_snapshot_and_is_not_repeated(settings):
    calls = []
    geo = Geography(replace(settings, refresh_places=True), transport=httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(503)))
    geo.initialize()
    count = len(geo.master)
    await geo.refresh_catalog(OperatingArea.from_input(YOKOHAMA_AREA))
    await geo.refresh_catalog(OperatingArea.from_input(YOKOHAMA_AREA))
    assert len(calls) == 1 and len(geo.master) == count and count > 500
    assert geo.catalog_status["status"] == "cached"


def stored_task(tmp_path):
    store = DataStore(tmp_path / "data")
    store.initialize()
    store.set_area(YOKOHAMA_AREA)
    created = store.create_manual_task(ManualTaskInput(kind="request", title="飲料水", action="手配", location="公園", map_category=["water"]))
    return store, created


def test_location_confirmation_does_not_approve_task_and_history_survives(tmp_path):
    store, created = stored_task(tmp_path)
    started = store.begin_location(created.id)
    suggested = store.finish_location(started, [place()])
    assert suggested.position.status == LocationStatus.SUGGESTED
    assert suggested.state == TaskState.CANDIDATE
    confirmed = store.confirm_location(created.id, LocationConfirmInput(version=suggested.version, candidate_id="place-1"))
    assert confirmed.state == TaskState.CANDIDATE
    opened = store.transition_task(confirmed.id, TaskTransitionInput(version=confirmed.version, target_state="open"))
    done = store.transition_task(opened.id, TaskTransitionInput(version=opened.version, target_state="done"))
    restored = DataStore(store.root); restored.initialize()
    assert restored.get_task(done.id).position.selected == place()
    assert any(entry.action == "location_confirmed" for entry in restored.get_task(done.id).history)
    assert restored.active_area == OperatingArea.from_input(YOKOHAMA_AREA)


def test_late_search_cannot_overwrite_manual_position_or_changed_location(tmp_path):
    store, created = stored_task(tmp_path)
    started = store.begin_location(created.id)
    manual = store.confirm_location(created.id, LocationConfirmInput(version=started.version, name="手動位置", lat=35.46, lon=139.64))
    assert store.finish_location(started, [place()]) is None
    assert store.get_task(created.id).position.selected.name == "手動位置"
    edited = store.edit_task(created.id, TaskEditInput(version=manual.version, title=manual.title, action=manual.action, location="別の公園"))
    assert edited.position.selected is None
    assert store.finish_location(started, [place()]) is None


def test_multiple_candidates_require_selection_and_invalid_id_is_rejected(tmp_path):
    store, created = stored_task(tmp_path)
    found = store.finish_location(store.begin_location(created.id), [place(), place(id="second", lat=35.46)])
    assert found.position.status == LocationStatus.AMBIGUOUS and found.position.selected is None
    with pytest.raises(ValueError):
        store.confirm_location(found.id, LocationConfirmInput(version=found.version, candidate_id="invented"))
    with pytest.raises(ValueError):
        store.confirm_location(found.id, LocationConfirmInput(version=found.version, name="範囲外", lat=40, lon=140))


@pytest.mark.asyncio
async def test_search_wait_does_not_block_board_and_human_change_wins(settings):
    service = SumradioService(settings)
    gate, entered = asyncio.Event(), asyncio.Event()
    async def delayed(current):
        entered.set()
        await gate.wait()
        return [place()]
    await service.start()
    try:
        await service.set_area(YOKOHAMA_AREA)
        service.geography.resolve = delayed
        created = await service.create_manual_task(ManualTaskInput(kind="request", title="飲料水", action="手配", location="公園"))
        await entered.wait()
        opened = await asyncio.wait_for(service.transition_task(created.id, TaskTransitionInput(version=created.version, target_state="open")), 0.5)
        fixed = await service.confirm_location(opened.id, LocationConfirmInput(version=opened.version, name="現場", lat=35.45, lon=139.64))
        gate.set()
        await asyncio.sleep(0)
        assert service.store.get_task(fixed.id).position.selected.name == "現場"
        assert service.store.get_task(fixed.id).state == TaskState.OPEN
    finally:
        await service.stop()


def test_api_requires_area_and_validates_location_versions(settings):
    with TestClient(create_app(settings)) as client:
        payload = {"kind": "request", "title": "手配", "action": "飲料水"}
        assert client.post("/api/tasks", json=payload).status_code == 422
        assert client.put("/api/area", json=YOKOHAMA_AREA.model_dump()).status_code == 200
        created = client.post("/api/tasks", json=payload).json()
        data = {"version": created["version"], "name": "手動地点", "lat": 35.45, "lon": 139.63}
        confirmed = client.put(f"/api/tasks/{created['id']}/location", json=data)
        assert confirmed.status_code == 200
        assert confirmed.json()["state"] == "candidate"
        assert client.put(f"/api/tasks/{created['id']}/location", json=data).status_code == 409
        discarded = client.post(f"/api/tasks/{created['id']}/transition", json={"version": confirmed.json()["version"], "target_state": "discarded"})
        assert discarded.status_code == 200
        assert not client.get("/api/state").json()["tasks"]
        history = client.get("/api/tasks?include_discarded=true").json()["tasks"]
        assert history[0]["position"]["selected"]["name"] == "手動地点"
        assert client.get(f"/api/tasks/{created['id']}").status_code == 200


def test_old_saved_task_loads_without_geography_fields(tmp_path):
    store, created = stored_task(tmp_path)
    path = store.tasks_dir / f"{created.id}.json"
    data = json.loads(path.read_text())
    for field in ["area", "map_info", "map_category", "position"]:
        data.pop(field)
    path.write_text(json.dumps(data))
    store.initialize()
    assert store.get_task(created.id).position.status == LocationStatus.UNRESOLVED
    assert store.get_task(created.id).map_category == ["other"]
