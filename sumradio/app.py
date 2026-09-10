import asyncio
import io
import json
from contextlib import asynccontextmanager
from typing import Annotated
from urllib.parse import urlparse

import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .extraction import make_task
from .models import CaptureStart, EventEdit, TaskCreate, TaskEdit
from .service import RadioService
from .settings import PACKAGE, Settings
from .store import Store, encode


def sse_frame(row):
    return f"id: {row['seq']}\nevent: {row['kind']}\ndata: {row['payload']}\n\n"


def create_app(settings=None, backend=None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        store = Store(settings.data_dir / "sumradio.sqlite3")
        service = RadioService(settings, store, backend)
        app.state.service = service
        try:
            yield
        finally:
            if service.replay_task and not service.replay_task.done():
                service.replay_task.cancel()
                try:
                    await service.replay_task
                except asyncio.CancelledError:
                    pass
            await asyncio.to_thread(service.close)
            store.close()

    app = FastAPI(title="Sumradio", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def local_only(request, call_next):
        # No CORS. Block cross-site mutations of a loopback service as well as DNS rebinding.
        host = request.url.hostname
        if host not in ("localhost", "127.0.0.1", "::1", "testserver"):
            return JSONResponse({"detail": "localhostで利用してください"}, status_code=403)
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and (
            request.headers.get("sec-fetch-site") == "cross-site"
            or (origin and urlparse(origin).netloc != request.url.netloc)
        ):
            return JSONResponse({"detail": "別サイトからの操作は許可されません"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; media-src 'self' blob:; img-src 'self' data:; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "交信またはタスクが見つかりません"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    def service(request: Request):
        return request.app.state.service

    @app.get("/api/snapshot")
    def snapshot(request: Request):
        return service(request).snapshot()

    @app.get("/api/stream")
    async def stream(request: Request, after: Annotated[int, Query(ge=0)] = 0):
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError as exc:
            raise HTTPException(400, "不正なSSEカーソル") from exc

        async def events():
            nonlocal cursor
            idle = 0
            while not await request.is_disconnected():
                rows = service(request).store.since(cursor)
                for row in rows:
                    yield sse_frame(row)
                    cursor = row["seq"]
                if rows:
                    idle = 0
                else:
                    idle += 1
                    if idle % 40 == 0:
                        yield ": heartbeat\n\n"
                    await asyncio.sleep(0.25)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.patch("/api/events/{identifier}")
    def edit_event(identifier: str, body: EventEdit, request: Request):
        changes = body.model_dump(exclude_unset=True, exclude={"actor"})
        if changes.get("status", "valid") is None:
            raise ValueError("確認状態は空にできません")
        return service(request).store.edit("events", identifier, changes, body.actor)

    @app.post("/api/tasks", status_code=201)
    def create_task(body: TaskCreate, request: Request):
        fields = body.model_dump(exclude={"actor"})
        task = make_task(**fields)
        return service(request).store.create_task(task, body.actor)

    @app.patch("/api/tasks/{identifier}")
    def edit_task(identifier: str, body: TaskEdit, request: Request):
        changes = body.model_dump(exclude_unset=True, exclude={"actor"})
        for key in ("status", "title", "source_event_ids"):
            if key in changes and changes[key] is None:
                raise ValueError(f"{key} は空にできません")
        return service(request).store.edit("tasks", identifier, changes, body.actor)

    @app.get("/api/history/{identifier}")
    def history(identifier: str, request: Request):
        return service(request).store.history(identifier)

    @app.get("/api/audio/{identifier}/{variant}")
    def audio(identifier: str, variant: str, request: Request):
        if variant not in ("raw", "processed"):
            raise HTTPException(404)
        event = service(request).store.get("events", identifier)
        if not event.get("audio_ref"):
            raise HTTPException(404, "この保存済み台本には音声がありません")
        path = (
            settings.data_dir / "audio" / f"{event['id']}{'.raw' if variant == 'raw' else ''}.wav"
        )
        if not path.is_file():
            raise HTTPException(404, "音声ファイルがありません")
        return FileResponse(path, media_type="audio/wav")

    @app.get("/api/devices")
    def devices(request: Request):
        try:
            return service(request).devices()
        except Exception as exc:
            raise HTTPException(
                503, f"音声入力を利用できません。audio依存関係を確認: {exc}"
            ) from exc

    @app.post("/api/capture/start")
    def start(body: CaptureStart, request: Request):
        service(request).start_capture(body.device, body.mode)
        return {"ok": True}

    @app.post("/api/capture/stop")
    def stop(request: Request):
        service(request).stop_capture()
        return {"ok": True}

    @app.post("/api/capture/manual/{action}")
    def manual(action: str, request: Request):
        if action not in ("start", "end"):
            raise HTTPException(404)
        service(request).manual(action == "start")
        return {"ok": True}

    @app.post("/api/audio", status_code=202)
    async def upload(request: Request, file: Annotated[UploadFile, File()]):
        svc = service(request)
        if (svc.capture_thread and svc.capture_thread.is_alive()) or (
            svc.replay_task and not svc.replay_task.done()
        ):
            raise ValueError("入力・台本再生を停止してからWAVを読み込んでください")
        data = await file.read(16 * 1024 * 1024 + 1)
        await file.close()
        if len(data) > 16 * 1024 * 1024:
            raise HTTPException(413, "WAVは16 MiB以下にしてください")
        try:
            with sf.SoundFile(io.BytesIO(data)) as sound:
                if sound.format not in ("WAV", "WAVEX") or sound.channels > 2:
                    raise ValueError("モノラル／ステレオのWAVを選択してください")
                if (
                    not 8000 <= sound.samplerate <= 192000
                    or not 0 < sound.frames <= sound.samplerate * 30
                ):
                    raise ValueError("8〜192 kHz、30秒以下のWAVを選択してください")
                samples = sound.read(dtype="float32", always_2d=True)
                rate = sound.samplerate
            if not np.isfinite(samples).all():
                raise ValueError("不正な音声サンプルです")
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return svc.submit(samples, rate, "recorded_radio", input_device="WAV import")

    @app.post("/api/demo/start")
    async def demo(request: Request, interval: Annotated[float, Query(ge=0.1, le=15)] = 3):
        svc = service(request)
        if svc.capture_thread and svc.capture_thread.is_alive():
            raise ValueError("音声入力を停止してから台本を再生してください")
        if svc.pending or (svc.replay_task and not svc.replay_task.done()):
            raise ValueError("すでに処理・再生中です")
        rows = json.loads((PACKAGE / "fixtures" / "demo.json").read_text(encoding="utf-8"))
        svc.replay_task = asyncio.create_task(svc.replay(rows, interval))
        return {"ok": True, "source": "mock"}

    @app.post("/api/demo/stop")
    async def stop_demo(request: Request):
        svc = service(request)
        if svc.replay_task and not svc.replay_task.done():
            svc.replay_task.cancel()
            try:
                await svc.replay_task
            except asyncio.CancelledError:
                pass
        return {"ok": True}

    @app.get("/api/export")
    def export(request: Request):
        snapshot = service(request).store.snapshot()
        content = "\n".join(
            encode({"kind": kind, "data": row})
            for kind in ("events", "tasks")
            for row in snapshot[kind]
        )
        return StreamingResponse(
            iter([content + "\n"]),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": 'attachment; filename="sumradio.jsonl"'},
        )

    app.mount("/static", StaticFiles(directory=PACKAGE / "static"), name="static")

    @app.get("/")
    def index():
        return FileResponse(PACKAGE / "static" / "index.html")

    return app
