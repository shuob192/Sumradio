from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from sumradio.codex_extractor import CodexExtractor
from sumradio.config import Settings
from sumradio.models import CommunicationRecord
from sumradio.phonetic import load_phonetic_table


EXPECTED_COUNTS = {
    "01": 1,
    "02": 2,
    "03": 2,
    "04": 3,
    "05": 2,
    "06": 2,
    "07": 2,
    "08": 3,
    "09": 3,
    "10": 2,
}


def load_script_cases(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    matches = re.findall(r'^## (\d{2}) [^\n]+\n\n「(.*?)」', text, flags=re.MULTILINE | re.DOTALL)
    return {case_id: transcript.replace("\n", " ").strip() for case_id, transcript in matches if case_id in EXPECTED_COUNTS}


def record(case_id: str, transcript: str) -> CommunicationRecord:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return CommunicationRecord(
        id=f"comm_validation_{case_id}",
        started_at=now,
        ended_at=now,
        created_at=now,
        original_audio_path="validation/original.wav",
        recognition_audio_path="validation/recognition.wav",
        original_sample_rate=48000,
        transcript_original=transcript,
        transcript_current=transcript,
    )


async def run(selected: str) -> int:
    settings = Settings.from_env()
    cases = load_script_cases(settings.project_root / "Document" / "無線交信台本.md")
    if set(cases) != set(EXPECTED_COUNTS):
        missing = sorted(set(EXPECTED_COUNTS) - set(cases))
        raise RuntimeError(f"台本の交信を読み込めません: {missing}")
    if selected != "all":
        cases = {selected: cases[selected]}
    extractor = CodexExtractor(
        settings,
        japanese_table=load_phonetic_table(settings.japanese_phonetic_path),
        nato_table=load_phonetic_table(settings.nato_phonetic_path),
        runtime_dir=settings.data_dir / "runtime",
    )
    results = []
    for case_id, transcript in cases.items():
        try:
            response, duration = await extractor.extract(record(case_id, transcript), [])
            candidates = response.candidates
            results.append(
                {
                    "case": case_id,
                    "ok": len(candidates) == EXPECTED_COUNTS[case_id]
                    and all(item.kind.value == "request" for item in candidates),
                    "expected_candidate_count": EXPECTED_COUNTS[case_id],
                    "actual_candidate_count": len(candidates),
                    "duration_seconds": round(duration, 3),
                    "candidate_titles": [item.title for item in candidates],
                    "result": response.model_dump(mode="json"),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "case": case_id,
                    "ok": False,
                    "expected_candidate_count": EXPECTED_COUNTS[case_id],
                    "error": str(exc),
                }
            )
        print(json.dumps(results[-1], ensure_ascii=False))
    output_dir = settings.data_dir / "verification"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_path = output_dir / f"codex-cases-{stamp}.json"
    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    passed = sum(1 for item in results if item["ok"])
    print(f"{passed}/{len(results)} cases passed; report={output_path}")
    return 0 if passed == len(results) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="台本の文字記録を実際のCodex CLIで整理する")
    parser.add_argument("--case", choices=["all", *EXPECTED_COUNTS], default="01")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.case)))


if __name__ == "__main__":
    main()
