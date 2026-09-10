from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def new_id() -> str:
    return uuid4().hex


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


TaskStatus = Literal["candidate", "open", "done", "dismissed"]


class EventEdit(StrictModel):
    actor: str = Field(min_length=1, max_length=100)
    status: Literal["needs_review", "confirmed"] | None = None
    corrected_text: str | None = Field(default=None, max_length=10000)

    @field_validator("actor")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("確認者を入力してください")
        return value.strip()


class TaskCreate(StrictModel):
    actor: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=1000)
    source_event_ids: list[str] = Field(min_length=1, max_length=50)
    place: str | None = Field(default=None, max_length=200)
    target: str | None = Field(default=None, max_length=200)
    assignee: str | None = Field(default=None, max_length=200)
    due_at: str | None = Field(default=None, max_length=200)

    @field_validator("actor", "title")
    @classmethod
    def nonblank(cls, value: str) -> str:
        return EventEdit.nonblank(value)


class TaskEdit(StrictModel):
    actor: str = Field(min_length=1, max_length=100)
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    status: TaskStatus | None = None
    place: str | None = Field(default=None, max_length=200)
    target: str | None = Field(default=None, max_length=200)
    assignee: str | None = Field(default=None, max_length=200)
    due_at: str | None = Field(default=None, max_length=200)
    source_event_ids: list[str] | None = Field(default=None, min_length=1, max_length=50)

    @field_validator("actor", "title")
    @classmethod
    def nonblank(cls, value: str | None) -> str | None:
        return EventEdit.nonblank(value) if value is not None else value


class CaptureStart(StrictModel):
    device: int | str
    mode: Literal["auto", "manual"] = "auto"
