from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .regions import PREFECTURES, normalize, split_area


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, json_schema_serialization_defaults_required=True
    )


class Kind(StrEnum):
    request = "request"
    situation_confirmation = "situation_confirmation"


class Status(StrEnum):
    candidate = "candidate"
    unhandled = "unhandled"
    completed = "completed"
    discarded = "discarded"


class Category(StrEnum):
    road_blocked = "road_blocked"
    water = "water"
    rescue = "rescue"
    injury = "injury"
    medical = "medical"
    fire = "fire"
    flood = "flood"
    building_damage = "building_damage"
    communication = "communication"
    missing_person = "missing_person"
    sanitation = "sanitation"
    supplies = "supplies"
    evacuation = "evacuation"
    power = "power"
    other = "other"


class Evidence(StrictModel):
    communication_id: str
    revision: int = Field(ge=1)
    quote: str = Field(min_length=1)


class Quantity(StrictModel):
    value: float | None
    unit: str | None
    description: str


class PlaceExpression(StrictModel):
    expression: str = Field(
        description="交信内で明示された地名・施設の固有名を省略せず、入口等の詳細地点と合わせた表現。"
    )
    search_name: str = Field(
        description="原文で明示された固有名を含む地名・施設名の全体。単に『公園』『集会所』のような施設種別に短縮しない。"
    )
    municipality: str | None
    detail: str | None


class PhoneticInterpretation(StrictModel):
    original: str
    characters: list[str]
    interpreted: str | None
    evidence: Evidence


class TranscriptInterpretation(StrictModel):
    possible_meaning: str | None
    reason: str
    evidence: Evidence


class Notice(StrictModel):
    kind: Literal["uncertain", "duplicate", "completion", "negation", "contradiction"]
    text: str
    related_task_ids: list[str]
    evidence: list[Evidence]


class ExtractedTask(StrictModel):
    kind: Kind
    title: str = Field(min_length=1)
    description: str
    place: PlaceExpression | None
    map_categories: list[Category] = Field(min_length=1)
    category_evidence: list[Evidence] = Field(min_length=1)
    quantities: list[Quantity]
    assignee: str | None
    deadline: str | None
    evidence: list[Evidence] = Field(min_length=1)
    related_task_ids: list[str]
    uncertainties: list[str]


class Extraction(StrictModel):
    sender: str | None
    recipient: str | None
    situation: str | None
    people: list[Quantity]
    phonetic_interpretations: list[PhoneticInterpretation]
    transcript_interpretations: list[TranscriptInterpretation] = Field(default_factory=list)
    tasks: list[ExtractedTask]
    notices: list[Notice]


class Location(StrictModel):
    id: str
    name: str
    municipality: str
    aliases: list[str] = Field(default_factory=list)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    precision: Literal["representative", "specific"] = "representative"
    detail: str = ""
    source: str
    source_url: str = ""
    checked_at: str = ""


class InterpretationLocationRef(StrictModel):
    communication_id: str
    revision: int = Field(ge=1)
    interpretation_index: int = Field(ge=0)


class ApprovedLocationInterpretation(InterpretationLocationRef):
    possible_meaning: str
    original_expression: str
    evidence: Evidence


class InterpretationLocationOption(InterpretationLocationRef):
    task_id: str
    task_version: int
    location: Location


class LocationState(StrictModel):
    status: Literal["pending", "unresolved", "candidates", "confirmed"] = "pending"
    candidates: list[Location] = Field(default_factory=list)
    confirmed: Location | None = None
    confirmed_by: str | None = None
    confirmed_at: str | None = None
    reason: str | None = None
    interpretation: ApprovedLocationInterpretation | None = None


class History(StrictModel):
    id: str = Field(default_factory=lambda: uid("event"))
    at: str = Field(default_factory=now)
    actor: str
    action: str
    target_id: str
    before: dict | None = None
    after: dict | None = None


class Correction(StrictModel):
    revision: int
    text: str
    actor: str
    at: str = Field(default_factory=now)


class Communication(StrictModel):
    id: str = Field(default_factory=lambda: uid("comm"))
    run_id: str
    received_at: str = Field(default_factory=now)
    version: int = 1
    revision: int = 1
    original_text: str = ""
    corrections: list[Correction] = Field(default_factory=list)
    original_audio: str | None = None
    recognition_audio: str | None = None
    transcription_status: Literal[
        "recording", "queued", "running", "done", "failed", "interrupted"
    ] = "queued"
    extraction_status: Literal["waiting", "running", "done", "failed"] = "waiting"
    extraction: Extraction | None = None
    extraction_revision: int | None = None
    extraction_attempts: list[dict] = Field(default_factory=list)
    error: str | None = None
    metrics: dict[str, float | int | str] = Field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.corrections[-1].text if self.corrections else self.original_text


class Task(ExtractedTask):
    id: str = Field(default_factory=lambda: uid("task"))
    run_id: str
    version: int = 1
    status: Status = Status.candidate
    origin: Literal["ai", "manual"]
    manual_reason: str | None = None
    # Manual tasks can have a human explanation without a radio reference.
    evidence: list[Evidence] = Field(default_factory=list)
    category_evidence: list[Evidence] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)
    location: LocationState = Field(default_factory=LocationState)
    notices: list[Notice] = Field(default_factory=list)


class Region(StrictModel):
    prefecture: str = Field(min_length=3, max_length=10)
    municipality: str = Field(default="", max_length=50)

    @field_validator("prefecture", "municipality")
    @classmethod
    def trim_region(cls, value):
        return normalize(value)

    @model_validator(mode="after")
    def validate_region(self):
        if self.prefecture not in PREFECTURES:
            raise ValueError("都道府県を選択してください")
        prefecture, municipality = split_area(self.municipality)
        if prefecture and prefecture != self.prefecture:
            raise ValueError("市区町村に指定された都道府県が一致しません")
        if municipality and (
            not municipality.endswith(("市", "区", "町", "村"))
            or any(c in municipality for c in "・、,;/\\<>\n")
        ):
            raise ValueError("市区町村は『台東区』『横浜市港北区』のように1地域を入力してください")
        self.municipality = municipality
        return self

    @property
    def label(self) -> str:
        return self.prefecture + self.municipality


class Run(StrictModel):
    id: str = Field(default_factory=lambda: uid("run"))
    demo_profile_id: str
    title: str
    region: Region | None = None
    created_at: str = Field(default_factory=now)
    version: int = 1
    communications: dict[str, Communication] = Field(default_factory=dict)
    tasks: dict[str, Task] = Field(default_factory=dict)
    history: list[History] = Field(default_factory=list)


class RunView(Run):
    # Read-only suggestions; never persisted as confirmed locations or plotted before approval.
    interpretation_locations: list[InterpretationLocationOption] = Field(default_factory=list)


class Profile(StrictModel):
    id: str
    name: str
    training: bool
    external_geocoding: bool
    locations: list[Location]


class RunCreate(StrictModel):
    demo_profile_id: str
    title: str = Field(default="", max_length=100)
    region: Region | None = None


class Versioned(StrictModel):
    expected_version: int = Field(ge=1)
    actor: str = Field(min_length=1, max_length=100)


class CorrectRequest(Versioned):
    text: str = Field(min_length=1, max_length=100000)


class DisplayMetric(Versioned):
    kind: Literal["final", "subtitle"]
    displayed_at_epoch: float = Field(gt=0)
    captured_at_epoch: float | None = None


class TransitionRequest(Versioned):
    status: Status


class TaskContent(StrictModel):
    kind: Kind
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    place: PlaceExpression | None = None
    map_categories: list[Category] = Field(default_factory=lambda: [Category.other], min_length=1)
    quantities: list[Quantity] = Field(default_factory=list)
    assignee: str | None = None
    deadline: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    related_task_ids: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    manual_reason: str = Field(min_length=1, max_length=10000)


class TaskCreate(TaskContent):
    actor: str = Field(min_length=1, max_length=100)


class TaskEdit(TaskContent, Versioned):
    pass


class ConfirmLocation(Versioned):
    candidate_id: str | None = None
    manual: Location | None = None


class ConfirmInterpretationLocation(Versioned, InterpretationLocationRef):
    location_id: str


class RecordingStart(StrictModel):
    device: int | None = None
    manual_mode: bool = False
