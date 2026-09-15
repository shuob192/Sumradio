import asyncio
import json
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Settings
from .models import (
    ConfirmInterpretationLocation,
    ConfirmLocation,
    CorrectRequest,
    DisplayMetric,
    Location,
    Profile,
    RecordingStart,
    Run,
    RunCreate,
    RunView,
    Task,
    TaskCreate,
    TaskEdit,
    TransitionRequest,
    Versioned,
)
from .regions import PREFECTURES
from .service import Service
from .storage import Conflict


def create_app(settings: Settings | None = None, service: Service | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        app.state.service = service or Service(settings)
        await app.state.service.start()
        try:
            yield
        finally:
            await app.state.service.close()

    app = FastAPI(title="Sumradio", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"]
    )

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            parsed = urlsplit(origin)
            if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                return JSONResponse({"detail": "このPCから操作してください"}, status_code=403)
        return await call_next(request)

    def svc() -> Service:
        return app.state.service

    @app.exception_handler(Conflict)
    async def conflict(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(KeyError)
    async def missing(_, exc):
        return JSONResponse({"detail": "対象が見つかりません"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/api/health")
    async def health():
        s = svc()
        return {
            "whisper": s.speech.engine.state,
            "whisper_error": s.speech.engine.error,
            "phonetic_error": s.table_error,
            "storage_errors": s.store.errors[-10:],
            "codex_available": bool(shutil.which(settings.codex_bin)),
            "codex_model": settings.codex_model,
            "recording_run_id": s.mic.run_id,
            "audio_error": s.mic.error,
            "extraction_pending": s.extraction_queue.qsize(),
            "tile_url": settings.tile_url,
            "data_dir": str(settings.data_dir),
        }

    @app.get("/api/profiles", response_model=list[Profile])
    async def profiles():
        return sorted(svc().profiles.values(), key=lambda p: (not p.training, p.id))

    @app.get("/api/runs")
    async def runs():
        return [
            {
                "id": r.id,
                "demo_profile_id": r.demo_profile_id,
                "title": r.title,
                "created_at": r.created_at,
                "version": r.version,
                "region": r.region.model_dump() if r.region else None,
            }
            for r in reversed(svc().store.all())
        ]

    @app.post("/api/runs", response_model=Run)
    async def create(body: RunCreate):
        return svc().create_run(body.demo_profile_id, body.title, body.region)

    @app.get("/api/regions")
    async def regions():
        return {"prefectures": PREFECTURES}

    @app.get("/api/runs/{run_id}/locations", response_model=list[Location])
    async def run_locations(run_id: str):
        run = svc().store.get(run_id)
        return svc().resolver.registered_locations(run.demo_profile_id, run.region)

    @app.get("/api/runs/{run_id}", response_model=RunView)
    async def get_run(run_id: str):
        return svc().view_run(run_id)

    @app.get("/api/devices")
    async def devices():
        try:
            return await asyncio.to_thread(svc().mic.devices)
        except Exception as exc:
            raise HTTPException(503, f"マイクを取得できません: {exc}") from exc

    @app.post("/api/runs/{run_id}/recording/start")
    async def start_recording(run_id: str, body: RecordingStart):
        svc().store.get(run_id)
        try:
            svc().mic.start(run_id, body.device, body.manual_mode)
        except Exception as exc:
            raise HTTPException(409 if svc().mic.run_id else 503, str(exc)) from exc
        svc().publish({"type": "health"})
        return {"recording_run_id": run_id}

    @app.post("/api/runs/{run_id}/recording/stop")
    async def stop_recording(run_id: str):
        if svc().mic.run_id != run_id:
            raise Conflict("この実施回は録音していません")
        await svc().mic.stop()
        svc().publish({"type": "health"})
        return {"recording_run_id": None}

    @app.post("/api/runs/{run_id}/communications/{comm_id}/corrections", response_model=Run)
    async def correct(run_id: str, comm_id: str, body: CorrectRequest):
        return svc().correct(run_id, comm_id, body)

    @app.post("/api/runs/{run_id}/communications/{comm_id}/retry")
    async def retry(run_id: str, comm_id: str, body: Versioned):
        from .storage import check_version

        comm = svc().store.get(run_id).communications[comm_id]
        check_version(comm, body.expected_version)
        if not comm.text.strip() or comm.transcription_status != "done":
            raise ValueError("確定した文字記録が必要です")
        svc().enqueue_extraction(run_id, comm_id)
        return {"queued": True}

    @app.post("/api/runs/{run_id}/tasks", response_model=Task)
    async def create_task(run_id: str, body: TaskCreate):
        return svc().create_task(run_id, body)

    @app.post("/api/runs/{run_id}/communications/{comm_id}/display-metrics")
    async def display_metric(run_id: str, comm_id: str, body: DisplayMetric):
        svc().display_metric(run_id, comm_id, body)
        return {"recorded": True}

    @app.put("/api/runs/{run_id}/tasks/{task_id}", response_model=Task)
    async def edit_task(run_id: str, task_id: str, body: TaskEdit):
        return svc().edit_task(run_id, task_id, body)

    @app.post("/api/runs/{run_id}/tasks/{task_id}/transition", response_model=Task)
    async def transition(run_id: str, task_id: str, body: TransitionRequest):
        return svc().transition(run_id, task_id, body)

    @app.post("/api/runs/{run_id}/tasks/{task_id}/location", response_model=Task)
    async def location(run_id: str, task_id: str, body: ConfirmLocation):
        return svc().confirm_location(run_id, task_id, body)

    @app.post("/api/runs/{run_id}/tasks/{task_id}/interpretation-location", response_model=Task)
    async def interpretation_location(
        run_id: str, task_id: str, body: ConfirmInterpretationLocation
    ):
        return svc().confirm_interpretation_location(run_id, task_id, body)

    @app.get("/api/runs/{run_id}/audio/{comm_id}/{variant}")
    async def audio(run_id: str, comm_id: str, variant: str):
        comm = svc().store.get(run_id).communications[comm_id]
        name = (
            comm.original_audio
            if variant == "original"
            else comm.recognition_audio
            if variant == "16k"
            else None
        )
        if not name:
            raise HTTPException(404)
        path = svc().store.run_dir(run_id) / "audio" / Path(name).name
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path, media_type="audio/wav")

    @app.get("/api/events")
    async def events(request: Request):
        subscriber = asyncio.Queue(maxsize=128)
        svc().subscribers.add(subscriber)

        async def generate():
            try:
                yield 'data: {"type":"resync"}\n\n'
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(subscriber.get(), timeout=15)
                        yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                    except TimeoutError:
                        yield ": heartbeat\n\n"
            finally:
                svc().subscribers.discard(subscriber)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    dist = settings.resources / "frontend/dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app
