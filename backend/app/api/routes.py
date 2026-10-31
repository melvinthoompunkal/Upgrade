"""Local, same-origin run creation and manifest-backed inspection."""

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response

from app.adaptation_inputs import adaptation_inputs
from app.api.admission import authorize_job
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
from app.errors import UnsupportedSourceError
from app.hosting.release import released_profiles, require_job
from app.jobs.policy import operator_allowed
from app.projects import project_store
from app.text_contracts import TEXT_PROFILE
from app.text_support import analysis_profiles
from app.tools.git import Git
from app.tools.runner import PROJECT_ROOT
from app.workflow_inputs import require_crawler_source

router = APIRouter(prefix="/api")


def artifacts(request: Request, run_id: str) -> RunArtifacts:
    return RunArtifacts(
        request.app.state.artifact_root,
        request.app.state.runs.row(run_id, owner_id=request.state.principal.owner_id),
    )


@router.get("/capabilities", response_model=Capabilities)
def capabilities(request: Request):
    settings = request.app.state.settings
    hosted = settings.deployment_mode == "hosted"
    return Capabilities(
        analysis_profiles=analysis_profiles(settings),
        text_analysis_release="opt_in_preview_live_gates_pending"
        if TEXT_PROFILE in analysis_profiles(settings)
        else "not_available",
        kinds=list(released_profiles(settings))
        if hosted
        else [
            "fit_select",
            "source_trace",
            "pinned_baseline",
            "local_baseline",
            "meta_smoke",
            "project_analysis",
            "catalog_discovery",
            "relevance_assessment",
            "verified_adaptation",
        ],
        patch_supported=not hosted,
        publishing_enabled=settings.github_publishing_enabled and not hosted,
        scope="Public Python analysis only; target execution is unavailable."
        if hosted
        else "Public text analysis preview; reviewed Python structure and execution profiles.",
        deployment_mode=settings.deployment_mode,
        target_execution_supported=not hosted,
        model_configured=settings.model_configured,
        model_id=settings.meta_model_id,
        checked_at=now(),
        image_receipt_present=not hosted and (PROJECT_ROOT / "data/runner-image.json").is_file(),
        worker_available=request.app.state.worker.available,
        diagnostics_allowed=operator_allowed(
            request.app.state.runs, request.state.principal.owner_id, settings
        ),
    )


@router.get("/targets", response_model=list[Target])
def targets(request: Request):
    if request.app.state.settings.deployment_mode == "hosted":
        return []
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
    idempotency_key: str = Header(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"),
):
    require_job(request.app.state.settings, body)
    if body.kind in {"fit_select", "source_trace"}:
        try:
            require_crawler_source(body.source_url)
        except UnsupportedSourceError as exc:
            raise HTTPException(422, str(exc)) from None
    if body.kind in {"catalog_discovery", "relevance_assessment"}:
        raise HTTPException(422, "Use the catalog or relevance endpoints to create these jobs.")
    if body.kind == "meta_smoke" and not operator_allowed(
        request.app.state.runs, request.state.principal.owner_id, request.app.state.settings
    ):
        raise HTTPException(403, "Meta diagnostics require a configured operator account.")
    epoch = authorize_job(
        request,
        model_required=body.kind
        in {"fit_select", "source_trace", "meta_smoke", "verified_adaptation"},
    )
    if body.kind == "verified_adaptation":
        adaptation_inputs(
            body,
            project_store(request),
            request.state.principal.owner_id,
            request.app.state.settings.meta_model_id,
        )
    if body.kind == "project_analysis":
        project_store(request).admit(request.state.principal.owner_id, body)
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
