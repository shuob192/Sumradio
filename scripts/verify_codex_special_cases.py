from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime

from sumradio.codex_extractor import CodexExtractor
from sumradio.config import Settings
from sumradio.models import (
    CommunicationRecord,
    ResourceQuantity,
    TaskKind,
    TaskRecord,
    TaskSource,
    TaskState,
)
from sumradio.phonetic import load_phonetic_table


def record(case_id: str, transcript: str) -> CommunicationRecord:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return CommunicationRecord(
        id=f"comm_special_{case_id}",
        started_at=now,
        ended_at=now,
        created_at=now,
        original_audio_path="validation/original.wav",
        recognition_audio_path="validation/recognition.wav",
        original_sample_rate=48000,
        transcript_original=transcript,
        transcript_current=transcript,
    )


def existing_task() -> TaskRecord:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return TaskRecord(
        id="task_existing_water",
        kind=TaskKind.REQUEST,
        state=TaskState.OPEN,
        source=TaskSource.MANUAL,
        title="飲料水15箱の手配",
        action="飲料水15箱を手配する",
        target="飲料水",
        location="本町小学校避難所",
        resources=[ResourceQuantity(item="飲料水", quantity=15, unit="箱")],
        created_at=now,
        updated_at=now,
    )


CASES = [
    {
        "id": "situation",
        "transcript": "災害本部、こちら本町小学校避難所担当。本町小学校避難所に負傷者3名がいます。以上。",
        "tasks": [],
        "check": lambda result: len(result.candidates) == 1
        and result.candidates[0].kind == TaskKind.SITUATION_CONFIRMATION
        and result.candidates[0].people[0].count == 3,
    },
    {
        "id": "japanese_phonetic",
        "transcript": "災害本部、こちら調査班。識別符号は朝日のあです。以上。",
        "tasks": [],
        "check": lambda result: [item.interpreted_value for item in result.phonetic_interpretations] == ["ア"],
    },
    {
        "id": "nato_phonetic",
        "transcript": "災害本部、こちら調査班。識別符号はアルファです。以上。",
        "tasks": [],
        "check": lambda result: [item.interpreted_value for item in result.phonetic_interpretations] == ["A"],
    },
    {
        "id": "ordinary_words",
        "transcript": "災害本部、こちら調査班。ホテルへ向かってください。アルファ波を測定しています。以上。",
        "tasks": [],
        "check": lambda result: result.phonetic_interpretations == [],
    },
    {
        "id": "negation_hold",
        "transcript": "災害本部、こちら本町小学校避難所担当。飲料水の手配は未完了です。追加手配は保留してください。以上。",
        "tasks": [existing_task()],
        "check": lambda result: result.candidates == []
        and any(item.type == "negation_or_hold" for item in result.confirmations)
        and any("task_existing_water" in item.related_task_ids for item in result.confirmations),
    },
    {
        "id": "completion",
        "transcript": "災害本部、こちら本町小学校避難所担当。飲料水15箱の手配は完了しました。以上。",
        "tasks": [existing_task()],
        "check": lambda result: result.candidates == []
        and any(
            item.type == "completion_report" and "task_existing_water" in item.related_task_ids
            for item in result.confirmations
        ),
    },
    {
        "id": "duplicate",
        "transcript": "災害本部、こちら本町小学校避難所担当。本町小学校避難所へ飲料水15箱を手配願います。以上。",
        "tasks": [existing_task()],
        "check": lambda result: len(result.candidates) == 1
        and "task_existing_water" in result.candidates[0].related_task_ids
        and any(item.type == "duplicate" for item in result.confirmations),
    },
    {
        "id": "unmatched_completion",
        "transcript": "災害本部、こちら道路調査班。現地の通行止めは完了しました。以上。",
        "tasks": [],
        "check": lambda result: result.candidates == []
        and not any(item.type == "completion_report" for item in result.confirmations),
    },
]


async def run(selected: str) -> int:
    settings = Settings.from_env()
    extractor = CodexExtractor(
        settings,
        japanese_table=load_phonetic_table(settings.japanese_phonetic_path),
        nato_table=load_phonetic_table(settings.nato_phonetic_path),
        runtime_dir=settings.data_dir / "runtime",
    )
    results = []
    selected_cases = CASES if selected == "all" else [case for case in CASES if case["id"] == selected]
    for case in selected_cases:
        try:
            response, duration = await extractor.extract(
                record(case["id"], case["transcript"]),
                case["tasks"],
            )
            results.append(
                {
                    "case": case["id"],
                    "ok": bool(case["check"](response)),
                    "duration_seconds": round(duration, 3),
                    "result": response.model_dump(mode="json"),
                }
            )
        except Exception as exc:
            results.append({"case": case["id"], "ok": False, "error": str(exc)})
        print(json.dumps({key: value for key, value in results[-1].items() if key != "result"}, ensure_ascii=False), flush=True)

    output_dir = settings.data_dir / "verification"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_path = output_dir / f"codex-special-cases-{stamp}.json"
    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    passed = sum(1 for item in results if item["ok"])
    print(f"{passed}/{len(results)} cases passed; report={output_path}", flush=True)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="台本外の必須条件を実際のCodex CLIで整理する")
    parser.add_argument("--case", choices=["all", *[case["id"] for case in CASES]], default="all")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.case)))
