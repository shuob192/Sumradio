import asyncio
import contextlib
import time

from .audio import Microphone, SpeechWorker
from .config import Settings
from .extraction import CodexExtractor, validate_evidence
from .interpretations import location_options
from .locations import Resolver, load_profiles
from .models import (
    ApprovedLocationInterpretation,
    ConfirmInterpretationLocation,
    ConfirmLocation,
    Correction,
    History,
    LocationState,
    Region,
    Run,
    RunView,
    Status,
    Task,
    TaskCreate,
    TaskEdit,
    TransitionRequest,
    now,
)
from .phonetics import load_tables
from .storage import Conflict, Store, check_version


class Service:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.data_dir)
        self.profiles = load_profiles(settings.resources)
        self.resolver = Resolver(settings, self.profiles)
        self.table_error = None
        try:
            self.tables = load_tables(settings.resources)
        except Exception as exc:
            self.tables, self.table_error = {}, str(exc)
        self.extractor = CodexExtractor(settings, self.tables)
        self.subscribers: set[asyncio.Queue] = set()
        self.extraction_queue: asyncio.Queue = asyncio.Queue()
        self.queued: set[tuple] = set()
        self.location_queue: asyncio.Queue = asyncio.Queue()
        self.mic = Microphone(settings, self.store, self.publish, self.enqueue_speech)
        self.speech = SpeechWorker(settings, self.store, self.mic, self.publish, self.transcribed)
        self.workers: list[asyncio.Task] = []

    def publish(self, event: dict):
        if event.get("run_id") and "version" not in event:
            event = {**event, "version": self.store.get(event["run_id"]).version}
        for subscriber in tuple(self.subscribers):
            if subscriber.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    subscriber.get_nowait()
                # A snapshot on reconnect or resync recovers any missing state event.
                with contextlib.suppress(asyncio.QueueFull):
                    subscriber.put_nowait({"type": "resync"})
            else:
                subscriber.put_nowait(event)

    def changed(self, run: Run):
        self.publish({"type": "state", "run_id": run.id, "version": run.version})

    async def start(self):
        self.workers = [
            asyncio.create_task(self.speech.run()),
            asyncio.create_task(self.extract_loop()),
            asyncio.create_task(self.location_loop()),
        ]
        for run in self.store.all():
            for comm in run.communications.values():
                if comm.transcription_status in {"queued", "running", "recording"}:
                    if (
                        comm.original_audio
                        and (self.store.run_dir(run.id) / "audio" / comm.original_audio).exists()
                    ):
                        self.speech.enqueue(run.id, comm.id)
                    else:
                        self.speech.set_status(
                            run.id, comm.id, "failed", "中断された録音の音声が見つかりません"
                        )
                elif comm.transcription_status == "done" and comm.extraction_status in {
                    "waiting",
                    "running",
                }:
                    self.enqueue_extraction(run.id, comm.id)
            for task in run.tasks.values():
                if task.location.status == "pending":
                    self.enqueue_location(run.id, task)
        # Confirmed locations in snapshots are authoritative; repair an interrupted cache write.
        confirmed = [
            (run, task)
            for run in self.store.all()
            for task in run.tasks.values()
            if task.location.confirmed
        ]
        for run, task in sorted(confirmed, key=lambda pair: pair[1].location.confirmed_at or ""):
            self.remember_location(run, task)

    async def close(self):
        await self.mic.stop()
        for task in self.workers:
            task.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.store.close()

    def create_run(self, profile_id: str, title: str, region: Region | None = None) -> Run:
        if self.mic.run_id:
            raise Conflict("録音を停止してから実施回を切り替えてください")
        profile = self.profiles[profile_id]
        if profile_id == "aoba" and region is not None:
            raise ValueError(
                "架空デモは青葉地区固定です。実在地域は実在デモか通常利用で指定してください"
            )
        run = self.store.add(
            Run(demo_profile_id=profile_id, title=title.strip() or profile.name, region=region)
        )
        self.changed(run)
        return run

    def enqueue_speech(self, run_id, comm_id):
        self.speech.enqueue(run_id, comm_id)

    def display_metric(self, run_id, comm_id, body):
        if abs(time.time() - body.displayed_at_epoch) > 30:
            raise ValueError("表示時刻が現在の時刻から離れています")

        def update(run):
            comm = run.communications[comm_id]
            check_version(comm, body.expected_version)
            metrics = comm.metrics
            if body.kind == "final":
                if comm.transcription_status != "done" or comm.revision != 1:
                    raise ValueError("初回の確定文字記録だけを測定します")
                if "displayed_final_at_epoch" in metrics:
                    return
                start = metrics.get("finalized_at_epoch")
                if start is None or body.displayed_at_epoch < float(start):
                    raise ValueError("区切りの確定時刻がありません")
                metrics["displayed_final_at_epoch"] = body.displayed_at_epoch
                metrics["final_display_delay_ms"] = (body.displayed_at_epoch - float(start)) * 1000
                metrics["speech_to_display_ms"] = (
                    body.displayed_at_epoch - float(metrics.get("speech_ended_at_epoch", start))
                ) * 1000
            else:
                captured = body.captured_at_epoch
                if captured is None or not 0 <= body.displayed_at_epoch - captured <= 120:
                    raise ValueError("字幕の測定時刻が不正です")
                latency = (body.displayed_at_epoch - captured) * 1000
                count = int(metrics.get("subtitle_samples", 0))
                total = float(metrics.get("subtitle_total_display_ms", 0))
                metrics["subtitle_samples"] = count + 1
                metrics["subtitle_total_display_ms"] = total + latency
                metrics["subtitle_max_display_ms"] = max(
                    float(metrics.get("subtitle_max_display_ms", 0)), latency
                )
            # Telemetry does not change the human-edit version or emit another display event.

        self.store.change(run_id, update)

    def transcribed(self, run_id: str, comm_id: str, text: str, recognition_seconds: float):
        def update(run):
            comm = run.communications[comm_id]
            comm.original_text = text
            comm.transcription_status = "done"
            comm.version += 1
            comm.error = None
            t = time.time()
            comm.metrics.update(
                {
                    "recognized_at_epoch": t,
                    "recognition_ms": recognition_seconds * 1000,
                    "final_text_delay_ms": (t - float(comm.metrics.get("finalized_at_epoch", t)))
                    * 1000,
                    "speech_to_text_ms": (t - float(comm.metrics.get("speech_ended_at_epoch", t)))
                    * 1000,
                }
            )
            if not text.strip():
                comm.extraction_status, comm.error = (
                    "failed",
                    "文字を認識できませんでした。音声を確認して訂正できます",
                )

        run = self.store.change(run_id, update)
        self.changed(run)
        if text.strip():
            self.enqueue_extraction(run_id, comm_id)

    def enqueue_extraction(self, run_id: str, comm_id: str):
        comm = self.store.get(run_id).communications[comm_id]
        if comm.extraction_revision == comm.revision and comm.extraction_status == "done":
            return
        key = (run_id, comm_id, comm.revision)
        if key not in self.queued:
            self.queued.add(key)
            self.extraction_queue.put_nowait(key)

    async def extract_loop(self):
        while True:
            key = await self.extraction_queue.get()
            run_id, comm_id, revision = key
            started = time.time()
            try:
                run = self.store.get(run_id)
                comm = run.communications[comm_id]
                if revision != comm.revision or (
                    comm.extraction_status == "done" and comm.extraction_revision == revision
                ):
                    continue

                def running(r):
                    c = r.communications[comm_id]
                    c.extraction_status, c.error = "running", None
                    c.version += 1

                self.changed(self.store.change(run_id, running))
                if self.table_error:
                    raise ValueError(self.table_error)
                result = await self.extractor.extract(comm, run)
                created = []

                def commit(r):
                    c = r.communications[comm_id]
                    if c.revision != revision:
                        c.extraction_attempts.append(
                            {"revision": revision, "status": "stale", "at": now()}
                        )
                        return
                    if c.extraction_revision == revision and c.extraction_status == "done":
                        return
                    c.extraction, c.extraction_revision, c.extraction_status = (
                        result,
                        revision,
                        "done",
                    )
                    c.version += 1
                    c.error = None
                    c.extraction_attempts.append(
                        {
                            "revision": revision,
                            "status": "done",
                            "at": now(),
                            "model": self.settings.codex_model,
                        }
                    )
                    t = time.time()
                    c.metrics.update(
                        {
                            "codex_ms": (t - started) * 1000,
                            "speech_to_tasks_ms": (
                                t - float(c.metrics.get("speech_ended_at_epoch", started))
                            )
                            * 1000,
                        }
                    )
                    r.history.append(
                        History(
                            actor="AI整理",
                            action="communication_extracted",
                            target_id=comm_id,
                            after={"revision": revision, "result": result.model_dump(mode="json")},
                        )
                    )
                    for candidate in result.tasks:
                        task = Task(**candidate.model_dump(), run_id=run_id, origin="ai")
                        r.tasks[task.id] = task
                        created.append(task)
                        r.history.append(
                            History(
                                actor="AI候補生成",
                                action="task_created",
                                target_id=task.id,
                                after=task.model_dump(mode="json"),
                            )
                        )
                    for notice in result.notices:
                        for tid in notice.related_task_ids:
                            r.tasks[tid].notices.append(notice)
                            r.tasks[tid].version += 1

                self.changed(self.store.change(run_id, commit))
                for task in created:
                    self.enqueue_location(run_id, task)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = (
                    "Codexの応答が60秒以内に完了しませんでした"
                    if isinstance(exc, TimeoutError)
                    else str(exc)
                )

                def failed(r):
                    c = r.communications[comm_id]
                    c.extraction_attempts.append(
                        {"revision": revision, "status": "failed", "at": now(), "error": message}
                    )
                    if c.revision == revision:
                        c.extraction_status, c.error = "failed", message
                        c.version += 1

                self.changed(self.store.change(run_id, failed))
            finally:
                self.queued.discard(key)
                self.extraction_queue.task_done()

    def enqueue_location(self, run_id: str, task: Task):
        self.location_queue.put_nowait((run_id, task.id, task.place))

    async def location_loop(self):
        while True:
            run_id, task_id, place = await self.location_queue.get()
            try:
                run = self.store.get(run_id)
                try:
                    result = await self.resolver.resolve(run.demo_profile_id, place, run.region)
                except Exception as exc:
                    result = LocationState(status="unresolved", reason=f"場所検索失敗: {exc}")

                def update(r):
                    task = r.tasks[task_id]
                    # Approval or an unrelated edit must not strand a pending lookup.
                    # A changed place or a human-confirmed location still rejects stale results.
                    if task.place == place and task.location.status == "pending":
                        task.location = result
                        task.version += 1

                self.changed(self.store.change(run_id, update))
            finally:
                self.location_queue.task_done()

    def correct(self, run_id, comm_id, body):
        def update(run):
            comm = run.communications[comm_id]
            check_version(comm, body.expected_version)
            if comm.transcription_status in {"recording", "queued", "running"}:
                raise Conflict("文字起こし完了後に訂正してください")
            old = {"text": comm.text, "revision": comm.revision}
            comm.revision += 1
            comm.version += 1
            comm.corrections.append(
                Correction(revision=comm.revision, text=body.text, actor=body.actor)
            )
            comm.transcription_status, comm.extraction_status, comm.error = "done", "waiting", None
            run.history.append(
                History(
                    actor=body.actor,
                    action="transcript_corrected",
                    target_id=comm_id,
                    before=old,
                    after={"text": body.text, "revision": comm.revision},
                )
            )

        run = self.store.change(run_id, update)
        self.changed(run)
        self.enqueue_extraction(run_id, comm_id)
        return run

    def validate_task(self, run: Run, body):
        validate_evidence(body.evidence, run)
        if not set(body.related_task_ids) <= run.tasks.keys():
            raise ValueError("関連タスクがこの実施回に存在しません")

    def create_task(self, run_id: str, body: TaskCreate):
        task = Task(
            **body.model_dump(exclude={"actor"}),
            category_evidence=[],
            run_id=run_id,
            origin="manual",
        )

        def update(run):
            self.validate_task(run, body)
            run.tasks[task.id] = task
            run.history.append(
                History(
                    actor=body.actor,
                    action="task_created",
                    target_id=task.id,
                    after=task.model_dump(mode="json"),
                )
            )

        run = self.store.change(run_id, update)
        self.changed(run)
        self.enqueue_location(run_id, task)
        return task

    def edit_task(self, run_id: str, task_id: str, body: TaskEdit):
        def update(run):
            task = run.tasks[task_id]
            check_version(task, body.expected_version)
            self.validate_task(run, body)
            before = task.model_dump(mode="json")
            changed_place = task.place != body.place
            values = task.model_dump()
            values.update(body.model_dump(exclude={"actor", "expected_version"}))
            task = Task.model_validate(values)
            if changed_place:
                task.location = LocationState()
            task.version += 1
            run.tasks[task_id] = task
            run.history.append(
                History(
                    actor=body.actor,
                    action="task_edited",
                    target_id=task_id,
                    before=before,
                    after=task.model_dump(mode="json"),
                )
            )

        run = self.store.change(run_id, update)
        self.changed(run)
        if run.tasks[task_id].location.status == "pending":
            self.enqueue_location(run_id, run.tasks[task_id])
        return run.tasks[task_id]

    def transition(self, run_id: str, task_id: str, body: TransitionRequest):
        allowed = {
            (Status.candidate, Status.unhandled),
            (Status.candidate, Status.discarded),
            (Status.unhandled, Status.completed),
        }

        def update(run):
            task = run.tasks[task_id]
            check_version(task, body.expected_version)
            if (task.status, body.status) not in allowed:
                raise ValueError("許可されていない状態遷移です")
            before = task.status
            task.status = body.status
            task.version += 1
            run.history.append(
                History(
                    actor=body.actor,
                    action="task_transition",
                    target_id=task_id,
                    before={"status": before},
                    after={"status": task.status},
                )
            )

        run = self.store.change(run_id, update)
        self.changed(run)
        return run.tasks[task_id]

    def confirm_location(self, run_id: str, task_id: str, body: ConfirmLocation):
        def update(run):
            task = run.tasks[task_id]
            check_version(task, body.expected_version)
            if bool(body.candidate_id) == bool(body.manual):
                raise ValueError("位置候補または手動座標のどちらかを指定してください")
            location = body.manual
            if body.candidate_id:
                location = next(
                    (x for x in task.location.candidates if x.id == body.candidate_id), None
                )
            if location is None:
                raise ValueError("選択した位置候補が見つかりません")
            if body.manual:
                location = location.model_copy(update={"source": "manual", "checked_at": now()})
            before = task.location.model_dump(mode="json")
            task.location = LocationState(
                status="confirmed", confirmed=location, confirmed_by=body.actor, confirmed_at=now()
            )
            task.version += 1
            run.history.append(
                History(
                    actor=body.actor,
                    action="location_confirmed",
                    target_id=task_id,
                    before=before,
                    after=task.location.model_dump(mode="json"),
                )
            )

        run = self.store.change(run_id, update)
        self.changed(run)
        task = run.tasks[task_id]
        self.remember_location(run, task)
        return task

    def view_run(self, run_id: str) -> RunView:
        run = self.store.get(run_id)
        return RunView(
            **run.model_dump(),
            interpretation_locations=location_options(run, self.profiles[run.demo_profile_id]),
        )

    def confirm_interpretation_location(
        self, run_id: str, task_id: str, body: ConfirmInterpretationLocation
    ):
        def update(run):
            task = run.tasks[task_id]
            check_version(task, body.expected_version)
            # Re-derive under the store lock. Client-supplied IDs are not authority to
            # use another run/revision, a changed master, or an ambiguous location.
            option = next(
                (
                    p
                    for p in location_options(run, self.profiles[run.demo_profile_id])
                    if p.task_id == task_id
                    and p.communication_id == body.communication_id
                    and p.revision == body.revision
                    and p.interpretation_index == body.interpretation_index
                    and p.location.id == body.location_id
                ),
                None,
            )
            if option is None:
                raise Conflict(
                    "解釈候補が変更されたか、登録地点を一意に確認できません。最新の内容を確認してください"
                )
            interpretation = run.communications[
                body.communication_id
            ].extraction.transcript_interpretations[body.interpretation_index]
            before = task.location.model_dump(mode="json")
            task.location = LocationState(
                status="confirmed",
                confirmed=option.location,
                confirmed_by=body.actor,
                confirmed_at=now(),
                reason="地名の解釈を人間が確認しました。タスク内容の承認とは別の操作です。",
                interpretation=ApprovedLocationInterpretation(
                    communication_id=body.communication_id,
                    revision=body.revision,
                    interpretation_index=body.interpretation_index,
                    possible_meaning=interpretation.possible_meaning,
                    original_expression=task.place.expression,
                    evidence=interpretation.evidence,
                ),
            )
            task.version += 1
            run.history.append(
                History(
                    actor=body.actor,
                    action="location_interpretation_confirmed",
                    target_id=task_id,
                    before=before,
                    after=task.location.model_dump(mode="json"),
                )
            )

        run = self.store.change(run_id, update)
        self.changed(run)
        task = run.tasks[task_id]
        self.remember_location(run, task)
        return task

    def remember_location(self, run: Run, task: Task):
        try:
            self.resolver.remember(
                run.demo_profile_id, task.place, task.location.confirmed, run.region
            )
        except (OSError, ValueError) as exc:
            self.store.errors.append(f"位置確認は保存済み。キャッシュ再生成は次回起動時: {exc}")
