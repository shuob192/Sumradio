from __future__ import annotations

import asyncio
import threading
import time
import uuid
from contextlib import suppress

from .audio import AudioRecorder, CapturedSegment
from .codex_extractor import CodexExtractionError, CodexExtractor
from .config import Settings
from .events import EventBus
from .geography import Geography, place_query
from .geography_models import AreaInput, LocationConfirmInput, LocationStatus, OperatingArea
from .models import (
    CorrectionInput,
    ManualTaskInput,
    TaskEditInput,
    TaskRecord,
    TaskTransitionInput,
    TaskState,
)
from .phonetic import PhoneticTableError, load_phonetic_table
from .storage import DataStore, VersionConflictError
from .transcriber import WhisperTranscriber


class SumradioService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = DataStore(settings.data_dir)
        self.events = EventBus()
        self.geography = Geography(settings)
        self._location_tasks: dict[str, asyncio.Task] = {}
        self._recording_area: OperatingArea | None = None
        self.boot_id = uuid.uuid4().hex
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
        self.geography.initialize()
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
        for task in self.store.list_tasks():
            if task.position.status in {LocationStatus.UNRESOLVED, LocationStatus.QUEUED, LocationStatus.SEARCHING}:
                await self._enqueue_location(task)
        if self.settings.skip_model_load:
            self.whisper_status = "skipped"
        else:
            self._model_task = asyncio.create_task(self._load_model(), name="sumradio-whisper-load")

    async def stop(self) -> None:
        with suppress(Exception):
            self.recorder.stop()
        for task in tuple(self._background_tasks):
            task.cancel()
        for task in self._location_tasks.values():
            task.cancel()
        if self._worker_task:
            self._worker_task.cancel()
        if self._model_task:
            self._model_task.cancel()
        tasks = [task for task in [self._worker_task, self._model_task, *self._background_tasks, *self._location_tasks.values()] if task]
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
            "boot_id": self.boot_id,
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
        return {**self.store.public_state(), "runtime": self.runtime_status(), "map": self.geography.config()}

    def list_devices(self) -> list[dict]:
        return self.recorder.devices()

    async def start_recording(self, device_id: int | None) -> dict:
        if not self.store.active_area:
            raise ValueError("先に災害対象地域を指定してください")
        if self.whisper_status != "ready":
            raise RuntimeError("Whisper mediumの準備完了後に録音を開始してください")
        self._recording_area = self.store.active_area.model_copy(deep=True)
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
            self._loop.call_soon_threadsafe(self._schedule, self._process_segment(segment, self._recording_area))

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

    async def _process_segment(self, segment: CapturedSegment, area: OperatingArea | None = None) -> None:
        communication = await asyncio.to_thread(
            self.store.create_communication,
            original_pcm=segment.original_pcm,
            original_sample_rate=segment.original_sample_rate,
            recognition_pcm=segment.recognition_pcm,
            started_at=segment.started_at,
            ended_at=segment.ended_at,
            area=area,
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
            related = [task for task in self.store.list_tasks(include_discarded=False) if task.area == record.area]
            result, duration = await self.extractor.extract(record, related)  # type: ignore[union-attr]
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
                    await self._enqueue_location(task)
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
        for task in self.store.list_tasks():
            if communication_id in task.evidence_communication_ids:
                job = self._location_tasks.get(task.id)
                if job:
                    job.cancel()
                await self.events.publish("task.updated", task.model_dump(mode="json"))
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
        if not self.store.active_area:
            raise ValueError("先に災害対象地域を指定してください")
        task = await asyncio.to_thread(self.store.create_manual_task, data)
        await self.events.publish("task.updated", task.model_dump(mode="json"))
        return await self._enqueue_location(task)

    async def edit_task(self, task_id: str, data: TaskEditInput) -> TaskRecord:
        task = await asyncio.to_thread(self.store.edit_task, task_id, data)
        await self.events.publish("task.updated", task.model_dump(mode="json"))
        return await self._enqueue_location(task)

    async def transition_task(self, task_id: str, data: TaskTransitionInput) -> TaskRecord:
        task = await asyncio.to_thread(self.store.transition_task, task_id, data)
        await self.events.publish("task.updated", task.model_dump(mode="json"))
        return task

    async def set_area(self, data: AreaInput) -> OperatingArea:
        if self.recorder.recording:
            raise ValueError("録音を停止してから対象地域を変更してください")
        area = await asyncio.to_thread(self.store.set_area, data)
        await self.events.publish("area.updated", area.model_dump(mode="json"))
        self._schedule(self._refresh_places(area))
        return area

    async def _refresh_places(self, area: OperatingArea) -> None:
        await self.geography.refresh_catalog(area)
        await self.events.publish("map.updated", self.geography.config())

    async def _enqueue_location(self, task: TaskRecord, *, version: int | None = None) -> TaskRecord:
        if not task.area or not place_query(task):
            return task
        if version is None and task.position.status not in {
            LocationStatus.UNRESOLVED, LocationStatus.QUEUED, LocationStatus.SEARCHING,
        }:
            return task
        started = await asyncio.to_thread(self.store.begin_location, task.id, version=version)
        if not started:
            return task
        previous = self._location_tasks.get(task.id)
        if previous and previous is not asyncio.current_task():
            previous.cancel()
        await self.events.publish("task.updated", started.model_dump(mode="json"))
        job = asyncio.create_task(self._resolve_location(started), name=f"location-{task.id}")
        self._location_tasks[task.id] = job

        def completed(future):
            if self._location_tasks.get(task.id) is future:
                self._location_tasks.pop(task.id, None)

        job.add_done_callback(completed)
        return started

    async def _resolve_location(self, started: TaskRecord) -> None:
        places, error = [], None
        try:
            places = await self.geography.resolve(started)
        except Exception as exc:
            error = f"場所検索に失敗しました。手動指定または再試行を利用してください: {exc}"
        updated = await asyncio.to_thread(self.store.finish_location, started, places, error)
        if updated:
            await self.events.publish("task.updated", updated.model_dump(mode="json"))
        else:
            current = self.store.get_task(started.id)
            # A board transition or text edit may race a lookup. Reuse the cache,
            # but never overwrite a confirmed position or an obsolete transcript.
            if current.position.status in {LocationStatus.QUEUED, LocationStatus.UNRESOLVED}:
                await self._enqueue_location(current)

    async def retry_location(self, task_id: str, version: int) -> TaskRecord:
        task = self.store.get_task(task_id)
        if task.version != version:
            raise VersionConflictError("タスクが更新されています。画面を更新してください")
        if not task.area or not place_query(task):
            raise ValueError("検索する場所と対象地域を指定してください")
        if task.state not in {TaskState.CANDIDATE, TaskState.OPEN}:
            raise ValueError("対応中のタスクだけ場所を再検索できます")
        if not self.store.evidence_is_current(task):
            raise ValueError("根拠が旧版です。訂正後のタスクを確認するか、場所を手動指定してください")
        return await self._enqueue_location(task, version=version)

    async def confirm_location(self, task_id: str, data: LocationConfirmInput) -> TaskRecord:
        task = await asyncio.to_thread(self.store.confirm_location, task_id, data)
        self.geography.remember(task)
        job = self._location_tasks.get(task_id)
        if job:
            job.cancel()
        await self.events.publish("task.updated", task.model_dump(mode="json"))
        return task
