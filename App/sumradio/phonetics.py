from pathlib import Path


def read_table(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    start, end = "<!-- phonetic-table:start -->", "<!-- phonetic-table:end -->"
    if text.count(start) != 1 or text.count(end) != 1 or text.index(start) >= text.index(end):
        raise ValueError(f"{path.name}: 通話表の開始・終了マーカーが不正です")
    lines = [
        line.strip() for line in text.split(start)[1].split(end)[0].splitlines() if line.strip()
    ]
    rows = []
    for line in lines:
        if not line.startswith("|") or not line.endswith("|"):
            raise ValueError(f"{path.name}: 表以外の行があります")
        cells = [s.strip() for s in line[1:-1].split("|")]
        if len(cells) != 3:
            raise ValueError(f"{path.name}: 3列である必要があります")
        rows.append(cells)
    if len(rows) < 3 or rows[0] != ["文字", "通話表の表記", "追加の検知表記"]:
        raise ValueError(f"{path.name}: 列名または行数が不正です")
    if any(set(c) - set("-: ") or "-" not in c for c in rows[1]):
        raise ValueError(f"{path.name}: 表の区切りが不正です")
    seen, result = set(), []
    for char, label, aliases in rows[2:]:
        if not char or not label or char in seen:
            raise ValueError(f"{path.name}: 文字の欠落・重複があります")
        seen.add(char)
        result.append(
            {
                "character": char,
                "label": label,
                "aliases": [a.strip() for a in aliases.split(";") if a.strip()],
            }
        )
    return result


def load_tables(root: Path) -> dict:
    return {
        "japanese": read_table(root / "japanese_phonetic.md"),
        "nato": read_table(root / "nato_phonetic.md"),
    }
