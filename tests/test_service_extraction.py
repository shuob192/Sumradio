from dataclasses import replace

import asyncio
import pytest

from sumradio.audio import CapturedSegment
from sumradio.models import CorrectionInput
from sumradio.service import SumradioService


CORRECTED = "飲料水が不足しています。2リットル6本1箱として、15箱手配してください。"
GARBLED = "燃料水が不足しています。15フットではいてください。"


@pytest.mark.asyncio
@pytest.mark.parametrize("needs_correction", [False, True])
async def test_transcription_and_correction_reach_cli_and_create_candidate(
    settings, codex_factory, monkeypatch, needs_correction
):
    # A real child process exercises stdin, validation and persistence; its
    # response is scripted and does not claim to measure a model's accuracy.
    cli = codex_factory(
        "data = json.loads(prompt.split('入力データ:\\n', 1)[1])\n"
        "text = data['transcript']\n"
        "if '飲料水' not in text:\n"
        "    print('error: simulated extraction failure', file=sys.stderr); sys.exit(1)\n"
        "print(json.dumps({'communication': {}, 'candidates': [{\n"
        "    'kind': 'request', 'title': '飲料水15箱の手配', 'action': '飲料水を手配する',\n"
        "    'resources': [{'item': '飲料水', 'quantity': 15, 'unit': '箱'}],\n"
        "    'map_info': {'location': None, 'map_category': ['water'], 'evidence_quotes': [text]},\n"
        "    'evidence_quotes': [text]}]}, ensure_ascii=False))\n",
        "extracting-codex",
    )
    service = SumradioService(replace(settings, codex_path=str(cli)))
    monkeypatch.setattr(
        service.transcriber, "transcribe_pcm",
        lambda *args, **kwargs: GARBLED if needs_correction else CORRECTED,
    )
    await service.start()
    try:
        assert service.codex_status == "ready"
        await service._process_segment(CapturedSegment(
            original_pcm=b"\0\0" * 480,
            original_sample_rate=48000,
            recognition_pcm=b"\0\0" * 160,
            started_at="2026-09-15T00:00:00Z",
            ended_at="2026-09-15T00:00:01Z",
        ))
        await asyncio.wait_for(service._extraction_queue.join(), timeout=5)
        record = service.store.list_communications()[0]
        if needs_correction:
            assert record.extraction_status.value == "failed"
            assert service.codex_status == "error"
            assert service.store.list_tasks() == []
            await service.correct_communication(record.id, CorrectionInput(
                version=record.version, text=CORRECTED, author="operator",
            ))
            await asyncio.wait_for(service._extraction_queue.join(), timeout=5)
            record = service.store.get_communication(record.id)
            assert record.transcript_original == GARBLED
            assert record.transcript_revision == 1
        assert record.transcript_current == CORRECTED
        assert record.extraction_status.value == "succeeded"
        assert service.codex_status == "ready" and service.codex_error is None
        task, = service.store.list_tasks()
        assert task.state.value == "candidate"
        assert task.resources[0].quantity == 15
        assert task.evidence_quotes == [CORRECTED]
        assert task.source_transcript_revision == record.transcript_revision
        await service.retry_extraction(record.id, record.version)
        await asyncio.wait_for(service._extraction_queue.join(), timeout=5)
        assert len(service.store.list_tasks()) == 1
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_missing_cli_is_visible_at_startup_without_stopping_app(settings, tmp_path):
    service = SumradioService(replace(settings, codex_path=str(tmp_path / "missing")))
    await service.start()
    try:
        assert service.codex_status == "error"
        assert "SUMRADIO_CODEX_PATH" in service.codex_error
        assert service.whisper_status == "skipped"
        assert service.public_state()["tasks"] == []
    finally:
        await service.stop()
