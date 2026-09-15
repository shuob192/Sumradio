from __future__ import annotations

import json
import os
import threading
import uuid
import wave
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .geography_models import (
    AreaInput, LocationConfirmInput, LocationStatus, OperatingArea,
    PlaceCandidate, TaskPosition,
)

from .models import (
    CommunicationRecord,
    Confirmation,
    Correction,
    ExtractionResponse,
    ExtractionRun,
    ExtractionStatus,
    ManualTaskInput,
    TaskEditInput,
    TaskHistoryEntry,
    TaskRecord,
    TaskSource,
    TaskState,
    TaskTransitionInput,
    TranscriptionStatus,
)


class NotFoundError(KeyError):
    pass


class VersionConflictError(ValueError):
    pass


class InvalidTransitionError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class DataStore:
    """File-backed, single-process store with atomic JSON and Markdown writes."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.communications_dir = self.root / "communications"
        self.tasks_dir = self.root / "tasks"
        self.runtime_dir = self.root / "runtime"
        self.codex_workspace_dir = self.runtime_dir / "codex-workspace"
        self._lock = threading.RLock()
        self._communications: dict[str, CommunicationRecord] = {}
        self._tasks: dict[str, TaskRecord] = {}
        self.diagnostics: list[str] = []
        self.active_area: OperatingArea | None = None

    def initialize(self) -> None:
        for path in (
            self.communications_dir,
            self.tasks_dir,
            self.runtime_dir,
            self.codex_workspace_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._communications.clear()
            self._tasks.clear()
            self.diagnostics.clear()
            area_path = self.root / "geography" / "area.json"
            if area_path.exists():
                try:
                    self.active_area = OperatingArea.model_validate_json(area_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    self.diagnostics.append(f"{area_path}: {exc}")
            for path in sorted(self.communications_dir.glob("*/metadata.json")):
                try:
                    record = CommunicationRecord.model_validate_json(path.read_text(encoding="utf-8"))
                    self._communications[record.id] = record
                except Exception as exc:  # corrupt data must remain untouched
                    self.diagnostics.append(f"{path}: {exc}")
            for path in sorted(self.tasks_dir.glob("*.json")):
                try:
                    task = TaskRecord.model_validate_json(path.read_text(encoding="utf-8"))
                    self._tasks[task.id] = task
                except Exception as exc:
                    self.diagnostics.append(f"{path}: {exc}")

    @staticmethod
    def _write_bytes_atomic(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp.write_bytes(payload)
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()

    @classmethod
    def _write_text_atomic(cls, path: Path, text: str) -> None:
        cls._write_bytes_atomic(path, text.encode("utf-8"))

    @classmethod
    def _write_json_atomic(cls, path: Path, payload: Any) -> None:
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        cls._write_text_atomic(path, text)

    @classmethod
    def _write_wav_atomic(cls, path: Path, pcm: bytes, sample_rate: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with wave.open(str(temp), "wb") as target:
                target.setnchannels(1)
                target.setsampwidth(2)
                target.setframerate(sample_rate)
                target.writeframes(pcm)
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()

    def _comm_dir(self, communication_id: str) -> Path:
        return self.communications_dir / communication_id

    def _comm_path(self, communication_id: str) -> Path:
        return self._comm_dir(communication_id) / "metadata.json"

    def _task_path(self, task_id: str) -> Path:
        return self.tasks_dir / f"{task_id}.json"

    def _save_communication(self, record: CommunicationRecord) -> None:
        self._write_json_atomic(self._comm_path(record.id), record.model_dump(mode="json"))
        self._write_transcript_markdown(record)
        self._communications[record.id] = record

    def _save_task(self, task: TaskRecord) -> None:
        self._write_json_atomic(self._task_path(task.id), task.model_dump(mode="json"))
        self._tasks[task.id] = task

    def _write_transcript_markdown(self, record: CommunicationRecord) -> None:
        lines = [
            f"# 交信 {record.id}",
            "",
            f"- 受信開始: {record.started_at}",
            f"- 受信終了: {record.ended_at}",
            f"- 原音: [original.wav](original.wav)",
            f"- 認識用音声: [recognition.wav](recognition.wav)",
            "",
            "## Whisper認識原文",
            "",
            record.transcript_original or "（文字起こし待ち）",
            "",
            "## 現在の確認文",
            "",
            record.transcript_current or "（文字起こし待ち）",
            "",
            "## 訂正履歴",
            "",
        ]
        if not record.corrections:
            lines.append("（訂正なし）")
        for correction in record.corrections:
            lines.extend(
                [
                    f"### 版 {correction.revision}",
                    "",
                    f"- 修正者: {correction.author}",
                    f"- 修正時刻: {correction.created_at}",
                    "",
                    correction.text,
                    "",
                ]
            )
        self._write_text_atomic(self._comm_dir(record.id) / "transcript.md", "\n".join(lines).rstrip() + "\n")

    def create_communication(
        self,
        *,
        original_pcm: bytes,
        original_sample_rate: int,
        recognition_pcm: bytes,
        started_at: str,
        ended_at: str,
        area: OperatingArea | None = None,
    ) -> CommunicationRecord:
        with self._lock:
            communication_id = _new_id("comm")
            directory = self._comm_dir(communication_id)
            directory.mkdir(parents=True, exist_ok=False)
            original = directory / "original.wav"
            recognition = directory / "recognition.wav"
            self._write_wav_atomic(original, original_pcm, original_sample_rate)
            self._write_wav_atomic(recognition, recognition_pcm, 16000)
            record = CommunicationRecord(
                area=area,
                id=communication_id,
                started_at=started_at,
                ended_at=ended_at,
                created_at=utc_now(),
                original_audio_path=str(original.relative_to(self.root)),
                recognition_audio_path=str(recognition.relative_to(self.root)),
                original_sample_rate=original_sample_rate,
            )
            self._save_communication(record)
            return deepcopy(record)

    def get_communication(self, communication_id: str) -> CommunicationRecord:
        with self._lock:
            try:
                return deepcopy(self._communications[communication_id])
            except KeyError as exc:
                raise NotFoundError(communication_id) from exc

    def list_communications(self) -> list[CommunicationRecord]:
        with self._lock:
            return [
                deepcopy(item)
                for item in sorted(self._communications.values(), key=lambda value: value.started_at, reverse=True)
            ]

    def set_transcription_running(self, communication_id: str) -> CommunicationRecord:
        with self._lock:
            record = self.get_communication(communication_id)
            record.transcription_status = TranscriptionStatus.RUNNING
            record.transcription_error = None
            record.version += 1
            self._save_communication(record)
            return deepcopy(record)

    def complete_transcription(self, communication_id: str, transcript: str, duration: float) -> CommunicationRecord:
        with self._lock:
            record = self.get_communication(communication_id)
            record.transcript_original = transcript.strip()
            record.transcript_current = transcript.strip()
            record.transcription_status = TranscriptionStatus.SUCCEEDED
            record.transcription_error = None
            record.transcription_duration_seconds = max(0.0, duration)
            record.extraction_status = ExtractionStatus.PENDING
            record.version += 1
            self._save_communication(record)
            return deepcopy(record)

    def fail_transcription(self, communication_id: str, error: str) -> CommunicationRecord:
        with self._lock:
            record = self.get_communication(communication_id)
            record.transcription_status = TranscriptionStatus.FAILED
            record.transcription_error = error[:2000]
            record.version += 1
            self._save_communication(record)
            return deepcopy(record)

    def correct_communication(self, communication_id: str, *, text: str, author: str, version: int) -> CommunicationRecord:
        with self._lock:
            record = self.get_communication(communication_id)
            if record.version != version:
                raise VersionConflictError(f"画面の版 {version} は現在の版 {record.version} と一致しません")
            record.transcript_revision += 1
            record.transcript_current = text.strip()
            record.corrections.append(
                Correction(
                    revision=record.transcript_revision,
                    text=record.transcript_current,
                    author=author,
                    created_at=utc_now(),
                )
            )
            record.extraction_status = ExtractionStatus.PENDING
            record.extraction_error = None
            record.version += 1
            self._save_communication(record)
            # Stop pending lookups based on the previous transcript. Keep human
            # confirmations and completed lookup results available for review.
            for task in self._tasks.values():
                if (communication_id in task.evidence_communication_ids
                        and task.source_transcript_revision is not None
                        and task.source_transcript_revision != record.transcript_revision
                        and task.position.status in {LocationStatus.QUEUED, LocationStatus.SEARCHING}):
                    before = task.position.model_dump(mode="json")
                    task.position = TaskPosition(
                        revision=task.position.revision + 1,
                        error="根拠交信が訂正されました。訂正後のタスクまたは場所を確認してください。",
                    )
                    task.version += 1
                    task.updated_at = utc_now()
                    task.history.append(TaskHistoryEntry(
                        action="location_reset", actor=author, created_at=task.updated_at,
                        before=before, after=task.position.model_dump(mode="json"),
                    ))
                    self._save_task(task)
            return deepcopy(record)

    def begin_extraction(self, communication_id: str, revision: int) -> tuple[CommunicationRecord, bool]:
        with self._lock:
            record = self.get_communication(communication_id)
            for run in record.extraction_runs:
                if run.revision == revision and run.status == ExtractionStatus.SUCCEEDED:
                    return deepcopy(record), False
            now = utc_now()
            record.extraction_runs.append(
                ExtractionRun(revision=revision, status=ExtractionStatus.RUNNING, started_at=now)
            )
            if revision == record.transcript_revision:
                record.extraction_status = ExtractionStatus.RUNNING
                record.extraction_error = None
            record.version += 1
            self._save_communication(record)
            return deepcopy(record), True

    def fail_extraction(self, communication_id: str, revision: int, error: str) -> CommunicationRecord:
        with self._lock:
            record = self.get_communication(communication_id)
            status = ExtractionStatus.FAILED if revision == record.transcript_revision else ExtractionStatus.STALE
            for run in reversed(record.extraction_runs):
                if run.revision == revision and run.status == ExtractionStatus.RUNNING:
                    run.status = status
                    run.completed_at = utc_now()
                    run.error = error[:2000]
                    break
            if revision == record.transcript_revision:
                record.extraction_status = ExtractionStatus.FAILED
                record.extraction_error = error[:2000]
            record.version += 1
            self._save_communication(record)
            return deepcopy(record)

    @staticmethod
    def _deterministic_task_id(communication_id: str, revision: int, index: int) -> str:
        value = uuid.uuid5(uuid.NAMESPACE_URL, f"sumradio:{communication_id}:{revision}:{index}")
        return f"task_{value.hex}"

    def commit_extraction(
        self,
        communication_id: str,
        revision: int,
        result: ExtractionResponse,
        duration: float,
    ) -> tuple[CommunicationRecord, list[TaskRecord], bool]:
        with self._lock:
            record = self.get_communication(communication_id)
            output_path = self._comm_dir(communication_id) / "extractions" / f"revision-{revision}.json"
            self._write_json_atomic(output_path, result.model_dump(mode="json"))
            is_current = revision == record.transcript_revision
            task_records: list[TaskRecord] = []
            candidate_ids: list[str] = []
            if is_current:
                for index, candidate in enumerate(result.candidates):
                    task_id = self._deterministic_task_id(communication_id, revision, index)
                    candidate_ids.append(task_id)
                    if task_id in self._tasks:
                        task_records.append(deepcopy(self._tasks[task_id]))
                        continue
                    now = utc_now()
                    task = TaskRecord(
                        area=record.area,
                        map_info=candidate.map_info,
                        map_category=candidate.map_info.map_category,
                        id=task_id,
                        kind=candidate.kind,
                        state=TaskState.CANDIDATE,
                        source=TaskSource.AI,
                        title=candidate.title,
                        action=candidate.action,
                        target=candidate.target,
                        location=candidate.location,
                        condition=candidate.condition,
                        assignee=candidate.assignee,
                        deadline=candidate.deadline,
                        people=candidate.people,
                        resources=candidate.resources,
                        evidence_quotes=candidate.evidence_quotes,
                        evidence_communication_ids=[communication_id],
                        related_task_ids=candidate.related_task_ids,
                        uncertainties=candidate.uncertainties,
                        negations_or_holds=candidate.negations_or_holds,
                        source_transcript_revision=revision,
                        created_at=now,
                        updated_at=now,
                    )
                    task.history.append(
                        TaskHistoryEntry(
                            action="created",
                            actor="codex",
                            created_at=now,
                            after={"state": task.state.value, "source": task.source.value},
                        )
                    )
                    self._save_task(task)
                    task_records.append(deepcopy(task))
                record.communication_facts = result.communication
                record.phonetic_interpretations = result.phonetic_interpretations
                record.confirmations = result.confirmations
                record.extraction_status = ExtractionStatus.SUCCEEDED
                record.extraction_error = None
            completed_at = utc_now()
            for run in reversed(record.extraction_runs):
                if run.revision == revision and run.status == ExtractionStatus.RUNNING:
                    run.status = ExtractionStatus.SUCCEEDED if is_current else ExtractionStatus.STALE
                    run.completed_at = completed_at
                    run.output_path = str(output_path.relative_to(self.root))
                    run.candidate_ids = candidate_ids
                    run.duration_seconds = max(0.0, duration)
                    break
            record.version += 1
            self._save_communication(record)
            return deepcopy(record), task_records, is_current

    def get_task(self, task_id: str) -> TaskRecord:
        with self._lock:
            try:
                return deepcopy(self._tasks[task_id])
            except KeyError as exc:
                raise NotFoundError(task_id) from exc

    def list_tasks(self, *, include_discarded: bool = False) -> list[TaskRecord]:
        with self._lock:
            values = self._tasks.values()
            if not include_discarded:
                values = [item for item in values if item.state != TaskState.DISCARDED]
            return [deepcopy(item) for item in sorted(values, key=lambda value: value.created_at)]

    def create_manual_task(self, data: ManualTaskInput) -> TaskRecord:
        with self._lock:
            unknown = [item for item in data.evidence_communication_ids if item not in self._communications]
            if unknown:
                raise NotFoundError(", ".join(unknown))
            now = utc_now()
            task = TaskRecord(
                area=deepcopy(self.active_area),
                map_category=data.map_category,
                id=_new_id("task"),
                kind=data.kind,
                state=TaskState.CANDIDATE,
                source=TaskSource.MANUAL,
                title=data.title.strip(),
                action=data.action.strip(),
                target=data.target,
                location=data.location,
                condition=data.condition,
                assignee=data.assignee,
                deadline=data.deadline,
                people=data.people,
                resources=data.resources,
                evidence_communication_ids=data.evidence_communication_ids,
                evidence_quotes=data.evidence_quotes,
                created_at=now,
                updated_at=now,
            )
            task.history.append(
                TaskHistoryEntry(
                    action="created",
                    actor=data.actor,
                    created_at=now,
                    after={"state": task.state.value, "source": task.source.value},
                )
            )
            self._save_task(task)
            return deepcopy(task)

    def edit_task(self, task_id: str, data: TaskEditInput) -> TaskRecord:
        with self._lock:
            task = self.get_task(task_id)
            if task.version != data.version:
                raise VersionConflictError(f"画面の版 {data.version} は現在の版 {task.version} と一致しません")
            unknown = [item for item in data.evidence_communication_ids if item not in self._communications]
            if unknown:
                raise NotFoundError(", ".join(unknown))
            before = task.model_dump(mode="json", exclude={"history"})
            if task.location != data.location:
                task.position = TaskPosition(revision=task.position.revision + 1)
                task.map_info = None
            if data.map_category is not None:
                task.map_category = list(dict.fromkeys(data.map_category))
            for field in (
                "title",
                "action",
                "target",
                "location",
                "condition",
                "assignee",
                "deadline",
                "people",
                "resources",
                "evidence_communication_ids",
                "evidence_quotes",
            ):
                setattr(task, field, getattr(data, field))
            task.updated_at = utc_now()
            task.version += 1
            task.history.append(
                TaskHistoryEntry(
                    action="edited",
                    actor=data.actor,
                    created_at=task.updated_at,
                    before=before,
                    after=task.model_dump(mode="json", exclude={"history"}),
                )
            )
            self._save_task(task)
            return deepcopy(task)

    def set_area(self, data: AreaInput) -> OperatingArea:
        with self._lock:
            area = OperatingArea.from_input(data)
            self._write_json_atomic(self.root / "geography" / "area.json", area.model_dump(mode="json"))
            self.active_area = area
            return deepcopy(area)

    def evidence_is_current(self, task: TaskRecord) -> bool:
        with self._lock:
            return all(
                task.source_transcript_revision is None
                or self._communications.get(cid) is None
                or self._communications[cid].transcript_revision == task.source_transcript_revision
                for cid in task.evidence_communication_ids
            )

    def begin_location(self, task_id: str, *, version: int | None = None) -> TaskRecord | None:
        with self._lock:
            task = self.get_task(task_id)
            if version is not None and task.version != version:
                raise VersionConflictError("タスクが更新されています。最新の表示から再試行してください")
            if task.state not in {TaskState.CANDIDATE, TaskState.OPEN}:
                return None
            if not self.evidence_is_current(task):
                return None
            if not task.area:
                return None
            if version is None and task.position.status == LocationStatus.CONFIRMED:
                return None
            # A manual re-search deliberately invalidates a previously selected location.
            old_position = task.position.model_dump(mode="json")
            task.position = TaskPosition(revision=task.position.revision + 1, status=LocationStatus.QUEUED)
            task.version += 1
            task.updated_at = utc_now()
            task.history.append(TaskHistoryEntry(
                action="location_reset", actor="operator" if version is not None else "resolver",
                created_at=task.updated_at, before=old_position, after=task.position.model_dump(mode="json"),
            ))
            self._save_task(task)
            return deepcopy(task)

    def finish_location(self, started: TaskRecord, places: list[PlaceCandidate], error: str | None = None) -> TaskRecord | None:
        with self._lock:
            task = self.get_task(started.id)
            if (
                task.version != started.version
                or task.position.revision != started.position.revision
                or task.area != started.area
                or task.source_transcript_revision != started.source_transcript_revision
                or not self.evidence_is_current(task)
                or task.state not in {TaskState.CANDIDATE, TaskState.OPEN}
            ):
                return None
            before = task.position.model_dump(mode="json")
            task.position.candidates = places
            task.position.selected = places[0] if len(places) == 1 else None
            task.position.status = (
                LocationStatus.FAILED if error else LocationStatus.NOT_FOUND if not places
                else LocationStatus.SUGGESTED if len(places) == 1 else LocationStatus.AMBIGUOUS
            )
            task.position.error = error
            task.position.searched_at = utc_now()
            task.version += 1
            task.updated_at = utc_now()
            task.history.append(TaskHistoryEntry(
                action="location_searched", actor="resolver", created_at=task.updated_at,
                before=before, after=task.position.model_dump(mode="json"),
            ))
            self._save_task(task)
            return deepcopy(task)

    def confirm_location(self, task_id: str, data: LocationConfirmInput) -> TaskRecord:
        with self._lock:
            task = self.get_task(task_id)
            if task.version != data.version:
                raise VersionConflictError("タスクが更新されています。最新の位置候補を確認してください")
            before = task.position.model_dump(mode="json")
            if data.candidate_id:
                selected = next((place for place in task.position.candidates if place.id == data.candidate_id), None)
                if selected is None:
                    raise ValueError("現在の位置候補にない地点は選択できません")
            else:
                selected = PlaceCandidate(
                    id=_new_id("manual"), name=data.name, lat=data.lat, lon=data.lon,
                    source="manual", precision="exact",
                )
            if task.area and not task.area.bounds.contains(selected.lat, selected.lon):
                raise ValueError("対象地域の範囲外です。地域または座標を確認してください")
            now = utc_now()
            task.position = TaskPosition(
                revision=task.position.revision + 1, status=LocationStatus.CONFIRMED,
                candidates=task.position.candidates, selected=selected,
                searched_at=task.position.searched_at, confirmed_by=data.actor, confirmed_at=now,
            )
            task.updated_at, task.version = now, task.version + 1
            task.history.append(TaskHistoryEntry(
                action="location_confirmed", actor=data.actor, created_at=now,
                before=before, after=task.position.model_dump(mode="json"),
            ))
            self._save_task(task)
            return deepcopy(task)

    def transition_task(self, task_id: str, data: TaskTransitionInput) -> TaskRecord:
        allowed = {
            TaskState.CANDIDATE: {TaskState.OPEN, TaskState.DISCARDED},
            TaskState.OPEN: {TaskState.DONE},
            TaskState.DONE: set(),
            TaskState.DISCARDED: set(),
        }
        with self._lock:
            task = self.get_task(task_id)
            if task.version != data.version:
                raise VersionConflictError(f"画面の版 {data.version} は現在の版 {task.version} と一致しません")
            if data.target_state not in allowed[task.state]:
                raise InvalidTransitionError(f"{task.state.value} から {data.target_state.value} へは変更できません")
            before_state = task.state
            task.state = data.target_state
            task.updated_at = utc_now()
            task.version += 1
            task.history.append(
                TaskHistoryEntry(
                    action="transitioned",
                    actor=data.actor,
                    created_at=task.updated_at,
                    before={"state": before_state.value},
                    after={"state": task.state.value},
                )
            )
            self._save_task(task)
            return deepcopy(task)

    def audio_path(self, communication_id: str, kind: str) -> Path:
        record = self.get_communication(communication_id)
        relative = record.original_audio_path if kind == "original" else record.recognition_audio_path
        resolved = (self.root / relative).resolve()
        if self.root not in resolved.parents or not resolved.is_file():
            raise NotFoundError(f"{communication_id}:{kind}")
        return resolved

    def public_state(self) -> dict[str, Any]:
        communications = self.list_communications()
        communication_revisions = {item.id: item.transcript_revision for item in communications}
        tasks: list[dict[str, Any]] = []
        for task in self.list_tasks():
            payload = task.model_dump(mode="json")
            payload["is_evidence_stale"] = any(
                task.source_transcript_revision is not None
                and communication_revisions.get(communication_id, task.source_transcript_revision)
                > task.source_transcript_revision
                for communication_id in task.evidence_communication_ids
            )
            tasks.append(payload)
        return {
            "active_area": self.active_area.model_dump(mode="json") if self.active_area else None,
            "communications": [item.model_dump(mode="json") for item in communications],
            "tasks": tasks,
            "discarded_task_count": len(self.list_tasks(include_discarded=True)) - len(tasks),
            "diagnostics": list(self.diagnostics),
        }
