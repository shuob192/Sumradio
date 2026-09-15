from pathlib import Path

import pytest

from sumradio.phonetic import PhoneticTableError, load_phonetic_table


def test_loads_both_reference_tables() -> None:
    root = Path(__file__).resolve().parents[1]
    japanese = load_phonetic_table(root / "Document" / "japanese_phonetic.md")
    nato = load_phonetic_table(root / "Document" / "nato_phonetic.md")
    assert next(item for item in japanese if item.character == "ア").code_word == "朝日のア"
    assert "朝日のあ" in next(item for item in japanese if item.character == "ア").variants
    assert next(item for item in nato if item.character == "A").code_word == "Alfa"
    assert "アルファ" in next(item for item in nato if item.character == "A").variants


@pytest.mark.parametrize(
    "text",
    [
        "| 文字 | 通話表の表記 | 追加の検知表記 |\n| --- | --- | --- |\n| A | Alfa | Alpha |",
        "<!-- phonetic-table:start -->\n| 文字 | 異なる | 追加の検知表記 |\n| --- | --- | --- |\n| A | Alfa | Alpha |\n<!-- phonetic-table:end -->",
        "<!-- phonetic-table:start -->\n| 文字 | 通話表の表記 | 追加の検知表記 |\n| --- | --- | --- |\n| A | Alfa | Alpha |\n| A | Again | |\n<!-- phonetic-table:end -->",
    ],
)
def test_rejects_malformed_table(tmp_path: Path, text: str) -> None:
    path = tmp_path / "table.md"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(PhoneticTableError):
        load_phonetic_table(path)
