import copy

import pytest
from test_core import extraction

from sumradio.config import ROOT
from sumradio.extraction import (
    materialize_evidence,
    source_spans,
    structured_schema,
    validate_extraction,
)
from sumradio.models import Communication, Run
from sumradio.phonetics import load_tables


def fixture(text="緊張水がほしいです。"):
    run = Run(demo_profile_id="aoba", title="聞き取り検証")
    comm = Communication(run_id=run.id, original_text=text, transcription_status="done")
    run.communications[comm.id] = comm
    return run, comm


def wire(comm):
    raw = extraction(comm).model_dump()
    raw["tasks"][0]["title"] = "要請品目を確認して手配"
    raw["tasks"][0]["quantities"] = []
    raw["tasks"][0]["place"] = None
    raw["tasks"][0]["map_categories"] = ["other"]
    for field in ["evidence", "category_evidence"]:
        raw["tasks"][0][field] = [{"source_id": "s1"}]
    raw["transcript_interpretations"] = [
        {
            "possible_meaning": "飲料水の可能性がありますが未確認",
            "reason": "品目の聞き取りが曖昧。原音で確認する必要がある",
            "evidence": {"source_id": "s1"},
        }
    ]
    return raw


def test_reproduce_old_failure_is_rewritten_quote_not_unknown_word():
    run, comm = fixture()
    result = extraction(comm)
    validate_extraction(result, comm, run, load_tables(ROOT))
    result.tasks[0].evidence[0].quote = "飲料水がほしいです。"
    with pytest.raises(ValueError, match="根拠引用"):
        validate_extraction(result, comm, run, load_tables(ROOT))


def test_source_ids_allow_interpretation_while_preserving_original():
    run, comm = fixture()
    raw = wire(comm)
    before = copy.deepcopy(raw)
    result = materialize_evidence(raw, comm, source_spans(comm.text))
    validate_extraction(result, comm, run, load_tables(ROOT))
    assert raw == before
    assert comm.original_text == "緊張水がほしいです。"
    assert result.tasks[0].evidence[0].quote == comm.original_text
    assert result.tasks[0].quantities == []
    assert any("聞き取り要確認" in n for n in result.tasks[0].uncertainties)
    assert result.transcript_interpretations[0].possible_meaning.startswith("飲料水の可能性")


@pytest.mark.parametrize(
    "text", ["", " 。\n", "a\nB。C！ D?", "長い交信" * 300, "２リットル\r\n6本。緊張水。"]
)
def test_sources_lossless_including_long_and_unpunctuated_text(text):
    spans = source_spans(text)
    assert "".join(spans.values()) == text
    assert all(len(piece) <= 400 for piece in spans.values())


@pytest.mark.parametrize(
    "bad",
    [
        {"source_id": "not_present"},
        {"source_id": "s1", "quote": "別の内容"},
        {"quote": "飲料水"},
        {"source_id": None},
    ],
)
def test_invalid_refs_still_rejected(bad):
    _, comm = fixture()
    raw = wire(comm)
    raw["tasks"][0]["evidence"] = [bad]
    with pytest.raises(ValueError):
        materialize_evidence(raw, comm, source_spans(comm.text))


def test_schema_only_requests_source_ids_not_generated_quotes():
    schema = structured_schema(["s1", "s2"])
    evidence = schema["$defs"]["Evidence"]
    assert set(evidence["properties"]) == {"source_id"}
    assert evidence["properties"]["source_id"]["enum"] == ["s1", "s2"]


def test_reference_revision_is_bound_by_backend():
    from sumradio.models import Correction

    run, comm = fixture()
    comm.revision = 2
    comm.corrections.append(Correction(revision=2, text="飲料水がほしいです。", actor="人"))
    result = materialize_evidence(wire(comm), comm, source_spans(comm.text))
    validate_extraction(result, comm, run, load_tables(ROOT))
    assert result.tasks[0].evidence[0].revision == 2
    assert result.tasks[0].evidence[0].quote == "飲料水がほしいです。"
    assert comm.original_text == "緊張水がほしいです。"


def test_phonetics_use_same_exact_source_binding():
    run, comm = fixture("符号は朝日のあです。")
    raw = wire(comm)
    raw["transcript_interpretations"] = []
    raw["phonetic_interpretations"] = [
        {
            "original": "朝日のあ",
            "characters": ["ア"],
            "interpreted": "ア",
            "evidence": {"source_id": "s1"},
        }
    ]
    result = materialize_evidence(raw, comm, source_spans(comm.text))
    validate_extraction(result, comm, run, load_tables(ROOT))
    assert result.phonetic_interpretations[0].evidence.quote == comm.original_text
