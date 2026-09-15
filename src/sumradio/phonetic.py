from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


START_MARKER = "<!-- phonetic-table:start -->"
END_MARKER = "<!-- phonetic-table:end -->"
EXPECTED_HEADERS = ["文字", "通話表の表記", "追加の検知表記"]


class PhoneticTableError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PhoneticEntry:
    character: str
    code_word: str
    variants: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "character": self.character,
            "code_word": self.code_word,
            "variants": list(self.variants),
        }


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def load_phonetic_table(path: Path) -> list[PhoneticEntry]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PhoneticTableError(f"{path.name}を読み込めません: {exc}") from exc

    if text.count(START_MARKER) != 1 or text.count(END_MARKER) != 1:
        raise PhoneticTableError(f"{path.name}の開始・終了マーカーが正しくありません")
    table = text.split(START_MARKER, 1)[1].split(END_MARKER, 1)[0].strip()
    lines = [line.strip() for line in table.splitlines() if line.strip()]
    if len(lines) < 3:
        raise PhoneticTableError(f"{path.name}の表にデータがありません")
    if _cells(lines[0]) != EXPECTED_HEADERS:
        raise PhoneticTableError(f"{path.name}の見出しは{EXPECTED_HEADERS}の3列である必要があります")
    separator = _cells(lines[1])
    if len(separator) != 3 or not all(cell and set(cell) <= {"-", ":"} for cell in separator):
        raise PhoneticTableError(f"{path.name}のMarkdown表区切りが不正です")

    entries: list[PhoneticEntry] = []
    seen: set[str] = set()
    for number, line in enumerate(lines[2:], start=3):
        cells = _cells(line)
        if len(cells) != 3 or not cells[0] or not cells[1]:
            raise PhoneticTableError(f"{path.name}:{number}は非空の3列である必要があります")
        if cells[0] in seen:
            raise PhoneticTableError(f"{path.name}:{number}の文字「{cells[0]}」が重複しています")
        seen.add(cells[0])
        variants = tuple(part.strip() for part in cells[2].split(";") if part.strip())
        entries.append(PhoneticEntry(cells[0], cells[1], variants))
    return entries
