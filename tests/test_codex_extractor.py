from __future__ import annotations

from dataclasses import replace

import pytest

from sumradio.codex_extractor import CodexExtractionError, CodexExtractor
from sumradio.codex_extractor import _structured_output_schema
from sumradio.codex_extractor import _split_exact_quotes
from sumradio.geography_models import MapExtraction, PlaceExpression
from sumradio.models import (
    CommunicationFacts,
    CommunicationRecord,
    ExtractionResponse,
    PhoneticInterpretation,
    TaskCandidate,
    TaskKind,
    TaskRecord,
    TaskSource,
    TaskState,
)
from sumradio.phonetic import PhoneticEntry


def communication(text: str) -> CommunicationRecord:
    return CommunicationRecord(
        id="comm_1",
        started_at="2026-09-14T00:00:00Z",
        ended_at="2026-09-14T00:00:10Z",
        created_at="2026-09-14T00:00:11Z",
        original_audio_path="communications/comm_1/original.wav",
        recognition_audio_path="communications/comm_1/recognition.wav",
        original_sample_rate=48000,
        transcript_original=text,
        transcript_current=text,
    )


def existing_task() -> TaskRecord:
    return TaskRecord(
        id="task_existing",
        kind=TaskKind.REQUEST,
        state=TaskState.OPEN,
        source=TaskSource.MANUAL,
        title="飲料水",
        action="飲料水を手配する",
        created_at="2026-09-14T00:00:00Z",
        updated_at="2026-09-14T00:00:00Z",
    )


def make_extractor(settings, tmp_path) -> CodexExtractor:
    return CodexExtractor(
        settings,
        japanese_table=[PhoneticEntry("ア", "朝日のア", ("朝日のあ",))],
        nato_table=[PhoneticEntry("A", "Alfa", ("アルファ",))],
        runtime_dir=tmp_path,
    )


def fake_codex(tmp_path, body: str):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "fake-codex"
    path.write_text(f"#!/bin/sh\n/bin/cat >/dev/null\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_command_uses_ephemeral_read_only_schema_and_luna(settings, tmp_path) -> None:
    extractor = make_extractor(settings, tmp_path)
    command = extractor.command()
    assert command[:4] == ["codex", "--ask-for-approval", "never", "exec"]
    assert "--ephemeral" in command and "read-only" in command
    assert "--ignore-user-config" in command and "--ignore-rules" in command
    assert "--output-schema" in command and "gpt-5.6-luna" in command


def test_structured_output_schema_requires_every_property() -> None:
    schema = _structured_output_schema()

    def assert_strict(node):
        if isinstance(node, dict):
            if "properties" in node:
                assert set(node["required"]) == set(node["properties"])
                assert node["additionalProperties"] is False
            assert "default" not in node
            for value in node.values():
                assert_strict(value)
        elif isinstance(node, list):
            for value in node:
                assert_strict(value)

    assert_strict(schema)


def test_separates_joined_quotes_only_when_each_sentence_is_exact_and_in_order():
    transcript = "避難者80名です。対象食品を確認中です。80食を手配願います。"
    assert _split_exact_quotes(["避難者80名です。80食を手配願います。"], transcript) == ["避難者80名です。", "80食を手配願います。"]
    contiguous = "避難者80名です。対象食品を確認中です。"
    assert _split_exact_quotes([contiguous], transcript) == [contiguous]
    for invalid in ["避難者81名です。80食を手配願います。", "80食を手配願います。避難者80名です。", "避難者80名です。完了しました。"]:
        # Keep the entire invalid quote so normal validation rejects it.
        assert _split_exact_quotes([invalid], transcript) == [invalid]
    with pytest.raises(CodexExtractionError, match="20件"):
        _split_exact_quotes(["避難者80名です。80食を手配願います。"] * 11, transcript)


def test_prompt_separates_rules_references_and_dynamic_input(settings, tmp_path) -> None:
    extractor = make_extractor(settings, tmp_path)
    prompt = extractor.build_prompt(communication("符号は朝日のあです。"), [existing_task()])
    assert "機械的に置換せず" in prompt
    assert '"character":"ア"' in prompt
    assert '"transcript":"符号は朝日のあです。"' in prompt
    assert '"id":"task_existing"' in prompt


def test_validates_exact_evidence_and_related_ids(settings, tmp_path) -> None:
    extractor = make_extractor(settings, tmp_path)
    text = "符号は朝日のあです。飲料水を手配願います。"
    result = ExtractionResponse(
        communication=CommunicationFacts(),
        phonetic_interpretations=[
            PhoneticInterpretation(
                alphabet="japanese", source_text="朝日のあ", interpreted_value="ア", evidence_quote="符号は朝日のあです"
            )
        ],
        candidates=[
            TaskCandidate(
                map_info=MapExtraction(location=None, map_category=["water"], evidence_quotes=["飲料水を手配願います"]),
                kind=TaskKind.REQUEST,
                title="飲料水",
                action="手配する",
                evidence_quotes=["飲料水を手配願います"],
                related_task_ids=["task_existing"],
            )
        ],
    )
    extractor.validate_result(result, text, [existing_task()])
    bad_quote = result.model_copy(deep=True)
    bad_quote.candidates[0].evidence_quotes = ["原文にない引用"]
    with pytest.raises(CodexExtractionError, match="根拠引用"):
        extractor.validate_result(bad_quote, text, [existing_task()])
    bad_quote.candidates[0].evidence_quotes = ["   "]
    with pytest.raises(CodexExtractionError, match="根拠引用"):
        extractor.validate_result(bad_quote, text, [existing_task()])
    bad_map_quote = result.model_copy(deep=True)
    bad_map_quote.candidates[0].map_info.evidence_quotes = ["原文にない地図の根拠"]
    with pytest.raises(CodexExtractionError):
        extractor.validate_result(bad_map_quote, text, [existing_task()])
    bad_map_quote.candidates[0].map_info = MapExtraction(
        location=PlaceExpression(source_text="原文にない施設", search_name="本町小学校"),
        map_category=["water"], evidence_quotes=["飲料水を手配願います"],
    )
    with pytest.raises(CodexExtractionError):
        extractor.validate_result(bad_map_quote, text, [existing_task()])
    bad_id = result.model_copy(deep=True)
    bad_id.candidates[0].related_task_ids = ["task_unknown"]
    with pytest.raises(CodexExtractionError, match="存在しない"):
        extractor.validate_result(bad_id, text, [existing_task()])


@pytest.mark.asyncio
async def test_extracts_structured_output_from_subprocess(settings, tmp_path) -> None:
    result = ExtractionResponse(
        communication=CommunicationFacts(sender="調査班"),
        candidates=[
            TaskCandidate(
                map_info=MapExtraction(location=None, map_category=["other"], evidence_quotes=["現地確認"]),
                kind=TaskKind.REQUEST,
                title="現地確認",
                action="現地を確認する",
                evidence_quotes=["現地確認をお願いします"],
            )
        ],
    )
    fake = fake_codex(tmp_path, "printf '%s' '" + result.model_dump_json().replace("'", "'\\''") + "'")
    configured = replace(settings, codex_path=str(fake))
    extractor = make_extractor(configured, tmp_path / "runtime")
    parsed, duration = await extractor.extract(communication("現地確認をお願いします。"), [])
    assert parsed.communication.sender == "調査班"
    assert len(parsed.candidates) == 1
    assert duration >= 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("printf 'not-json'", "JSONを検証できません"),
        ("printf '{\"message\":\"認証が必要です\"}' >&2; exit 7", "認証が必要です"),
    ],
)
async def test_rejects_invalid_or_failed_codex(settings, tmp_path, body, message) -> None:
    fake = fake_codex(tmp_path, body)
    extractor = make_extractor(replace(settings, codex_path=str(fake)), tmp_path / "runtime")
    with pytest.raises(CodexExtractionError, match=message):
        await extractor.extract(communication("現地確認をお願いします。"), [])


@pytest.mark.asyncio
async def test_enforces_timeout_and_output_limit(settings, tmp_path) -> None:
    slow = fake_codex(tmp_path / "slow", "/bin/sleep 1")
    extractor = make_extractor(
        replace(settings, codex_path=str(slow), codex_timeout_seconds=0.05),
        tmp_path / "slow-runtime",
    )
    with pytest.raises(CodexExtractionError, match="タイムアウト"):
        await extractor.extract(communication("現地確認をお願いします。"), [])

    large = fake_codex(tmp_path / "large", "printf '1234567890'")
    extractor = make_extractor(
        replace(settings, codex_path=str(large), codex_output_limit_bytes=4),
        tmp_path / "large-runtime",
    )
    with pytest.raises(CodexExtractionError, match="許可サイズ"):
        await extractor.extract(communication("現地確認をお願いします。"), [])
