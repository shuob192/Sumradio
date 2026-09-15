from __future__ import annotations

import asyncio
import threading
import time
from contextlib import suppress

from .audio import AudioRecorder, CapturedSegment
from .codex_extractor import CodexExtractionError, CodexExtractor
from .config import Settings
from .events import EventBus
from .models import (
    CorrectionInput,
    ManualTaskInput,
    TaskEditInput,
    TaskRecord,
    TaskTransitionInput,
)
from .phonetic import PhoneticTableError, load_phonetic_table
from .storage import DataStore, VersionConflictError
from .transcriber import WhisperTranscriber


class SumradioService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = DataStore(settings.data_dir)
        self.events = EventBus()
        self.transcriber = WhisperTranscriber(
            settings.whisper_model,
            settings.whisper_cpu_threads,
            settings.model_cache_dir,
        )
        self.extractor: CodexExtractor | None = None
        self.recorder = AudioRecorder(
            on_segment=self._on_segment,
            on_partial=self._on_partial,
            on_level=self._on_level,
            on_error=self._on_audio_error,
            voice_rms_threshold=settings.voice_rms_threshold,
        )
        self.whisper_status = "starting"
        self.whisper_error: str | None = None
        self.codex_status = "starting"
        self.codex_error: str | None = None
        self.audio_error: str | None = None
        self.input_level = 0.0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._extraction_queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._model_task: asyncio.Task | None = None
        self._background_tasks: set[asyncio.Task] = set()
        self._transcription_lock = asyncio.Lock()
        self._partial_guard = threading.Lock()
        self._partial_busy = False
        self._last_level_event = 0.0

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.store.initialize()
        try:
            japanese = load_phonetic_table(self.settings.japanese_phonetic_path)
            nato = load_phonetic_table(self.settings.nato_phonetic_path)
            self.extractor = CodexExtractor(
                self.settings,
                japanese_table=japanese,
                nato_table=nato,
                runtime_dir=self.store.runtime_dir,
            )
            await asyncio.to_thread(self.extractor.check_cli)
            self.codex_status = "ready"
        except (CodexExtractionError, PhoneticTableError, OSError) as exc:
            self.codex_status = "error"
            self.codex_error = str(exc)
        self._worker_task = asyncio.create_task(self._extraction_worker(), name="sumradio-extraction")
        if self.settings.skip_model_load:
            self.whisper_status = "skipped"
        else:
            self._model_task = asyncio.create_task(self._load_model(), name="sumradio-whisper-load")

    async def stop(self) -> None:
        with suppress(Exception):
            self.recorder.stop()
        for task in tuple(self._background_tasks):
            task.cancel()
        if self._worker_task:
            self._worker_task.cancel()
        if self._model_task:
            self._model_task.cancel()
        tasks = [task for task in [self._worker_task, self._model_task, *self._background_tasks] if task]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _load_model(self) -> None:
        self.whisper_status = "loading"
        await self.events.publish("system.status", self.runtime_status())
        try:
            await asyncio.to_thread(self.transcriber.load)
            self.whisper_status = "ready"
            self.whisper_error = None
        except Exception as exc:
            self.whisper_status = "error"
            self.whisper_error = str(exc)
        await self.events.publish("system.status", self.runtime_status())

    def runtime_status(self) -> dict:
        return {
            "recording": self.recorder.recording,
            "input_level": self.input_level,
            "whisper": {"status": self.whisper_status, "error": self.whisper_error},
            "codex": {
                "status": self.codex_status,
                "error": self.codex_error,
                "model": self.settings.codex_model,
                "effort": self.settings.codex_effort,
                "version": self.extractor.cli_version if self.extractor else None,
            },
            "audio_error": self.audio_error,
        }

    def public_state(self) -> dict:
        return {**self.store.public_state(), "runtime": self.runtime_status()}

    def list_devices(self) -> list[dict]:
        return self.recorder.devices()

    async def start_recording(self, device_id: int | None) -> dict:
        if self.whisper_status != "ready":
            raise RuntimeError("Whisper mediumの準備完了後に録音を開始してください")
        info = await asyncio.to_thread(self.recorder.start, device_id)
        self.audio_error = None
        await self.events.publish("system.status", self.runtime_status())
        return info

    async def finalize_recording(self) -> bool:
        finalized = await asyncio.to_thread(self.recorder.finalize_current)
        return finalized

    async def stop_recording(self) -> bool:
        stopped = await asyncio.to_thread(self.recorder.stop)
        self.input_level = 0.0
        await self.events.publish("system.status", self.runtime_status())
        return stopped

    def _schedule(self, coroutine) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        task = self._loop.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _on_segment(self, segment: CapturedSegment) -> None:
        if self._loop:
            self._loop.call_soon_threadsafe(self._schedule, self._process_segment(segment))

    def _on_partial(self, pcm: bytes) -> None:
        with self._partial_guard:
            if self._partial_busy:
                return
            self._partial_busy = True
        if self._loop:
            self._loop.call_soon_threadsafe(self._schedule, self._process_partial(pcm))

    def _on_level(self, level: float) -> None:
        self.input_level = level
        now = time.monotonic()
        if self._loop and now - self._last_level_event >= 0.1:
            self._last_level_event = now
            self._loop.call_soon_threadsafe(self._schedule, self.events.publish("audio.level", {"level": level}))

    def _on_audio_error(self, error: str) -> None:
        self.audio_error = error
        if self._loop:
            self._loop.call_soon_threadsafe(
                self._schedule, self.events.publish("system.status", self.runtime_status())
            )

    async def _process_partial(self, pcm: bytes) -> None:
        try:
            if self._transcription_lock.locked():
                return
            async with self._transcription_lock:
                text = await asyncio.to_thread(self.transcriber.transcribe_pcm, pcm, final=False)
            await self.events.publish("caption.partial", {"text": text})
        except Exception as exc:
            self.whisper_error = str(exc)
        finally:
            with self._partial_guard:
                self._partial_busy = False

    async def _process_segment(self, segment: CapturedSegment) -> None:
        communication = await asyncio.to_thread(
            self.store.create_communication,
            original_pcm=segment.original_pcm,
            original_sample_rate=segment.original_sample_rate,
            recognition_pcm=segment.recognition_pcm,
            started_at=segment.started_at,
            ended_at=segment.ended_at,
        )
        await self.events.publish("communication.created", communication.model_dump(mode="json"))
        await asyncio.to_thread(self.store.set_transcription_running, communication.id)
        started = time.monotonic()
        try:
            async with self._transcription_lock:
                text = await asyncio.to_thread(
                    self.transcriber.transcribe_pcm,
                    segment.recognition_pcm,
                    final=True,
                )
            if not text:
                raise RuntimeError("文字起こし結果が空です")
            communication = await asyncio.to_thread(
                self.store.complete_transcription,
                communication.id,
                text,
                time.monotonic() - started,
            )
            await self.events.publish("caption.partial", {"text": ""})
            await self.events.publish("communication.updated", communication.model_dump(mode="json"))
            await self.enqueue_extraction(communication.id, communication.transcript_revision)
        except Exception as exc:
            communication = await asyncio.to_thread(self.store.fail_transcription, communication.id, str(exc))
            await self.events.publish("communication.updated", communication.model_dump(mode="json"))

    async def enqueue_extraction(self, communication_id: str, revision: int) -> None:
        if self.extractor is None:
            await asyncio.to_thread(self.store.begin_extraction, communication_id, revision)
            record = await asyncio.to_thread(
                self.store.fail_extraction,
                communication_id,
                revision,
                self.codex_error or "Codex整理を初期化できません",
            )
            await self.events.publish("communication.updated", record.model_dump(mode="json"))
            return
        await self._extraction_queue.put((communication_id, revision))

    async def _extraction_worker(self) -> None:
        while True:
            communication_id, revision = await self._extraction_queue.get()
            try:
                await self._run_extraction(communication_id, revision)
            finally:
                self._extraction_queue.task_done()

    async def _run_extraction(self, communication_id: str, revision: int) -> None:
        record, should_run = await asyncio.to_thread(self.store.begin_extraction, communication_id, revision)
        if not should_run:
            return
        await self.events.publish("communication.updated", record.model_dump(mode="json"))
        try:
            result, duration = await self.extractor.extract(record, self.store.list_tasks(include_discarded=False))  # type: ignore[union-attr]
            updated, tasks, is_current = await asyncio.to_thread(
                self.store.commit_extraction,
                communication_id,
                revision,
                result,
                duration,
            )
            await self.events.publish("communication.updated", updated.model_dump(mode="json"))
            if is_current:
                for task in tasks:
                    await self.events.publish("task.updated", task.model_dump(mode="json"))
            self.codex_status = "ready"
            self.codex_error = None
        except Exception as exc:
            self.codex_status = "error"
            self.codex_error = str(exc)
            updated = await asyncio.to_thread(self.store.fail_extraction, communication_id, revision, str(exc))
            await self.events.publish("communication.updated", updated.model_dump(mode="json"))
        await self.events.publish("system.status", self.runtime_status())

    async def correct_communication(self, communication_id: str, data: CorrectionInput):
        record = await asyncio.to_thread(
            self.store.correct_communication,
            communication_id,
            text=data.text,
            author=data.author,
            version=data.version,
        )
        await self.events.publish("communication.updated", record.model_dump(mode="json"))
        await self.enqueue_extraction(record.id, record.transcript_revision)
        return record

    async def retry_extraction(self, communication_id: str, version: int):
        record = self.store.get_communication(communication_id)
        if record.version != version:
            raise VersionConflictError(f"画面の版 {version} は現在の版 {record.version} と一致しません")
        if not record.transcript_current:
            raise RuntimeError("文字記録がないため再整理できません")
        await self.enqueue_extraction(record.id, record.transcript_revision)
        return record

    async def create_manual_task(self, data: ManualTaskInput) -> TaskRecord:
        task = await asyncio.to_thread(self.store.create_manual_task, data)
        await self.events.publish("task.updated", task.model_dump(mode="json"))
        return task

    async def edit_task(self, task_id: str, data: TaskEditInput) -> TaskRecord:
        task = await asyncio.to_thread(self.store.edit_task, task_id, data)
        await self.events.publish("task.updated", task.model_dump(mode="json"))
        return task

    async def transition_task(self, task_id: str, data: TaskTransitionInput) -> TaskRecord:
        task = await asyncio.to_thread(self.store.transition_task, task_id, data)
        await self.events.publish("task.updated", task.model_dump(mode="json"))
        return task
