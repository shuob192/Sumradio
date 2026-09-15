from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GeoModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class MapCategory(StrEnum):
    ROAD_BLOCKED = "road_blocked"
    WATER = "water"
    RESCUE = "rescue"
    SUPPLIES = "supplies"
    EVACUATION = "evacuation"
    POWER = "power"
    OTHER = "other"


class Bounds(GeoModel):
    south: float = Field(ge=-85, le=85)
    west: float = Field(ge=-180, le=180)
    north: float = Field(ge=-85, le=85)
    east: float = Field(ge=-180, le=180)

    @model_validator(mode="after")
    def ordered(self):
        if self.south >= self.north or self.west >= self.east:
            raise ValueError("対象範囲の南北・東西を確認してください")
        return self

    def contains(self, lat: float, lon: float) -> bool:
        return self.south <= lat <= self.north and self.west <= lon <= self.east


class AreaInput(GeoModel):
    name: str = Field(min_length=1, max_length=160)
    bounds: Bounds
    training: bool = True


class OperatingArea(AreaInput):
    id: str

    @classmethod
    def from_input(cls, value: AreaInput) -> "OperatingArea":
        content = json.dumps(value.model_dump(), sort_keys=True, ensure_ascii=False)
        return cls(**value.model_dump(), id=hashlib.sha256(content.encode()).hexdigest()[:20])


YOKOHAMA_AREA = AreaInput(
    name="神奈川県横浜市",
    bounds=Bounds(south=35.30, west=139.45, north=35.61, east=139.73),
)


class PlaceExpression(GeoModel):
    source_text: str = Field(min_length=1, max_length=400)
    search_name: str = Field(min_length=1, max_length=160)
    municipality: str | None = Field(default=None, max_length=100)
    district: str | None = Field(default=None, max_length=100)
    facility_type: str | None = Field(default=None, max_length=80)
    detail: str | None = Field(default=None, max_length=240)


class MapExtraction(GeoModel):
    location: PlaceExpression | None
    map_category: list[MapCategory] = Field(min_length=1, max_length=7)
    evidence_quotes: list[str] = Field(min_length=1, max_length=20)


class PlaceCandidate(GeoModel):
    id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=400)
    address: str = Field(default="", max_length=1000)
    lat: float = Field(ge=-85, le=85)
    lon: float = Field(ge=-180, le=180)
    source: Literal["master", "nominatim", "manual"]
    source_url: str = Field(default="", max_length=2000)
    license: str = Field(default="", max_length=200)
    precision: Literal["representative", "exact"] = "representative"
    bounds: Bounds | None = None


class MasterPlace(PlaceCandidate):
    aliases: list[str] = Field(default_factory=list)


class LocationStatus(StrEnum):
    UNRESOLVED = "unresolved"
    QUEUED = "queued"
    SEARCHING = "searching"
    SUGGESTED = "suggested"
    AMBIGUOUS = "ambiguous"
    CONFIRMED = "confirmed"
    NOT_FOUND = "not_found"
    FAILED = "failed"


class TaskPosition(GeoModel):
    revision: int = Field(default=0, ge=0)
    status: LocationStatus = LocationStatus.UNRESOLVED
    candidates: list[PlaceCandidate] = Field(default_factory=list, max_length=20)
    selected: PlaceCandidate | None = None
    error: str | None = None
    searched_at: str | None = None
    confirmed_by: str | None = None
    confirmed_at: str | None = None


class LocationConfirmInput(GeoModel):
    version: int = Field(ge=1)
    candidate_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=400)
    lat: float | None = Field(default=None, ge=-85, le=85)
    lon: float | None = Field(default=None, ge=-180, le=180)
    actor: str = Field(default="operator", min_length=1, max_length=120)

    @model_validator(mode="after")
    def one_method(self):
        manual = self.lat is not None and self.lon is not None and bool(self.name)
        if self.candidate_id:
            if any(value is not None for value in (self.lat, self.lon, self.name)):
                raise ValueError("候補の選択と座標入力は同時に指定できません")
        elif not manual:
            raise ValueError("候補を選ぶか、表示名・緯度・経度を入力してください")
        return self


class AreaSearchInput(GeoModel):
    query: str = Field(min_length=1, max_length=160)
