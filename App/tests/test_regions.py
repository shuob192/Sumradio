import asyncio
from contextlib import suppress

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from sumradio.app import create_app
from sumradio.audio import WhisperEngine
from sumradio.config import Settings
from sumradio.models import Communication, Location, PlaceExpression, Region, Run, TaskCreate
from sumradio.regions import PREFECTURES, geocoder_municipality, in_region, region_conflicts
from sumradio.service import Service
from sumradio.storage import Store


@pytest.fixture
def service(tmp_path):
    service = Service(Settings(data_dir=tmp_path))
    yield service
    service.store.close()


def place(name="上野公園", municipality=None, detail=None):
    return PlaceExpression(
        expression=name, search_name=name, municipality=municipality, detail=detail
    )


def region(municipality="台東区", prefecture="東京都"):
    return Region(prefecture=prefecture, municipality=municipality)


def test_region_validation_and_normalization():
    assert len(PREFECTURES) == len(set(PREFECTURES)) == 47
    assert region(" 東京都 台東区 ").municipality == "台東区"
    assert region("").label == "東京都"
    assert region("横浜市港北区", "神奈川県").municipality == "横浜市港北区"
    for prefecture, municipality in [
        ("東京", "台東区"),
        ("東京都", "大阪府大阪市"),
        ("東京都", "台東区・渋谷区"),
        ("東京都", "台東区上野公園"),
    ]:
        with pytest.raises(ValidationError):
            region(municipality, prefecture)


@pytest.mark.parametrize(
    "scope, address, expected",
    [
        (region(), "東京都台東区", True),
        (region(), "東京都渋谷区", False),
        (region("東区"), "東京都台東区", False),
        (region("東区"), "東京都江東区", False),
        (region(""), "東京都渋谷区", True),
        (region(""), "京都府京都市", False),
        (region("中央区"), "東京都江東区・東京都中央区", True),
        (region("中央区"), "東京都江東区・中央区", True),
        (region("大阪市", "大阪府"), "大阪府大阪市北区", True),
        (region("大阪市北区", "大阪府"), "大阪府堺市北区", False),
        (region(), "台東区", False),
    ],
)
def test_scope_respects_administrative_components(scope, address, expected):
    assert in_region(scope, address) is expected


def test_explicit_conflicting_area_is_not_overridden():
    assert region_conflicts(region(), "東京都渋谷区")
    assert region_conflicts(region(), "大阪府台東区")
    assert not region_conflicts(region(), "東京都")
    assert not region_conflicts(region(), "台東区")
    assert not region_conflicts(region(), None)


async def test_region_filters_master_and_unknown_detail_without_external_lookup(
    service, monkeypatch
):
    def forbidden(*args, **kwargs):
        raise AssertionError("デモから外部地名検索してはいけません")

    monkeypatch.setattr(httpx.AsyncClient, "get", forbidden)
    resolver = service.resolver
    p = place()
    assert (await resolver.resolve("tokyo", p, region())).candidates[0].id == "tokyo_ueno"
    assert p.municipality is None  # Scope must not become claimed radio content.
    assert (await resolver.resolve("tokyo", p, region("渋谷区"))).status == "unresolved"
    conflict = await resolver.resolve("tokyo", place(municipality="渋谷区"), region())
    assert "一致しません" in conflict.reason
    assert (await resolver.resolve("tokyo", place("未知の公園"), region())).status == "unresolved"
    assert (
        await resolver.resolve("tokyo", place("上野公園北入口", detail="北入口"), region())
    ).status == "unresolved"
    assert [x.id for x in resolver.registered_locations("tokyo", region())] == ["tokyo_ueno"]


async def test_same_name_cache_not_overwritten_across_regions(service):
    resolver = service.resolver
    for ward, lat in [("台東区", 35.7), ("渋谷区", 35.6)]:
        resolver.remember(
            "tokyo",
            place("確認した広場"),
            Location(
                id="manual",
                name="確認した広場",
                municipality="東京都" + ward,
                lat=lat,
                lon=139.7,
                source="manual",
            ),
            region(ward),
        )
    assert len(resolver.cache("tokyo")) == 2
    for ward in ["台東区", "渋谷区"]:
        found = await resolver.resolve("tokyo", place("確認した広場"), region(ward))
        assert len(found.candidates) == 1
        assert found.candidates[0].municipality == "東京都" + ward
    assert (await resolver.resolve("aoba", place("確認した広場"))).status == "unresolved"


async def test_same_name_master_is_disambiguated_by_region(service):
    resolver = service.resolver
    original = service.profiles["tokyo"].locations[0]
    service.profiles["tokyo"].locations.append(
        original.model_copy(
            update={
                "id": "same_name_other_ward",
                "municipality": "東京都渋谷区",
            }
        )
    )
    assert len((await resolver.resolve("tokyo", place())).candidates) == 2
    scoped = await resolver.resolve("tokyo", place(), region())
    assert [p.id for p in scoped.candidates] == [original.id]


def test_saved_region_survives_restart_without_reinterpreting_old_runs(service):
    new = service.create_run("tokyo", "地域付き", region())
    comm = Communication(
        run_id=new.id, original_text="上野公園から報告", transcription_status="done"
    )
    service.store.change(new.id, lambda r: r.communications.__setitem__(comm.id, comm))
    markdown = (service.store.run_dir(new.id) / f"{comm.id}.md").read_text()
    assert "開始時の設定・交信原文ではない" in markdown
    assert "東京都台東区" in markdown
    legacy = Run.model_validate({"demo_profile_id": "tokyo", "title": "旧記録"})
    assert legacy.region is None
    service.store.add(legacy)
    service.store.close()
    restored = Store(service.settings.data_dir)
    try:
        assert restored.get(new.id).region == region()
        assert restored.get(new.id).communications[comm.id].original_text == comm.original_text
        assert restored.get(legacy.id).region is None
    finally:
        restored.close()


async def test_location_jobs_keep_originating_run_scope(service):
    first = service.create_run("tokyo", "台東", region())
    task = service.create_task(
        first.id,
        TaskCreate(
            kind="request",
            title="確認",
            manual_reason="検証",
            actor="人",
            place=place(),
        ),
    )
    second = service.create_run("tokyo", "渋谷", region("渋谷区"))
    other = service.create_task(
        second.id,
        TaskCreate(
            kind="request",
            title="確認",
            manual_reason="検証",
            actor="人",
            place=place(),
        ),
    )
    worker = asyncio.create_task(service.location_loop())
    try:
        await asyncio.wait_for(service.location_queue.join(), 2)
        assert service.store.get(first.id).tasks[task.id].location.status == "candidates"
        assert service.store.get(second.id).tasks[other.id].location.status == "unresolved"
        assert service.store.get(first.id).tasks[task.id].place.municipality is None
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker


async def test_external_geocoder_query_and_results_are_scoped(service, monkeypatch):
    service.settings.geocoder_url = "https://geocoder.example/search"
    service.settings.geocoder_agent = "Sumradio test"
    requests = []

    async def response(client, url, **kwargs):
        requests.append(kwargs["params"])
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json=[
                {
                    "display_name": "同名広場 台東区",
                    "lat": "35.7",
                    "lon": "139.7",
                    "address": {"province": "東京都", "city": "台東区", "country_code": "jp"},
                },
                {
                    "display_name": "同名広場 渋谷区",
                    "lat": "35.6",
                    "lon": "139.7",
                    "address": {"province": "東京都", "city": "渋谷区", "country_code": "jp"},
                },
                {"display_name": "住所不明の広場", "lat": "35.7", "lon": "139.7"},
            ],
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", response)
    found = await service.resolver.resolve("local", place("同名広場"), region())
    assert len(found.candidates) == 1
    assert found.candidates[0].municipality == "東京都台東区"
    assert requests[0]["q"] == "東京都台東区 同名広場"
    assert requests[0]["countrycodes"] == "jp" and requests[0]["addressdetails"] == 1
    assert set(requests[0]) == {
        "q",
        "format",
        "limit",
        "addressdetails",
        "accept-language",
        "countrycodes",
    }
    await service.resolver.resolve("local", place("同名広場"), region())
    assert len(requests) == 1
    await service.resolver.resolve("local", place("同名広場"), region("渋谷区"))
    assert len(requests) == 2  # No cross-region cache reuse.
    await service.resolver.resolve(
        "local", place("同名広場", municipality="大阪府大阪市"), region()
    )
    assert len(requests) == 2  # Explicit conflict is not searched.


def test_external_scope_uses_response_not_query_assumption():
    assert (
        geocoder_municipality(
            {"address": {"state": "東京都", "city": "台東区", "country_code": "jp"}}
        )
        == "東京都台東区"
    )
    assert (
        geocoder_municipality(
            {"address": {"state": "東京都", "city": "台東区", "country_code": "xx"}}
        )
        == ""
    )
    assert geocoder_municipality({}) == ""


def test_region_api_and_fictional_profile_remain_compatible(tmp_path, monkeypatch):
    monkeypatch.setattr(WhisperEngine, "load", lambda self: None)
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        assert len(client.get("/api/regions").json()["prefectures"]) == 47
        response = client.post(
            "/api/runs", json={"demo_profile_id": "tokyo", "region": region().model_dump()}
        )
        assert response.status_code == 200
        run = response.json()
        assert run["region"]["municipality"] == "台東区"
        assert client.get("/api/runs").json()[0]["region"] == run["region"]
        assert len(client.get(f"/api/runs/{run['id']}/locations").json()) == 1
        assert (
            client.post(
                "/api/runs", json={"demo_profile_id": "aoba", "region": region().model_dump()}
            ).status_code
            == 422
        )
        assert client.post("/api/runs", json={"demo_profile_id": "aoba"}).status_code == 200
