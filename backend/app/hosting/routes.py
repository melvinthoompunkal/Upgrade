"""Readiness and operator-only metadata. No browser deployment mutation API."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.hosting.release import released_profiles
from app.jobs.operations import metrics
from app.jobs.policy import operator_allowed

router = APIRouter()


@router.get("/ready", include_in_schema=False)
def readiness(request: Request):
    state = request.app.state
    ready = state.worker.available and (
        state.settings.deployment_mode == "local" or bool(released_profiles(state.settings))
    )
    return JSONResponse(
        {"status": "ready" if ready else "unavailable"},
        status_code=200 if ready else 503,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/operations/metrics")
def operator_metrics(request: Request):
    state = request.app.state
    if not operator_allowed(state.runs, request.state.principal.owner_id, state.settings):
        raise HTTPException(403, "Operator access is required.")
    return {
        **metrics(state.runs),
        "worker_available": state.worker.available,
        "deployment_mode": state.settings.deployment_mode,
        "released_profiles": released_profiles(state.settings),
    }
