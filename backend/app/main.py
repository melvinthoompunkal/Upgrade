from fastapi import FastAPI
from pydantic import BaseModel

from app import __version__
from app.config import Settings


class HealthResponse(BaseModel):
    status: str
    version: str
    phase: str
    model_provider: str
    model_id: str
    model_configured: bool


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    application = FastAPI(title="Graft Meta", version=__version__)

    @application.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            version=__version__,
            phase="meta_foundation",
            model_provider="meta",
            model_id=settings.meta_model_id,
            model_configured=settings.model_configured,
        )

    return application
