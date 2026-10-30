"""Shared local worker and connected-account admission checks."""

from fastapi import HTTPException

from app.api.security import require_local_origin
from app.auth.snapshots import access_error
from app.errors import GraftError


def authorize_job(request, *, model_required: bool = False) -> int | None:
    require_local_origin(request)
    if not request.app.state.worker.available:
        raise HTTPException(503, "The worker is unavailable. Retry when the service is ready.")
    settings = request.app.state.settings
    if model_required and not settings.model_configured:
        raise HTTPException(409, "Configure MODEL_API_KEY before starting a model run.")
    if settings.auth_mode == "github":
        if not request.state.principal.connected:
            raise HTTPException(409, "Reconnect GitHub before starting another run.")
        try:
            return request.app.state.auth.current_access(
                request.state.principal.owner_id, request.app.state.github
            )
        except GraftError as error:
            raise access_error(error) from None
    return None
