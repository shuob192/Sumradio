import pytest

from sumradio.extraction import Extractor


def event(text, extractor):
    normalized, highlights, hits = extractor.annotate(text)
    return {
        "id": "event-1",
        "raw_text": text,
        "normalized_text": normalized,
        "highlights": highlights,
        "glossary_hits": hits,
    }


def test_annotations_preserve_raw_and_require_context(settings):
    extractor = Extractor(settings.config_dir)
    for text in ["アルファ波を確認", "ホテルへ向かって", "訓練、アルファ、了解"]:
        assert extractor.annotate(text)[0] == text
    assert (
        extractor.annotate("訓練、符号：アルファ、ブラボー")[0]
        == "訓練、符号：A（アルファ）、B（ブラボー）"
    )
    assert extractor.annotate("アルファ、ブラボー")[0] == "A（アルファ）、B（ブラボー）"
    assert Extractor(settings.config_dir, False).annotate("符号：アルファ")[0] == "符号：アルファ"
    assert len(extractor.terms["nato_phonetic"]) == 26


def test_important_slots_without_confidence(settings):
    extractor = Extractor(settings.config_dir)
    text = "訓練、青葉避難所、十五名、14時30分、未確認"
    normalized, highlights, _ = extractor.annotate(text)
    assert normalized == text  # no speculative number/place correction
    assert {h["type"] for h in highlights} >= {"place", "number", "time", "negative"}


def test_request_then_completion_never_completes_task(settings):
    extractor = Extractor(settings.config_dir)
    request = event("訓練、青葉避難所へ飲料水15箱を手配願う", extractor)
    (task,) = extractor.extract(request, [])
    assert task["status"] == "candidate"
    task["status"] = "open"
    complete = event("訓練、青葉避難所への飲料水の手配、完了", extractor)
    assert extractor.extract(complete, [task]) == []
    assert complete["completion_suggestions"][0]["task_id"] == task["id"]
    assert task["status"] == "open"
    other = event("訓練、若葉公園への飲料水の手配、完了", extractor)
    extractor.extract(other, [task])
    assert other["completion_suggestions"] == []


@pytest.mark.parametrize(
    "ending",
    ["未完了", "完了していない", "保留", "未対応", "できない", "必要ない", "訂正、完了ではない"],
)
def test_negative_never_suggests_completion(settings, ending):
    extractor = Extractor(settings.config_dir)
    (task,) = extractor.extract(event("訓練、青葉避難所へ飲料水を手配願う", extractor), [])
    report = event(f"訓練、青葉避難所への飲料水手配、{ending}", extractor)
    assert extractor.extract(report, [task]) == []
    assert report["completion_suggestions"] == []


def test_duplicate_is_suggestion_only(settings):
    extractor = Extractor(settings.config_dir)
    report = event("青葉避難所へ飲料水を手配願う", extractor)
    (first,) = extractor.extract(report, [])
    (second,) = extractor.extract(report, [first])
    assert second["id"] != first["id"]
    assert second["duplicate_candidates"] == [first["id"]]
