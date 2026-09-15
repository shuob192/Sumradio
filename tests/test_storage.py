from __future__ import annotations

import pytest

from sumradio.geography_models import MapExtraction, LocationStatus, YOKOHAMA_AREA
from sumradio.models import (
    CommunicationFacts,
    ExtractionResponse,
    ManualTaskInput,
    PersonCount,
    ResourceQuantity,
    TaskCandidate,
    TaskEditInput,
    TaskKind,
    TaskState,
    TaskTransitionInput,
)
from sumradio.storage import DataStore, InvalidTransitionError, VersionConflictError


def create_communication(store: DataStore):
    record = store.create_communication(
        original_pcm=b"\0\0" * 100,
        original_sample_rate=48000,
        recognition_pcm=b"\0\0" * 100,
        started_at="2026-09-14T00:00:00Z",
        ended_at="2026-09-14T00:00:10Z",
    )
    store.set_transcription_running(record.id)
    return store.complete_transcription(record.id, "飲料水15箱を手配願います。", 1.2)


def extraction() -> ExtractionResponse:
    return ExtractionResponse(
        communication=CommunicationFacts(sender="青葉避難所担当", recipient="災害本部", location="青葉避難所"),
        candidates=[
            TaskCandidate(
                map_info=MapExtraction(location=None, map_category=["other"], evidence_quotes=["飲料水"]),
                kind=TaskKind.REQUEST,
                title="飲料水の手配",
                action="飲料水15箱を手配する",
                evidence_quotes=["飲料水15箱を手配願います"],
            )
        ],
    )


def test_persists_transcript_tasks_and_restart(tmp_path) -> None:
    store = DataStore(tmp_path / "data")
    store.initialize()
    communication = create_communication(store)
    _, should_run = store.begin_extraction(communication.id, 0)
    assert should_run
    updated, tasks, current = store.commit_extraction(communication.id, 0, extraction(), 2.3)
    assert current and updated.extraction_status.value == "succeeded"
    assert len(tasks) == 1
    assert (tmp_path / "data" / "communications" / communication.id / "transcript.md").is_file()
    assert store.audio_path(communication.id, "original").is_file()

    restored = DataStore(tmp_path / "data")
    restored.initialize()
    assert restored.get_communication(communication.id).transcript_current.startswith("飲料水")
    assert restored.get_task(tasks[0].id).title == "飲料水の手配"


def test_retry_is_idempotent_and_correction_marks_evidence_stale(tmp_path) -> None:
    store = DataStore(tmp_path / "data")
    store.initialize()
    communication = create_communication(store)
    store.begin_extraction(communication.id, 0)
    _, tasks, _ = store.commit_extraction(communication.id, 0, extraction(), 1)
    _, should_run = store.begin_extraction(communication.id, 0)
    assert not should_run
    current = store.get_communication(communication.id)
    corrected = store.correct_communication(
        communication.id,
        text="飲料水16箱を手配願います。",
        author="operator",
        version=current.version,
    )
    assert corrected.transcript_original != corrected.transcript_current
    payload = store.public_state()
    assert next(item for item in payload["tasks"] if item["id"] == tasks[0].id)["is_evidence_stale"]

    store.begin_extraction(communication.id, 0)
    _, stale_tasks, is_current = store.commit_extraction(communication.id, 0, extraction(), 1)
    assert not is_current
    assert stale_tasks == []
    assert len(store.list_tasks()) == 1


def test_transcript_correction_stops_pending_location_and_rejects_old_result(tmp_path):
    store = DataStore(tmp_path / "data")
    store.initialize()
    area = store.set_area(YOKOHAMA_AREA)
    communication = create_communication(store)
    store.begin_extraction(communication.id, 0)
    _, tasks, _ = store.commit_extraction(communication.id, 0, extraction(), 1)
    # Older recordings may lack an area; assign one to model an active lookup.
    store._tasks[tasks[0].id].area = area
    started = store.begin_location(tasks[0].id)
    current = store.get_communication(communication.id)
    store.correct_communication(current.id, text="元街小学校へ手配願います。", author="operator", version=current.version)
    updated = store.get_task(started.id)
    assert updated.position.status == LocationStatus.UNRESOLVED
    assert "訂正" in updated.position.error
    assert store.finish_location(started, []) is None
    assert store.begin_location(started.id) is None


def test_human_only_transitions_and_history(tmp_path) -> None:
    store = DataStore(tmp_path / "data")
    store.initialize()
    task = store.create_manual_task(
        ManualTaskInput(kind=TaskKind.REQUEST, title="手動候補", action="確認する")
    )
    assert task.state == TaskState.CANDIDATE
    with pytest.raises(InvalidTransitionError):
        store.transition_task(task.id, TaskTransitionInput(version=task.version, target_state=TaskState.DONE))
    opened = store.transition_task(task.id, TaskTransitionInput(version=task.version, target_state=TaskState.OPEN))
    done = store.transition_task(opened.id, TaskTransitionInput(version=opened.version, target_state=TaskState.DONE))
    assert done.state == TaskState.DONE
    assert [item.action for item in done.history] == ["created", "transitioned", "transitioned"]
    with pytest.raises(VersionConflictError):
        store.edit_task(
            done.id,
            TaskEditInput(version=1, title="古い画面", action="更新", actor="operator"),
        )


def test_manual_task_structured_counts_can_be_edited(tmp_path) -> None:
    store = DataStore(tmp_path / "data")
    store.initialize()
    task = store.create_manual_task(
        ManualTaskInput(
            kind=TaskKind.REQUEST,
            title="物資確認",
            action="物資を確認する",
            people=[PersonCount(description="避難者", count=60)],
            resources=[ResourceQuantity(item="飲料水", quantity=15, unit="箱")],
        )
    )
    edited = store.edit_task(
        task.id,
        TaskEditInput(
            version=task.version,
            title=task.title,
            action=task.action,
            people=[PersonCount(description="避難者", count=61)],
            resources=[ResourceQuantity(item="飲料水", quantity=16, unit="箱")],
        ),
    )
    assert edited.people[0].count == 61
    assert edited.resources[0].quantity == 16
    assert edited.history[-1].action == "edited"


def test_discarded_task_hidden_but_preserved(tmp_path) -> None:
    store = DataStore(tmp_path / "data")
    store.initialize()
    task = store.create_manual_task(
        ManualTaskInput(kind=TaskKind.SITUATION_CONFIRMATION, title="報告確認", action="対応要否を判断する")
    )
    discarded = store.transition_task(
        task.id, TaskTransitionInput(version=task.version, target_state=TaskState.DISCARDED)
    )
    assert discarded not in store.list_tasks()
    assert store.get_task(task.id).state == TaskState.DISCARDED
    assert store.public_state()["discarded_task_count"] == 1
