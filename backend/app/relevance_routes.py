"""Owner-scoped commands and saved assessment reads."""

from fastapi import APIRouter, Header, HTTPException, Request, Response

from app.api.admission import authorize_job
from app.catalog_store import CatalogStore
from app.project_privacy import require_project_analysis
from app.projects import project_store
from app.relevance_contracts import AssessmentCreate, AssessmentView, FeatureAssessment
from app.relevance_store import RelevanceStore

router = APIRouter(prefix="/api/relevance-assessments")


@router.post("", response_model=AssessmentView, status_code=202)
def create_assessment(
    body: AssessmentCreate,
    request: Request,
    response: Response,
    idempotency_key: str = Header(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"),
):
    epoch = authorize_job(request)
    owner, runs = request.state.principal.owner_id, request.app.state.runs
    catalog = CatalogStore(runs).get(owner, body.catalog_id, body.catalog_revision)
    require_project_analysis(project_store(request).get(owner, catalog.source_project))
    target = project_store(request).get(owner, body.target_project) if body.target_project else None
    if target:
        require_project_analysis(target)
    if target and target.requested.role != "target":
        raise HTTPException(422, "Select a project pinned for the target role.")
    if target and target.support.capability == "browse_index":
        raise HTTPException(409, "Target supports browsing only. Review its preflight.")
    settings = request.app.state.settings
    view = RelevanceStore(runs).submit(
        owner,
        catalog,
        target,
        body,
        idempotency_key,
        settings.meta_model_id,
        model_configured=settings.model_configured,
        require_connected=settings.auth_mode == "github",
        access_epoch=epoch,
    )
    response.headers["Location"] = f"/api/relevance-assessments/{view.id}"
    return view


@router.get("/{assessment_id}", response_model=AssessmentView)
def assessment(assessment_id: str, request: Request):
    return RelevanceStore(request.app.state.runs).get(
        request.state.principal.owner_id, assessment_id
    )


@router.get("/{assessment_id}/features/{feature_id}", response_model=FeatureAssessment)
def feature_assessment(assessment_id: str, feature_id: str, request: Request):
    store = RelevanceStore(request.app.state.runs)
    owner = request.state.principal.owner_id
    view = store.get(owner, assessment_id)
    return store.feature(owner, view.id, view.catalog_id, view.catalog_revision, feature_id)
