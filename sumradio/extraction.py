"""Conservative dictionary annotations and explainable task suggestions."""

import json
import re
from pathlib import Path

import yaml

from .models import new_id, now
from .phonetics import load_phonetic_table


class Extractor:
    def __init__(self, config_dir: Path, radio_mode=True):
        self.terms = yaml.safe_load((config_dir / "radio_terms.yaml").read_text(encoding="utf-8"))
        for family in ("nato_phonetic", "japanese_phonetic"):
            self.terms[family] = load_phonetic_table(config_dir / f"{family}.md")
        self.rules = json.loads((config_dir / "task_rules.json").read_text(encoding="utf-8"))
        self.radio_mode = radio_mode
        self.aliases = {}
        for family in ("nato_phonetic", "japanese_phonetic"):
            for symbol, aliases in self.terms[family].items():
                for alias in aliases:
                    key = alias.lower()
                    if key in self.aliases and self.aliases[key] != symbol:
                        raise ValueError(f"通話表間で検知表記が重複しています: {alias}")
                    self.aliases[key] = symbol
        alternatives = "|".join(re.escape(a) for a in sorted(self.aliases, key=len, reverse=True))
        # Japanese words are not separated by spaces: require actual delimiters.
        # Limit case folding to ASCII inside the alternatives, preserving Unicode delimiters.
        self.phonetic = re.compile(rf"(?<![^\s、。，:：])(?ai:({alternatives}))(?=$|[\s、。，])")

    @property
    def prompt(self):
        terms = "、".join(self.terms["domain_terms"])
        examples = "、".join(
            aliases[0]
            for family in ("japanese_phonetic", "nato_phonetic")
            for aliases in list(self.terms[family].values())[:2]
        )
        return f"日本語の訓練無線。{terms}。符号、{examples}。例：要支援者15名。"

    def annotate(self, raw_text):
        matches = list(self.phonetic.finditer(raw_text))
        explicit = bool(re.search(r"符号|通話表|コールサイン", raw_text))
        consecutive = any(
            re.fullmatch(r"[\s、。，]+", raw_text[a.end() : b.start()])
            for a, b in zip(matches, matches[1:], strict=False)
        )
        normalized = raw_text
        if self.radio_mode and (explicit or consecutive):
            normalized = self.phonetic.sub(
                lambda m: f"{self.aliases[m[1].lower()]}（{m[1]}）", raw_text
            )
        highlights = []
        for kind, terms in (
            ("place", self.terms["places"]),
            ("unit", self.terms["units"]),
            ("negative", self.rules["negative"]),
        ):
            highlights.extend({"type": kind, "text": t} for t in terms if t in raw_text)
        for kind, pattern in (
            (
                "time",
                r"[0-9０-９一二三四五六七八九十]+(?:時(?:半|[0-9０-９一二三四五六七八九十]+分)?|:[0-9０-９]{2})",
            ),
            ("number", r"[0-9０-９〇零一二三四五六七八九十百千万]+(?:名|人|箱|台|本|枚|個)"),
        ):
            highlights.extend({"type": kind, "text": m[0]} for m in re.finditer(pattern, raw_text))
        if normalized != raw_text:
            highlights.extend({"type": "phonetic", "text": m[0]} for m in matches)
        return normalized, highlights, [t for t in self.terms["domain_terms"] if t in raw_text]

    def extract(self, event, existing_tasks):
        text = event["raw_text"]
        places = [p for p in self.terms["places"] if p in text]
        targets = [t for t in self.rules["targets"] if t in text]
        negatives = [t for t in self.rules["negative"] if t in text]
        complete = [t for t in self.rules["completion"] if t in text]
        requests = [t for t in self.rules["request"] if t in text]
        # Keep contradictory/negative reports visible, but never suggest completion from them.
        event["completion_suggestions"] = []
        event["extraction_notes"] = negatives
        if complete and not negatives:
            event["completion_suggestions"] = [
                {"task_id": t["id"], "source_event_id": event["id"], "matched_rules": complete}
                for t in existing_tasks
                if t["status"] in ("candidate", "open")
                and t.get("place") in places
                and t.get("target") in targets
            ]
        if not requests or negatives or complete:
            return []
        units = [u for u in self.terms["units"] if u in text]
        due = next((h["text"] for h in event["highlights"] if h["type"] == "time"), None)
        task = make_task(
            re.sub(r"^訓練[、，。\s]*", "", text),
            [event["id"]],
            place=places[0] if len(places) == 1 else None,
            target=targets[0] if len(targets) == 1 else None,
            assignee=units[0] if len(units) == 1 else None,
            due_at=due,
        )
        task["matched_rules"] = requests
        task["duplicate_candidates"] = [
            t["id"]
            for t in existing_tasks
            if t["status"] in ("candidate", "open")
            and task["place"]
            and task["target"]
            and t.get("place") == task["place"]
            and t.get("target") == task["target"]
        ]
        return [task]


def make_task(title, source_event_ids, **fields):
    return {
        "id": f"task-{new_id()}",
        "title": title,
        "status": "candidate",
        "place": None,
        "target": None,
        "assignee": None,
        "due_at": None,
        "source_event_ids": list(dict.fromkeys(source_event_ids)),
        "created_at": now(),
        "confirmed_by": None,
        "completed_at": None,
        **fields,
    }
