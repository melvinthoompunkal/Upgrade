import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.api.artifacts import import_history
from app.api.routes import router
from app.api.storage import RunStore
from app.api.worker import LocalWorker
from app.config import Settings
from app.tools.runner import PROJECT_ROOT


class HealthResponse(BaseModel):
    status: str
    version: str
    phase: str
    model_provider: str
    model_id: str
    model_configured: bool


def create_app(
    settings: Settings | None = None, *, data_root: Path | None = None, start_worker: bool = True
) -> FastAPI:
    settings = settings if settings is not None else Settings()
    data_root = data_root or PROJECT_ROOT / "data"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.artifact_root = data_root / "artifacts"
        app.state.artifact_root.mkdir(parents=True, exist_ok=True)
        app.state.runs = RunStore(data_root / "web/runs.sqlite3")
        await asyncio.to_thread(import_history, app.state.runs, app.state.artifact_root)
        app.state.worker = LocalWorker(app.state.runs, app.state.artifact_root, settings)
        if start_worker:
            app.state.worker.start()
        yield
        await asyncio.to_thread(app.state.worker.stop)

    application = FastAPI(title="Upgrade", version=__version__, lifespan=lifespan)
    application.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver", "test"]
    )
    application.include_router(router)

    @application.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            version=__version__,
            phase="source_tracing",
            model_provider="meta",
            model_id=settings.meta_model_id,
            model_configured=settings.model_configured,
        )

    frontend = PROJECT_ROOT / "frontend/dist"
    if (frontend / "index.html").is_file():
        application.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

        @application.get("/favicon.svg", include_in_schema=False)
        def favicon():
            return FileResponse(frontend / "favicon.svg")

        @application.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if (
                path == ""
                or path in {"runs", "system"}
                or (
                    path.startswith("runs/")
                    and all(part not in {"..", "."} for part in path.split("/"))
                )
            ):
                return FileResponse(frontend / "index.html")
            raise HTTPException(404, "Not found")

    return application
