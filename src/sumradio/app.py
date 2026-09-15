from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .geography_models import AreaInput, AreaSearchInput, LocationConfirmInput
from .models import (
    CorrectionInput,
    ManualTaskInput,
    RecordingStartInput,
    TaskEditInput,
    TaskTransitionInput,
    VersionInput,
)
from .service import SumradioService
from .storage import InvalidTransitionError, NotFoundError, VersionConflictError


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or Settings.from_env()
    service = SumradioService(resolved_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.service = service
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    app = FastAPI(title="Sumradio", version="0.2.0", lifespan=lifespan)
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    def get_service(request: Request) -> SumradioService:
        return request.app.state.service

    def translate_error(exc: Exception) -> HTTPException:
        if isinstance(exc, NotFoundError):
            return HTTPException(status_code=404, detail=f"対象が見つかりません: {exc}")
        if isinstance(exc, VersionConflictError):
            return HTTPException(status_code=409, detail=str(exc))
        if isinstance(exc, InvalidTransitionError):
            return HTTPException(status_code=422, detail=str(exc))
        if isinstance(exc, ValueError):
            return HTTPException(status_code=422, detail=str(exc))
        return HTTPException(status_code=503, detail=str(exc))

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/api/health")
    async def health(request: Request) -> dict:
        current = get_service(request)
        return {"ok": True, "runtime": current.runtime_status(), "diagnostics": current.store.diagnostics}

    @app.get("/api/state")
    async def state(request: Request) -> dict:
        return get_service(request).public_state()

    @app.get("/api/devices")
    async def devices(request: Request) -> dict:
        try:
            return {"devices": await asyncio.to_thread(get_service(request).list_devices)}
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/api/recording/start")
    async def recording_start(data: RecordingStartInput, request: Request) -> dict:
        try:
            return {"recording": True, "device": await get_service(request).start_recording(data.device_id)}
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/api/recording/finalize")
    async def recording_finalize(request: Request) -> dict:
        try:
            return {"finalized": await get_service(request).finalize_recording()}
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/api/recording/stop")
    async def recording_stop(request: Request) -> dict:
        try:
            return {"recording": False, "stopped": await get_service(request).stop_recording()}
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.patch("/api/communications/{communication_id}/transcript")
    async def correct_transcript(communication_id: str, data: CorrectionInput, request: Request) -> dict:
        try:
            record = await get_service(request).correct_communication(communication_id, data)
            return record.model_dump(mode="json")
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/api/communications/{communication_id}/retry")
    async def retry_extraction(communication_id: str, data: VersionInput, request: Request) -> dict:
        try:
            record = await get_service(request).retry_extraction(communication_id, data.version)
            return record.model_dump(mode="json")
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.get("/api/communications/{communication_id}/audio/{kind}")
    async def audio(communication_id: str, kind: str, request: Request) -> FileResponse:
        if kind not in {"original", "recognition"}:
            raise HTTPException(status_code=404, detail="音声種別が不正です")
        try:
            path = get_service(request).store.audio_path(communication_id, kind)
            return FileResponse(path, media_type="audio/wav", filename=f"{communication_id}-{kind}.wav")
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/api/tasks")
    async def create_task(data: ManualTaskInput, request: Request) -> dict:
        try:
            task = await get_service(request).create_manual_task(data)
            return task.model_dump(mode="json")
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.patch("/api/tasks/{task_id}")
    async def edit_task(task_id: str, data: TaskEditInput, request: Request) -> dict:
        try:
            task = await get_service(request).edit_task(task_id, data)
            return task.model_dump(mode="json")
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/api/tasks/{task_id}/transition")
    async def transition_task(task_id: str, data: TaskTransitionInput, request: Request) -> dict:
        try:
            task = await get_service(request).transition_task(task_id, data)
            return task.model_dump(mode="json")
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        return StreamingResponse(
            get_service(request).events.subscribe(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/areas/search")
    async def search_areas(data: AreaSearchInput, request: Request) -> dict:
        try:
            result = await get_service(request).geography.search_areas(data.query)
            return {"areas": [area.model_dump(mode="json") for area in result]}
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.put("/api/area")
    async def set_area(data: AreaInput, request: Request) -> dict:
        try:
            area = await get_service(request).set_area(data)
            return area.model_dump(mode="json")
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.get("/api/tasks")
    async def list_tasks(request: Request, include_discarded: bool = False) -> dict:
        return {"tasks": [task.model_dump(mode="json") for task in get_service(request).store.list_tasks(include_discarded=include_discarded)]}

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str, request: Request) -> dict:
        try:
            return get_service(request).store.get_task(task_id).model_dump(mode="json")
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/api/tasks/{task_id}/location/search")
    async def search_location(task_id: str, data: VersionInput, request: Request) -> dict:
        try:
            task = await get_service(request).retry_location(task_id, data.version)
            return task.model_dump(mode="json")
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.put("/api/tasks/{task_id}/location")
    async def confirm_location(task_id: str, data: LocationConfirmInput, request: Request) -> dict:
        try:
            task = await get_service(request).confirm_location(task_id, data)
            return task.model_dump(mode="json")
        except Exception as exc:
            raise translate_error(exc) from exc

    return app
