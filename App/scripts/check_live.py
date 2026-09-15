"""Live Codex checks against saved training text; this is NOT microphone acceptance."""

import argparse
import asyncio
import hashlib
import re
import subprocess
import time
from pathlib import Path

from sumradio.config import ROOT, Settings
from sumradio.extraction import INSTRUCTIONS, CodexExtractor
from sumradio.models import Communication, PlaceExpression, Quantity, Run, Task, now
from sumradio.phonetics import load_tables
from sumradio.storage import atomic_json


def cases():
    result = {}
    for filename in ["無線交信台本.md", "無線交信台本_実在地名.md"]:
        content = (ROOT / filename).read_text()
        for match in re.finditer(r"^## (\d{2}|R\d{2}) [^\n]+\n\n「([^」]+)」", content, re.M):
            result[match[1]] = match[2]
    result.update(
        {
            "S01": "本部、こちら調査班。青葉避難所に負傷者3名。以上。",
            "S02": "本部、こちら調査班。符号は朝日のあです。続いて符号はアルファです。以上。",
            "S03": "本部、こちら調査班。ホテルへ向かっています。アルファ波についての一般連絡です。以上。",
            "S04": "本部、こちら調査班。青葉避難所への飲料水の手配は未完了です。到着は未確認、追加の手配は保留です。以上。",
            "S05": "本部、こちら調査班。青葉避難所への飲料水15箱の手配は完了しました。追加の要請はありません。以上。",
            "S06": "本部、こちら調査班。先ほどと同じ要請を再送します。青葉避難所への飲料水15箱の手配をお願いします。追加分ではありません。以上。",
            "A01": "本部、緊張水がほしいです。",
            "A02": "本部、飲み水が足りません。緊張水15箱をお願いします。",
            "A03": "本部、蒸留水が欲しいです。飲料用ではありません。",
            "A04": "本部、毛布と緊張水が欲しいです。人数は不明、追加の救急隊の派遣は不要です。",
            "I01": "訓練。本部、青葉避難所に負傷者3名。うち1名の搬送と救急隊の派遣をお願いします。",
            "I02": "訓練。青葉避難所の倉庫に2名が閉じ込められています。けが人はいません。救助をお願いします。",
            "I03": "訓練。ひなた集会所で煙と炎が出ています。消防隊の出動をお願いします。",
            "I04": "訓練。青葉橋の道路が冠水して通行できません。近くの建物の壁も崩れています。道路と建物の現地確認をお願いします。",
            "I05": "訓練。青葉避難所の電話が不通で無線機も故障しています。予備の無線機を手配願います。",
            "I06": "訓練。青葉避難所に戻る予定の2名の行方が分からず、連絡も取れません。安否確認をお願いします。",
            "I07": "訓練。青葉避難所に発熱している人が1名います。けがはありません。医療担当者への相談を手配願います。",
            "I08": "訓練。青葉避難所のトイレが使用できず、し尿の処理もできません。仮設トイレを2基お願いします。",
            "I09": "訓練。青葉避難所に火災も負傷者もありません。飲料水が足りないので10箱手配願います。",
        }
    )
    return result


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", default="R01")
    parser.add_argument("--output", default="reports/live-extraction.json")
    args = parser.parse_args()
    selected = (
        cases() if args.case == "all" else {key: cases()[key] for key in args.case.split(",")}
    )
    settings = Settings()
    extractor = CodexExtractor(settings, load_tables(ROOT))
    version = subprocess.run(
        [settings.codex_bin, "--version"], capture_output=True, text=True
    ).stdout.strip()
    output = {
        "mode": "saved_text_live_codex_not_microphone",
        "model": settings.codex_model,
        "codex_version": version,
        "at": now(),
        "prompt_sha256": hashlib.sha256(INSTRUCTIONS.encode()).hexdigest(),
        "results": [],
    }
    for key, text in selected.items():
        run = Run(demo_profile_id="tokyo" if key.startswith("R") else "aoba", title="検証")
        comm = Communication(run_id=run.id, original_text=text, transcription_status="done")
        run.communications[comm.id] = comm
        if key in {"S05", "S06"}:
            existing = Task(
                run_id=run.id,
                kind="request",
                title="青葉避難所へ飲料水15箱を手配",
                description="本部で確認した先行要請",
                origin="manual",
                manual_reason="検証用の既存タスク",
                status="unhandled",
                place=PlaceExpression(
                    expression="青葉避難所",
                    search_name="青葉避難所",
                    municipality=None,
                    detail=None,
                ),
                map_categories=["water"],
                quantities=[Quantity(value=15, unit="箱", description="飲料水")],
                assignee=None,
                deadline=None,
                related_task_ids=[],
                uncertainties=[],
            )
            run.tasks[existing.id] = existing
        started = time.monotonic()
        entry = {"case": key, "input": text}
        try:
            result = await extractor.extract(comm, run)
            entry.update(
                {"status": "valid_json_and_evidence", "output": result.model_dump(mode="json")}
            )
        except Exception as exc:
            entry.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        entry["seconds"] = round(time.monotonic() - started, 3)
        output["results"].append(entry)
        atomic_json(Path(args.output), output)
        print(key, entry["status"], entry["seconds"], flush=True)
    if any(x["status"] == "failed" for x in output["results"]):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
