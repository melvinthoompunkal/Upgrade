import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

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
from app.auth.crypto import CredentialCipher
from app.auth.github import GitHubClient
from app.auth.installations import router as installation_router
from app.auth.middleware import SessionMiddleware
from app.auth.repositories import router as repository_router
from app.auth.routes import router as auth_router
from app.auth.snapshots import router as snapshot_router
from app.auth.store import AuthStore
from app.auth.webhooks import router as webhook_router
from app.config import Settings
from app.selection_artifacts import prepare_directory
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
        app.state.private_repository_root = data_root / "private-repositories"
        prepare_directory(app.state.artifact_root)
        app.state.runs = RunStore(data_root / "web/runs.sqlite3")
        if settings.auth_mode == "local":
            await asyncio.to_thread(import_history, app.state.runs, app.state.artifact_root)
        if settings.auth_mode == "github":
            app.state.auth = AuthStore(
                app.state.runs, CredentialCipher(settings.token_encryption_keys.get_secret_value())
            )
            app.state.github = GitHubClient(settings)
        app.state.worker = LocalWorker(
            app.state.runs,
            app.state.artifact_root,
            settings,
            connection_check=(lambda owner: app.state.auth.current_access(owner, app.state.github))
            if settings.auth_mode == "github"
            else None,
        )
        if start_worker:
            app.state.worker.start()
        yield
        await asyncio.to_thread(app.state.worker.stop)

    application = FastAPI(title="Upgrade", version=__version__, lifespan=lifespan)
    application.add_middleware(SessionMiddleware)
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver", "test"]
        if settings.auth_mode == "local"
        else [urlsplit(settings.app_base_url).hostname],
    )
    application.include_router(auth_router)
    application.include_router(repository_router)
    application.include_router(webhook_router)
    application.include_router(snapshot_router)
    application.include_router(installation_router)
    application.include_router(router)

    @application.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            version=__version__,
            phase="connected_analysis_foundations",
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
                or path in {"runs", "system", "repositories"}
                or (
                    path.startswith("runs/")
                    and all(part not in {"..", "."} for part in path.split("/"))
                )
            ):
                return FileResponse(frontend / "index.html")
            raise HTTPException(404, "Not found")

    return application
