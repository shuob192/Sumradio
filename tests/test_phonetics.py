from shutil import copytree
from string import ascii_uppercase

import pytest

from sumradio.extraction import Extractor
from sumradio.phonetics import load_phonetic_table


def table(body):
    return (
        "# テスト用通話表\n\n説明文は検知に使いません。\n"
        "<!-- phonetic-table:start -->\n"
        "| 文字 | 通話表の表記 | 追加の検知表記 |\n"
        "| --- | --- | --- |\n"
        f"{body}\n"
        "<!-- phonetic-table:end -->\n"
    )


def test_tables_cover_japanese_and_nato_alphabets(settings):
    extractor = Extractor(settings.config_dir)
    japanese = extractor.terms["japanese_phonetic"]
    assert set(japanese) == set(
        "アイウエオカキクケコサシスセソタチツテトナニヌネノ"
        "ハヒフヘホマミムメモヤユヨラリルレロワヰヱヲン゛゜ー、└（）0123456789"
    )
    assert set(extractor.terms["nato_phonetic"]) == set(ascii_uppercase)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("符号：朝日のあ", "符号：ア（朝日のあ）"),
        ("通話表：あさひのあ", "通話表：ア（あさひのあ）"),
        ("桜のサ、無線のム", "サ（桜のサ）、ム（無線のム）"),
        ("符号：ゐどのヰ、かぎのあるヱ", "符号：ヰ（ゐどのヰ）、ヱ（かぎのあるヱ）"),
        ("符号：おしまいのん", "符号：ン（おしまいのん）"),
        ("符号：はがきのハ、半濁点", "符号：ハ（はがきのハ）、゜（半濁点）"),
        ("符号：数字のひと、数字のきゅう", "符号：1（数字のひと）、9（数字のきゅう）"),
        ("符号：Alfa、JULIETT、x-RaY", "符号：A（Alfa）、J（JULIETT）、X（x-RaY）"),
        ("アルファー、ブラボー", "A（アルファー）、B（ブラボー）"),
        ("ノベンバー、ロミオ、ウィスキー", "N（ノベンバー）、R（ロミオ）、W（ウィスキー）"),
        ("符号：ロメオ、ズールー", "符号：R（ロメオ）、Z（ズールー）"),
        ("朝日のあ、bravo", "ア（朝日のあ）、B（bravo）"),
        ("符号：　朝日のア　", "符号：　ア（朝日のア）　"),
    ],
)
def test_markdown_annotations_preserve_original_spelling(settings, text, expected):
    normalized, highlights, _ = Extractor(settings.config_dir).annotate(text)
    assert normalized == expected
    phonetics = [h for h in highlights if h["type"] == "phonetic"]
    assert phonetics
    assert all(h["text"] in text for h in phonetics)


@pytest.mark.parametrize(
    "text",
    [
        "朝日のあ",
        "符号：朝日のアです",
        "符号：朝日のアいろはのイ",
        "ホテルへ向かって",
        "符号：アルファ波を確認",
        "コールサイン：BravoTeam",
        "符号：Hotelへ向かって",
        "符号：朝日の未知",
        "要支援者十五名",
    ],
)
def test_context_and_word_boundaries_still_required(settings, text):
    normalized, highlights, _ = Extractor(settings.config_dir).annotate(text)
    assert normalized == text
    assert not any(h["type"] == "phonetic" for h in highlights)


def test_radio_mode_off_disables_markdown_annotations(settings):
    text = "符号：朝日のあ、Alfa"
    normalized, highlights, _ = Extractor(settings.config_dir, False).annotate(text)
    assert normalized == text
    assert not any(h["type"] == "phonetic" for h in highlights)


def test_editing_markdown_changes_detection_after_reload(settings, tmp_path):
    config = tmp_path / "config"
    copytree(settings.config_dir, config)
    old = Extractor(config)
    path = config / "japanese_phonetic.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("朝日のあ;", "朝日のあ; 朝日のエー;"),
        encoding="utf-8",
    )
    text = "符号：朝日のエー"
    assert old.annotate(text)[0] == text
    assert Extractor(config).annotate(text)[0] == "符号：ア（朝日のエー）"


def test_prompt_examples_come_from_markdown(settings, tmp_path):
    config = tmp_path / "config"
    copytree(settings.config_dir, config)
    (config / "japanese_phonetic.md").write_text(
        table("| ア | テストのア | テストのあ |"), encoding="utf-8"
    )
    assert "テストのア" in Extractor(config).prompt
    assert "朝日のア" not in Extractor(config).prompt


@pytest.mark.parametrize(
    "content",
    [
        "# 表なし",
        table("| ア | 朝日のア | |") + "<!-- phonetic-table:start -->",
        table(""),
        table("| ア | | 朝日のあ |"),
        table("| ア | 朝日のア | |\n| ア | あさひのあ | |"),
        table("| ア | 朝日のア | |\n| イ | 朝日のア | |"),
        table("| A | Alfa | |\n| B | ALFA | |"),
        table("| ア | 朝日のア | 朝日のあ; |"),
        table("| ア | 朝日のア | extra | extra |"),
        table("ア | 朝日のア | 朝日のあ"),
        table("| ア | 朝日のア | | ").replace("追加の検知表記", "別の列"),
    ],
)
def test_invalid_markdown_reports_file(tmp_path, content):
    path = tmp_path / "japanese_phonetic.md"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="japanese_phonetic.md"):
        load_phonetic_table(path)


def test_missing_table_does_not_silently_disable_detection(settings, tmp_path):
    config = tmp_path / "config"
    copytree(settings.config_dir, config)
    (config / "japanese_phonetic.md").unlink()
    with pytest.raises(FileNotFoundError, match="japanese_phonetic.md"):
        Extractor(config)


def test_conflicting_aliases_across_tables_are_rejected(settings, tmp_path):
    config = tmp_path / "config"
    copytree(settings.config_dir, config)
    (config / "japanese_phonetic.md").write_text(table("| ア | Alfa | |"), encoding="utf-8")
    with pytest.raises(ValueError, match="通話表間.*Alfa"):
        Extractor(config)
