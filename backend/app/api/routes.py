"""Local, same-origin run creation and manifest-backed inspection."""

import re

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response

from app.api.artifacts import RunArtifacts
from app.api.contracts import (
    Artifact,
    ArtifactText,
    Capabilities,
    EventPage,
    ReferenceDetail,
    RunDetail,
    RunPage,
    RunRequest,
    RunSummary,
    Target,
)
from app.api.storage import now
from app.auth.snapshots import access_error
from app.errors import GraftError
from app.tools.git import Git
from app.tools.runner import PROJECT_ROOT

router = APIRouter(prefix="/api")


def artifacts(request: Request, run_id: str) -> RunArtifacts:
    return RunArtifacts(
        request.app.state.artifact_root,
        request.app.state.runs.row(run_id, owner_id=request.state.principal.owner_id),
    )


@router.get("/capabilities", response_model=Capabilities)
def capabilities(request: Request):
    settings = request.app.state.settings
    return Capabilities(
        kinds=["fit_select", "source_trace", "pinned_baseline", "local_baseline", "meta_smoke"],
        model_configured=settings.model_configured,
        model_id=settings.meta_model_id,
        checked_at=now(),
        image_receipt_present=(PROJECT_ROOT / "data/runner-image.json").is_file(),
        worker_available=request.app.state.worker.available,
    )


@router.get("/targets", response_model=list[Target])
def targets():
    target = Target(checked_at=now())
    try:
        git = Git(PROJECT_ROOT / "data/web-git")
        target.head = git.run("rev-parse", "HEAD^{commit}", cwd=PROJECT_ROOT).decode().strip()
        target.dirty = bool(
            git.run(
                "status",
                "--porcelain",
                "--",
                "examples/crawler-target",
                cwd=PROJECT_ROOT,
            )
        )
    except Exception:
        pass
    return [target]


@router.post("/runs", status_code=202, response_model=RunSummary)
def create_run(
    body: RunRequest,
    request: Request,
    response: Response,
    idempotency_key: str = Header(min_length=16, max_length=100),
):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", idempotency_key):
        raise HTTPException(422, "Invalid submission key.")
    origin = request.headers.get("origin")
    allowed = {
        f"http://{host}:{port}" for host in ("127.0.0.1", "localhost") for port in (8000, 5173)
    }
    allowed.add(request.app.state.settings.app_base_url)
    if (origin and origin not in allowed) or request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Start runs from the local Upgrade application.")
    if not request.app.state.worker.available:
        raise HTTPException(503, "The local worker is unavailable. Start one backend process.")
    if request.app.state.settings.auth_mode == "github" and not request.state.principal.connected:
        raise HTTPException(409, "Reconnect GitHub before starting another run.")
    if (
        body.kind in {"fit_select", "source_trace", "meta_smoke"}
        and not request.app.state.settings.model_configured
    ):
        raise HTTPException(
            409, "Configure MODEL_API_KEY in backend/.env before starting a model run."
        )
    epoch = None
    if request.app.state.settings.auth_mode == "github":
        try:
            epoch = request.app.state.auth.current_access(
                request.state.principal.owner_id, request.app.state.github
            )
        except GraftError as error:
            raise access_error(error) from None
    run = request.app.state.runs.enqueue(
        body,
        idempotency_key,
        owner_id=request.state.principal.owner_id,
        require_connected=request.app.state.settings.auth_mode == "github",
        access_epoch=epoch,
    )
    response.headers["Location"] = f"/api/runs/{run.id}"
    return run


@router.get("/runs", response_model=RunPage)
def list_runs(
    request: Request,
    cursor: int = Query(0, ge=0, le=1000000),
    q: str = Query("", max_length=200),
    kind: str | None = Query(None, max_length=30),
):
    items, next_cursor = request.app.state.runs.page(
        owner_id=request.state.principal.owner_id, cursor=cursor, query=q, kind=kind
    )
    return RunPage(items=items, next_cursor=next_cursor)


@router.get("/runs/{run_id}", response_model=RunDetail)
def run_detail(run_id: str, request: Request):
    return artifacts(request, run_id).detail()


@router.get("/runs/{run_id}/status", response_model=RunSummary)
def run_status(run_id: str, request: Request):
    row = request.app.state.runs.row(run_id, owner_id=request.state.principal.owner_id)
    return RunSummary.model_validate_json(row["summary"])


@router.get("/runs/{run_id}/events", response_model=EventPage)
def events(run_id: str, request: Request, after: int = Query(0, ge=0)):
    items = request.app.state.runs.events(run_id, after, owner_id=request.state.principal.owner_id)
    return EventPage(items=items, next_cursor=items[-1].sequence if items else after)


@router.get("/runs/{run_id}/artifacts", response_model=list[Artifact])
def artifact_list(run_id: str, request: Request):
    return artifacts(request, run_id).descriptors()


@router.get("/runs/{run_id}/artifacts/{artifact_id}/text", response_model=ArtifactText)
def artifact_text(run_id: str, artifact_id: str, request: Request):
    bundle = artifacts(request, run_id)
    text = bundle.published(bundle.name_for(artifact_id)).decode("utf-8", errors="replace")
    return ArtifactText(text=text[:100000], truncated=len(text) > 100000)


@router.get("/runs/{run_id}/artifacts/{artifact_id}")
def download(run_id: str, artifact_id: str, request: Request):
    bundle = artifacts(request, run_id)
    name = bundle.name_for(artifact_id)
    return Response(
        bundle.published(name),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{name.split("/")[-1]}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.get("/runs/{run_id}/references/{reference_id}", response_model=ReferenceDetail)
def reference(run_id: str, reference_id: str, request: Request):
    index = artifacts(request, run_id).references()
    if reference_id not in index:
        raise HTTPException(404, "Reference not found in this run's validated evidence.")
    return index[reference_id]
