"""Synthetic saved extraction for isolated tests only, never live radio input."""

from sumradio.models import (
    Communication,
    Evidence,
    ExtractedTask,
    Extraction,
    LocationState,
    PlaceExpression,
    Quantity,
    Region,
    Run,
    Task,
    TranscriptInterpretation,
)


def make_run():
    run = Run(
        id="run_interpretationtest",
        demo_profile_id="tokyo",
        title="地名ワンタッチ検証",
        region=Region(prefecture="東京都", municipality="台東区"),
    )
    comm = Communication(
        id="comm_interpretationtest",
        run_id=run.id,
        original_text="こちら上野恩師公園水が足りません水1箱6本入り10箱ください",
        transcription_status="done",
        extraction_status="done",
        extraction_revision=1,
    )
    evidence = Evidence(communication_id=comm.id, revision=1, quote=comm.text)
    candidate = ExtractedTask(
        kind="request",
        title="水10箱の手配",
        description=comm.text,
        place=PlaceExpression(
            expression="上野恩師公園", search_name="上野恩師公園", municipality=None, detail=None
        ),
        map_categories=["water"],
        category_evidence=[evidence],
        quantities=[
            Quantity(value=10, unit="箱", description="1箱6本入り"),
            Quantity(value=6, unit="本/箱", description="1箱あたり"),
        ],
        assignee=None,
        deadline=None,
        evidence=[evidence],
        related_task_ids=[],
        uncertainties=["公園名は要確認。", "水が飲料水を指すか要確認。"],
    )
    comm.extraction = Extraction(
        sender=None,
        recipient=None,
        situation=None,
        people=[],
        tasks=[candidate],
        notices=[],
        phonetic_interpretations=[],
        transcript_interpretations=[
            TranscriptInterpretation(
                possible_meaning="上野恩賜公園", reason="公園名の誤認識の可能性", evidence=evidence
            )
        ],
    )
    task = Task(
        **candidate.model_dump(),
        id="task_interpretationtest",
        run_id=run.id,
        origin="ai",
        location=LocationState(status="unresolved"),
    )
    run.communications[comm.id] = comm
    run.tasks[task.id] = task
    return run
