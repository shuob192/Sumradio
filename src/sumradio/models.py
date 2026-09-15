from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .geography_models import MapCategory, MapExtraction, OperatingArea, TaskPosition


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STALE = "stale"


class TranscriptionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class TaskKind(StrEnum):
    REQUEST = "request"
    SITUATION_CONFIRMATION = "situation_confirmation"


class TaskState(StrEnum):
    CANDIDATE = "candidate"
    OPEN = "open"
    DONE = "done"
    DISCARDED = "discarded"


class TaskSource(StrEnum):
    AI = "ai"
    MANUAL = "manual"


class PersonCount(StrictModel):
    description: str = Field(min_length=1, max_length=240)
    count: int | None = Field(default=None, ge=0)


class ResourceQuantity(StrictModel):
    item: str = Field(min_length=1, max_length=240)
    quantity: float | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=80)
    detail: str | None = Field(default=None, max_length=400)


class CommunicationFacts(StrictModel):
    sender: str | None = Field(default=None, max_length=240)
    recipient: str | None = Field(default=None, max_length=240)
    location: str | None = Field(default=None, max_length=400)
    reported_situation: str | None = Field(default=None, max_length=2000)
    people: list[PersonCount] = Field(default_factory=list, max_length=100)
    resources: list[ResourceQuantity] = Field(default_factory=list, max_length=100)


class PhoneticInterpretation(StrictModel):
    alphabet: Literal["japanese", "nato"]
    source_text: str = Field(min_length=1, max_length=400)
    interpreted_value: str = Field(min_length=1, max_length=120)
    evidence_quote: str = Field(min_length=1, max_length=500)


class TaskCandidate(StrictModel):
    map_info: MapExtraction
    kind: TaskKind
    title: str = Field(min_length=1, max_length=160)
    action: str = Field(min_length=1, max_length=1000)
    target: str | None = Field(default=None, max_length=400)
    location: str | None = Field(default=None, max_length=400)
    condition: str | None = Field(default=None, max_length=500)
    assignee: str | None = Field(default=None, max_length=240)
    deadline: str | None = Field(default=None, max_length=240)
    people: list[PersonCount] = Field(default_factory=list, max_length=100)
    resources: list[ResourceQuantity] = Field(default_factory=list, max_length=100)
    evidence_quotes: list[str] = Field(min_length=1, max_length=20)
    related_task_ids: list[str] = Field(default_factory=list, max_length=50)
    uncertainties: list[str] = Field(default_factory=list, max_length=50)
    negations_or_holds: list[str] = Field(default_factory=list, max_length=50)


class Confirmation(StrictModel):
    type: Literal["completion_report", "ambiguity", "contradiction", "duplicate", "negation_or_hold"]
    summary: str = Field(min_length=1, max_length=1000)
    evidence_quotes: list[str] = Field(min_length=1, max_length=20)
    related_task_ids: list[str] = Field(default_factory=list, max_length=50)


class ExtractionResponse(StrictModel):
    communication: CommunicationFacts
    phonetic_interpretations: list[PhoneticInterpretation] = Field(default_factory=list, max_length=100)
    candidates: list[TaskCandidate] = Field(default_factory=list, max_length=50)
    confirmations: list[Confirmation] = Field(default_factory=list, max_length=50)


class Correction(StrictModel):
    revision: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=30000)
    author: str = Field(min_length=1, max_length=120)
    created_at: str


class ExtractionRun(StrictModel):
    revision: int = Field(ge=0)
    status: ExtractionStatus
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    output_path: str | None = None
    candidate_ids: list[str] = Field(default_factory=list)
    duration_seconds: float | None = Field(default=None, ge=0)


class CommunicationRecord(StrictModel):
    area: OperatingArea | None = None
    id: str
    started_at: str
    ended_at: str
    created_at: str
    original_audio_path: str
    recognition_audio_path: str
    original_sample_rate: int = Field(gt=0)
    transcript_original: str = ""
    transcript_current: str = ""
    transcript_revision: int = Field(default=0, ge=0)
    corrections: list[Correction] = Field(default_factory=list)
    transcription_status: TranscriptionStatus = TranscriptionStatus.PENDING
    transcription_error: str | None = None
    transcription_duration_seconds: float | None = Field(default=None, ge=0)
    extraction_status: ExtractionStatus = ExtractionStatus.PENDING
    extraction_error: str | None = None
    extraction_runs: list[ExtractionRun] = Field(default_factory=list)
    communication_facts: CommunicationFacts | None = None
    phonetic_interpretations: list[PhoneticInterpretation] = Field(default_factory=list)
    confirmations: list[Confirmation] = Field(default_factory=list)
    version: int = Field(default=1, ge=1)


class TaskHistoryEntry(StrictModel):
    action: Literal["created", "edited", "transitioned", "location_searched", "location_confirmed", "location_reset"]
    actor: str
    created_at: str
    before: dict | None = None
    after: dict


class TaskRecord(StrictModel):
    area: OperatingArea | None = None
    map_info: MapExtraction | None = None
    map_category: list[MapCategory] = Field(default_factory=lambda: [MapCategory.OTHER])
    position: TaskPosition = Field(default_factory=TaskPosition)
    id: str
    kind: TaskKind
    state: TaskState = TaskState.CANDIDATE
    source: TaskSource
    title: str
    action: str
    target: str | None = None
    location: str | None = None
    condition: str | None = None
    assignee: str | None = None
    deadline: str | None = None
    people: list[PersonCount] = Field(default_factory=list)
    resources: list[ResourceQuantity] = Field(default_factory=list)
    evidence_quotes: list[str] = Field(default_factory=list)
    evidence_communication_ids: list[str] = Field(default_factory=list)
    related_task_ids: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    negations_or_holds: list[str] = Field(default_factory=list)
    source_transcript_revision: int | None = None
    created_at: str
    updated_at: str
    version: int = Field(default=1, ge=1)
    history: list[TaskHistoryEntry] = Field(default_factory=list)


class ManualTaskInput(StrictModel):
    map_category: list[MapCategory] = Field(default_factory=lambda: [MapCategory.OTHER], min_length=1, max_length=7)
    kind: TaskKind
    title: str = Field(min_length=1, max_length=160)
    action: str = Field(min_length=1, max_length=1000)
    target: str | None = Field(default=None, max_length=400)
    location: str | None = Field(default=None, max_length=400)
    condition: str | None = Field(default=None, max_length=500)
    assignee: str | None = Field(default=None, max_length=240)
    deadline: str | None = Field(default=None, max_length=240)
    people: list[PersonCount] = Field(default_factory=list, max_length=100)
    resources: list[ResourceQuantity] = Field(default_factory=list, max_length=100)
    evidence_communication_ids: list[str] = Field(default_factory=list, max_length=50)
    evidence_quotes: list[str] = Field(default_factory=list, max_length=20)
    actor: str = Field(default="operator", min_length=1, max_length=120)


class TaskEditInput(StrictModel):
    map_category: list[MapCategory] | None = Field(default=None, min_length=1, max_length=7)
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=160)
    action: str = Field(min_length=1, max_length=1000)
    target: str | None = Field(default=None, max_length=400)
    location: str | None = Field(default=None, max_length=400)
    condition: str | None = Field(default=None, max_length=500)
    assignee: str | None = Field(default=None, max_length=240)
    deadline: str | None = Field(default=None, max_length=240)
    people: list[PersonCount] = Field(default_factory=list, max_length=100)
    resources: list[ResourceQuantity] = Field(default_factory=list, max_length=100)
    evidence_communication_ids: list[str] = Field(default_factory=list, max_length=50)
    evidence_quotes: list[str] = Field(default_factory=list, max_length=20)
    actor: str = Field(default="operator", min_length=1, max_length=120)


class TaskTransitionInput(StrictModel):
    version: int = Field(ge=1)
    target_state: TaskState
    actor: str = Field(default="operator", min_length=1, max_length=120)


class CorrectionInput(StrictModel):
    version: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=30000)
    author: str = Field(default="operator", min_length=1, max_length=120)


class RecordingStartInput(StrictModel):
    device_id: int | None = None


class VersionInput(StrictModel):
    version: int = Field(ge=1)
