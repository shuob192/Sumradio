"""Evaluate saved CLI checks. Never loads fixtures into the application or calls the model."""

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from sumradio.config import ROOT, Settings
from sumradio.locations import Resolver, load_profiles
from sumradio.models import PlaceExpression
from sumradio.storage import atomic_json

EXPECTED = {
    "01": ("water", [15, 60]),
    "02": ("rescue", [3, 2, 1]),
    "03": ("road_blocked", [20]),
    "04": ("road_blocked", [5]),
    "05": ("rescue", [5]),
    "06": ("supplies", [30, 6]),
    "07": ("evacuation", [12, 2]),
    "08": ("power", [1]),
    "09": ("rescue", [2]),
    "10": ("supplies", [80, 4]),
    "R01": ("water", [15, 60]),
    "R02": ("rescue", [3, 2, 1]),
    "R03": ("road_blocked", []),
}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("reports", nargs="+")
    parser.add_argument("--output", default="reports/live-evaluation.json")
    args = parser.parse_args()
    selected = {}
    attempts = []
    for filename in args.reports:
        report = json.loads(Path(filename).read_text())
        for entry in report["results"]:
            item = {**entry, "source": filename}
            selected[entry["case"]] = item
            attempts.append(
                {
                    "case": entry["case"],
                    "status": entry["status"],
                    "seconds": entry["seconds"],
                    "source": filename,
                }
            )
    evaluations = []
    with tempfile.TemporaryDirectory(prefix="sumradio-evaluation-") as temporary:
        resolver = Resolver(Settings(data_dir=Path(temporary)), load_profiles(ROOT))
        for case, entry in selected.items():
            problems = []
            if entry["status"] != "valid_json_and_evidence":
                problems.append(entry.get("error", "応答失敗"))
            result = entry.get("output", {})
            tasks = result.get("tasks", [])
            locations = []
            if case in EXPECTED and result:
                category, numbers = EXPECTED[case]
                categories = {c for t in tasks for c in t["map_categories"]}
                if category not in categories:
                    problems.append(f"必要な分類がない: {category}")
                if not any(t["kind"] == "request" for t in tasks):
                    problems.append("明示要請の候補がない")
                quantities = result["people"] + [q for t in tasks for q in t["quantities"]]
                for n in numbers:
                    if not any(q["value"] == n for q in quantities):
                        problems.append(f"数量/人数の構造化値がない: {n}")
                for task in tasks:
                    state = await resolver.resolve(
                        "tokyo" if case.startswith("R") else "aoba",
                        PlaceExpression.model_validate(task["place"]) if task["place"] else None,
                    )
                    locations.append(
                        {
                            "title": task["title"],
                            "status": state.status,
                            "candidates": [p.name for p in state.candidates],
                        }
                    )
                if not any(x["status"] == "candidates" for x in locations):
                    problems.append("登録地点に照合できる候補がない（手動確認が必要）")
            if case == "S01" and not any(t["kind"] == "situation_confirmation" for t in tasks):
                problems.append("明示要請のない報告が状況確認になっていない")
            interpretations = result.get("phonetic_interpretations", [])
            if case == "S02" and {c for p in interpretations for c in p["characters"]} != {
                "ア",
                "A",
            }:
                problems.append("通話表のア/A解釈が不一致")
            if case == "S03" and interpretations:
                problems.append("一般語句を通話表として誤変換")
            if case == "S04" and any(n["kind"] == "completion" for n in result.get("notices", [])):
                problems.append("未完了を完了報告にした")
            if case in {"S05", "S06"}:
                kind = "completion" if case == "S05" else "duplicate"
                if not any(
                    n["kind"] == kind and n["related_task_ids"] for n in result.get("notices", [])
                ):
                    problems.append(f"既存タスクに関連付けた{kind}の確認事項がない")
                if case == "S05" and tasks:
                    problems.append("追加要請のない完了報告から新規タスクを生成した")
            evaluations.append(
                {
                    "case": case,
                    "source": entry["source"],
                    "passed": not problems,
                    "problems": problems,
                    "locations": locations,
                }
            )
    output = {
        "mode": "saved_text_live_cli_not_microphone",
        "limitations": "形式・引用と限定的な分類/数量/場所の検証。意味の完全性・全体の正確さ・音声精度・実機操作の合格を意味しない。後のファイルを最新試行として採用。",
        "attempts": attempts,
        "evaluations": evaluations,
        "passed": sum(e["passed"] for e in evaluations),
        "total": len(evaluations),
    }
    atomic_json(Path(args.output), output)
    print(f"{output['passed']} / {output['total']} checks passed; {args.output}")
    for result in evaluations:
        if result["problems"]:
            print(result["case"], "; ".join(result["problems"]))
    if output["passed"] != output["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
