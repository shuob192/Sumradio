import asyncio
import hashlib
import json
import time
import unicodedata
from pathlib import Path

import httpx

from .config import Settings
from .models import Location, LocationState, PlaceExpression, Profile, Region
from .regions import geocoder_municipality, in_region, region_conflicts
from .storage import atomic_json


def norm(value: str | None) -> str:
    return "".join(unicodedata.normalize("NFKC", value or "").split()).casefold()


def load_profiles(root: Path) -> dict[str, Profile]:
    result = {}
    for path in sorted((root / "data/profiles").glob("*.json")):
        profile = Profile.model_validate_json(path.read_text())
        if profile.id in result or len({p.id for p in profile.locations}) != len(profile.locations):
            raise ValueError("位置マスターに重複IDがあります")
        if profile.training and profile.external_geocoding:
            raise ValueError("デモの外部地名検索は許可されません")
        result[profile.id] = profile
    return result


def matches(place: PlaceExpression, location: Location) -> bool:
    municipality = norm(place.municipality)
    if municipality and municipality not in norm(location.municipality):
        return False
    # Punctuation can separate a facility and its registered detail; suffixes are retained.
    separators = str.maketrans("", "", "、,・")
    names = {norm(n).translate(separators) for n in [location.name, *location.aliases]}
    expression = norm(place.expression).translate(separators)
    # Detail must be explicitly registered; dropping an entrance in search_name cannot match.
    if place.detail and norm(place.detail) != norm(location.detail):
        explicitly_registered = (
            location.precision == "specific"
            and expression in names
            and norm(place.detail) in expression
        )
        if not explicitly_registered:
            return False
    if expression in names:
        return True
    # A shortened original phrase can use its fully named search field, but only when the
    # exact detailed point is registered and the phrase is a literal suffix of that name.
    # e.g. "集会所玄関" + the explicitly extracted named hall; never an unknown north entrance.
    search = norm(place.search_name).translate(separators)
    if (
        location.precision == "specific"
        and place.detail
        and norm(place.detail) == norm(location.detail)
        and search in names
        and (search + norm(place.detail)).endswith(expression)
    ):
        return True
    # Removing an administrative prefix is allowed; dropping a building/entrance suffix is not.
    prefixes = {norm(location.municipality), municipality}
    prefixes.update(norm(location.municipality).split("・"))
    if municipality.startswith("東京都"):
        prefixes.add(municipality.removeprefix("東京都"))
    return any(
        prefix and expression in {prefix + name, prefix + "の" + name}
        for prefix in prefixes
        for name in names
    )


class Resolver:
    def __init__(self, settings: Settings, profiles: dict[str, Profile]):
        self.settings, self.profiles = settings, profiles
        self.gate = asyncio.Lock()
        self.last_request = 0.0

    def cache_path(self, profile_id: str) -> Path:
        if profile_id not in self.profiles:
            raise KeyError(profile_id)
        return self.settings.data_dir / "location-cache" / f"{profile_id}.json"

    def cache(self, profile_id: str) -> list[Location]:
        path = self.cache_path(profile_id)
        if not path.exists():
            return []
        return [Location.model_validate(x) for x in json.loads(path.read_text())]

    def remember(
        self,
        profile_id: str,
        place: PlaceExpression | None,
        location: Location,
        region: Region | None = None,
    ):
        if place is None:
            return
        entries = self.cache(profile_id)
        entry = location.model_copy(deep=True)
        entry.aliases = list(set(entry.aliases + [place.expression]))
        entry.detail = place.detail or entry.detail
        identity = place.model_dump()
        if region:
            identity = {"place": identity, "region": region.model_dump()}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
        entry.id = f"cache_{key}"
        entries = [x for x in entries if x.id != entry.id] + [entry]
        atomic_json(self.cache_path(profile_id), [x.model_dump(mode="json") for x in entries])

    def registered_locations(self, profile_id: str, region: Region | None = None) -> list[Location]:
        return [x for x in self.profiles[profile_id].locations if in_region(region, x.municipality)]

    async def resolve(
        self, profile_id: str, place: PlaceExpression | None, region: Region | None = None
    ) -> LocationState:
        if place is None:
            return LocationState(status="unresolved", reason="地点表現がありません")
        profile = self.profiles[profile_id]
        if region_conflicts(region, place.municipality):
            return LocationState(
                status="unresolved",
                reason=f"対象地域（{region.label}）と交信の自治体が一致しません。原文・場所を確認してください",
            )
        for entries in [profile.locations, self.cache(profile_id)]:
            candidates = [
                x for x in entries if in_region(region, x.municipality) and matches(place, x)
            ]
            if candidates:
                return LocationState(
                    status="candidates",
                    candidates=candidates,
                    reason=f"対象地域（{region.label}）内の候補です。位置を確認してください"
                    if region
                    else "位置を確認してください",
                )
        if profile.training or not profile.external_geocoding or not self.settings.geocoder_url:
            return LocationState(
                status="unresolved",
                reason=(f"対象地域（{region.label}）内の" if region else "")
                + "事前登録された地点に一致しません。手動で指定できます",
            )
        if not self.settings.geocoder_agent:
            return LocationState(status="unresolved", reason="地名検索の識別情報が未設定です")
        # The geocoder receives only the location fields, never a transcript or task body.
        query = " ".join(
            dict.fromkeys(
                filter(
                    None, [region.label if region else None, place.municipality, place.search_name]
                )
            )
        )
        params = {
            "q": query,
            "format": "jsonv2",
            "limit": 5,
            "addressdetails": 1,
            "accept-language": "ja",
        }
        if region:
            params["countrycodes"] = "jp"
        key = hashlib.sha256(
            json.dumps([profile_id, self.settings.geocoder_url, params], sort_keys=True).encode()
        ).hexdigest()
        path = self.settings.data_dir / "geocoder-cache" / f"{key}.json"
        async with self.gate:
            if path.exists():
                raw = json.loads(path.read_text())
            else:
                await asyncio.sleep(max(0, 1.05 - (time.monotonic() - self.last_request)))
                self.last_request = time.monotonic()
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.get(
                        self.settings.geocoder_url,
                        params=params,
                        headers={"User-Agent": self.settings.geocoder_agent},
                    )
                    response.raise_for_status()
                    raw = response.json()
                if not isinstance(raw, list):
                    raise ValueError("地名検索の形式が不正です")
                atomic_json(path, raw)
        candidates = [
            Location(
                id=f"geo_{key[:10]}_{i}",
                name=x["display_name"],
                municipality=geocoder_municipality(x),
                lat=float(x["lat"]),
                lon=float(x["lon"]),
                source="geocoder",
                source_url=self.settings.geocoder_url,
            )
            for i, x in enumerate(raw[:5])
            if not region or in_region(region, geocoder_municipality(x))
        ]
        return LocationState(
            status="candidates" if candidates else "unresolved",
            candidates=candidates,
            reason="外部検索の位置候補です。地点の範囲と詳細を確認してください"
            if candidates
            else "対象地域内の住所を確認できる検索結果がありません。手動で指定できます",
        )
