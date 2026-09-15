import asyncio
import sys
import time
from contextlib import suppress

import numpy as np
import pytest
import soundfile as sf

from sumradio.audio import SpeechWorker
from sumradio.config import ROOT, Settings
from sumradio.extraction import CodexExtractor
from sumradio.models import (
    Communication,
    ConfirmLocation,
    DisplayMetric,
    Extraction,
    Location,
    LocationState,
    Notice,
    PlaceExpression,
    TaskCreate,
    TransitionRequest,
)
from sumradio.phonetics import load_tables
from sumradio.service import Service
from sumradio.storage import Conflict, Store


@pytest.fixture
def service(tmp_path):
    s = Service(Settings(data_dir=tmp_path))
    yield s
    s.store.close()


def create(s, expression="上野公園"):
    run = s.create_run("tokyo", "障害検証")
    task = s.create_task(
        run.id,
        TaskCreate(
            title="飲料水を手配",
            kind="request",
            actor="本部",
            manual_reason="操作テスト",
            place=PlaceExpression(
                expression=expression, search_name=expression, municipality=None, detail=None
            ),
        ),
    )
    return run, task


async def test_approve_during_location_lookup(service):
    run, task = create(service)
    entered, gate = asyncio.Event(), asyncio.Event()

    async def delayed(*args):
        entered.set()
        await gate.wait()
        return LocationState(
            status="candidates", candidates=[service.profiles["tokyo"].locations[0]]
        )

    service.resolver.resolve = delayed
    worker = asyncio.create_task(service.location_loop())
    await entered.wait()
    service.transition(
        run.id,
        task.id,
        TransitionRequest(expected_version=task.version, actor="人", status="unhandled"),
    )
    gate.set()
    await asyncio.wait_for(service.location_queue.join(), 2)
    result = service.store.get(run.id).tasks[task.id]
    assert result.status == "unhandled" and result.location.status == "candidates"
    worker.cancel()
    with suppress(asyncio.CancelledError):
        await worker


async def test_human_location_wins_over_delayed_lookup(service):
    run, task = create(service)
    entered, gate = asyncio.Event(), asyncio.Event()

    async def delayed(*args):
        entered.set()
        await gate.wait()
        return LocationState(status="unresolved")

    service.resolver.resolve = delayed
    worker = asyncio.create_task(service.location_loop())
    await entered.wait()
    service.confirm_location(
        run.id,
        task.id,
        ConfirmLocation(
            expected_version=task.version,
            actor="人",
            manual=Location(
                id="manual1",
                name="確認した場所",
                municipality="台東区",
                lat=35,
                lon=139,
                source="manual",
            ),
        ),
    )
    gate.set()
    await asyncio.wait_for(service.location_queue.join(), 2)
    assert service.store.get(run.id).tasks[task.id].location.status == "confirmed"
    worker.cancel()
    with suppress(asyncio.CancelledError):
        await worker


async def test_duplicate_and_completion_do_not_modify_human_task(service):
    run, task = create(service)
    approved = service.transition(
        run.id, task.id, TransitionRequest(expected_version=1, actor="人", status="unhandled")
    )
    comm = Communication(
        run_id=run.id,
        original_text="上野公園への飲料水の手配は完了しました。",
        transcription_status="done",
    )
    service.store.change(run.id, lambda r: r.communications.__setitem__(comm.id, comm))

    class Fake:
        async def extract(self, c, r):
            return Extraction(
                sender=None,
                recipient=None,
                situation=c.text,
                people=[],
                phonetic_interpretations=[],
                tasks=[],
                notices=[
                    Notice(
                        kind="completion",
                        text=c.text,
                        related_task_ids=[task.id],
                        evidence=[{"communication_id": c.id, "revision": 1, "quote": c.text}],
                    ),
                    Notice(
                        kind="duplicate",
                        text="既存の依頼を確認",
                        related_task_ids=[task.id],
                        evidence=[{"communication_id": c.id, "revision": 1, "quote": c.text}],
                    ),
                ],
            )

    service.extractor = Fake()
    worker = asyncio.create_task(service.extract_loop())
    service.enqueue_extraction(run.id, comm.id)
    await asyncio.wait_for(service.extraction_queue.join(), 2)
    result = service.store.get(run.id)
    assert len(result.tasks) == 1
    assert result.tasks[task.id].status == approved.status == "unhandled"
    assert result.tasks[task.id].quantities == approved.quantities
    assert len(result.tasks[task.id].notices) == 2
    worker.cancel()
    with suppress(asyncio.CancelledError):
        await worker


@pytest.mark.parametrize("mode", ["timeout", "nonzero", "bad_json", "huge_stdout", "huge_file"])
async def test_cli_failure_boundaries(tmp_path, mode):
    program = {
        "timeout": "time.sleep(5)",
        "nonzero": "sys.exit(2)",
        "bad_json": "out.write_text('{broken')",
        "huge_stdout": "print('x'*4096)",
        "huge_file": "out.write_text('x'*4096)",
    }[mode]
    stub = tmp_path / "codex-stub"
    stub.write_text(
        f"#!{sys.executable}\nimport sys,time\nfrom pathlib import Path\nout=Path(sys.argv[sys.argv.index('-o')+1])\nsys.stdin.read()\n{program}\n"
    )
    stub.chmod(0o700)
    settings = Settings(
        data_dir=tmp_path / "data",
        codex_bin=str(stub),
        codex_timeout=0.15 if mode == "timeout" else 3,
        output_limit=1024,
    )
    extractor = CodexExtractor(settings, load_tables(ROOT))
    from sumradio.models import Run

    run = Run(demo_profile_id="tokyo", title="CLI境界")
    c = Communication(run_id=run.id, original_text="訓練", transcription_status="done")
    run.communications[c.id] = c
    with pytest.raises((TimeoutError, ValueError, RuntimeError)):
        await extractor.extract(c, run)
    assert extractor.process is None


async def test_final_recognition_reads_whole_audio_not_rolling_window(service):
    run = service.create_run("aoba", "長い交信")
    c = Communication(
        run_id=run.id,
        transcription_status="queued",
        original_audio="long-original.wav",
        recognition_audio="long-16k.wav",
    )
    directory = service.store.run_dir(run.id) / "audio"
    directory.mkdir()
    # Synthetic samples verify buffering/conversion only, NOT speech accuracy.
    sf.write(directory / c.original_audio, np.full(48000 * 31, 0.01), 48000)
    service.store.change(run.id, lambda r: r.communications.__setitem__(c.id, c))
    received = []

    class Engine:
        state = "ready"

        def load(self):
            pass

        def transcribe(self, audio, rate):
            received.append((len(audio), rate))
            return "音声長の模擬テスト"

    s = SpeechWorker(
        service.settings, service.store, service.mic, service.publish, lambda *args: None
    )
    s.engine = Engine()
    s.enqueue(run.id, c.id)
    worker = asyncio.create_task(s.run())
    await asyncio.wait_for(s.finals.join(), 3)
    assert received == [(16000 * 31, 16000)]
    assert sf.info(directory / c.recognition_audio).samplerate == 16000
    worker.cancel()
    with suppress(asyncio.CancelledError):
        await worker


def test_single_process_lock_and_failed_atomic_commit(service, monkeypatch):
    run = service.create_run("tokyo", "")
    with pytest.raises(RuntimeError):
        Store(service.settings.data_dir)
    monkeypatch.setattr(
        "sumradio.storage.os.replace", lambda *args: (_ for _ in ()).throw(OSError("保存失敗"))
    )
    with pytest.raises(OSError):
        service.store.change(run.id, lambda r: setattr(r, "title", "未確定の更新"))
    assert service.store.get(run.id).title != "未確定の更新"


def test_display_metrics_distinguish_segmentation_and_paint(service):
    run = service.create_run("aoba", "計測")
    t = time.time()
    c = Communication(
        run_id=run.id,
        original_text="記録",
        transcription_status="done",
        metrics={"finalized_at_epoch": t - 2, "speech_ended_at_epoch": t - 7},
    )
    service.store.change(run.id, lambda r: r.communications.__setitem__(c.id, c))
    service.display_metric(
        run.id,
        c.id,
        DisplayMetric(actor="画面計測", expected_version=1, kind="final", displayed_at_epoch=t),
    )
    metrics = service.store.get(run.id).communications[c.id].metrics
    assert metrics["final_display_delay_ms"] == 2000
    assert metrics["speech_to_display_ms"] == 7000
    with pytest.raises(Conflict):
        service.display_metric(
            run.id,
            c.id,
            DisplayMetric(actor="画面計測", expected_version=2, kind="final", displayed_at_epoch=t),
        )


async def test_registered_punctuation_and_ambiguous_names(service):
    p = PlaceExpression(
        expression="青葉避難所・体育館正面入口",
        search_name="青葉避難所",
        municipality=None,
        detail="体育館正面入口",
    )
    assert (await service.resolver.resolve("aoba", p)).status == "candidates"
    loc = service.profiles["tokyo"].locations[0].model_copy(update={"id": "another_ueno"})
    service.profiles["tokyo"].locations.append(loc)
    p = PlaceExpression(
        expression="上野公園", search_name="上野公園", municipality=None, detail=None
    )
    found = await service.resolver.resolve("tokyo", p)
    assert len(found.candidates) == 2 and found.confirmed is None


async def test_short_original_phrase_with_registered_full_name_and_detail(service):
    p = PlaceExpression(
        expression="集会所玄関", search_name="ひなた集会所", municipality=None, detail="玄関"
    )
    assert (await service.resolver.resolve("aoba", p)).candidates[0].name == "ひなた集会所"
    p.expression = "集会所北玄関"
    assert (await service.resolver.resolve("aoba", p)).status == "unresolved"
    p.expression, p.search_name, p.detail = "公園南口", "上野恩賜公園", "南口"
    assert (await service.resolver.resolve("tokyo", p)).status == "unresolved"


def test_recording_blocks_run_creation(service):
    run = service.create_run("aoba", "")
    service.mic.run_id = run.id
    with pytest.raises(Conflict):
        service.create_run("tokyo", "")
    service.mic.run_id = None
