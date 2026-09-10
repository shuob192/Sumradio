"""Load the editable Markdown spelling tables used by radio annotations."""

import re
from pathlib import Path


def load_phonetic_table(path: Path) -> dict[str, list[str]]:
    """Read one marked, three-column table; reject ambiguous or malformed entries."""
    text = path.read_text(encoding="utf-8")
    start, end = "<!-- phonetic-table:start -->", "<!-- phonetic-table:end -->"
    if text.count(start) != 1 or text.count(end) != 1 or text.index(start) > text.index(end):
        raise ValueError(f"{path}: 通話表の開始・終了マーカーが必要です")
    block = text.split(start, 1)[1].split(end, 1)[0]
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    header = ["文字", "通話表の表記", "追加の検知表記"]
    rows = []
    for line in lines:
        if not line.startswith("|") or not line.endswith("|"):
            raise ValueError(f"{path}: 通話表は3列のMarkdown表にしてください: {line}")
        cells = [cell.strip() for cell in line[1:-1].split("|")]
        if len(cells) != 3:
            raise ValueError(f"{path}: 通話表は3列が必要です: {line}")
        rows.append(cells)
    if (
        len(rows) < 3
        or rows[0] != header
        or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in rows[1])
    ):
        raise ValueError(f"{path}: 通話表の見出し・区切り行・データ行を確認してください")
    result = {}
    owners = {}
    for symbol, canonical, extra in rows[2:]:
        if len(symbol) != 1 or symbol in result or not canonical:
            raise ValueError(f"{path}: 文字の重複または空の表記があります: {symbol}")
        aliases = [canonical] + (extra.split(";") if extra else [])
        result[symbol] = []
        for alias in aliases:
            alias = alias.strip()
            if not alias:
                raise ValueError(f"{path}: 空の検知表記があります: {symbol}")
            key = alias.lower()
            if key in owners and owners[key] != symbol:
                raise ValueError(f"{path}: 複数の文字に同じ検知表記があります: {alias}")
            owners[key] = symbol
            if alias not in result[symbol]:
                result[symbol].append(alias)
    return result
