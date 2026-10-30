"""Explicit discovery/continuation commands and inference-free saved catalog reads."""

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response

from app.api.admission import authorize_job
from app.catalog_contracts import (
    CatalogContinue,
    CatalogCreate,
    CatalogView,
    EvidenceStatus,
    FeatureDetail,
    FeaturePage,
)
from app.catalog_discovery import discovery_paths
from app.catalog_store import CatalogStore
from app.project_privacy import require_project_analysis
from app.projects import project_store
from app.relevance_contracts import BrowseMode, FeatureSort, FitStatus, RelevanceStatus

router = APIRouter(prefix="/api/catalogs")


def submit(request, response, body, key, catalog_id=None):
    epoch = authorize_job(request)
    owner = request.state.principal.owner_id
    catalogs = CatalogStore(request.app.state.runs)
    projects = project_store(request)
    project_id = (
        catalogs.get(owner, catalog_id, body.base_revision).source_project
        if catalog_id
        else body.source_project
    )
    selected = projects.get(owner, project_id)
    require_project_analysis(selected)
    if selected.requested.role != "source":
        raise HTTPException(422, "Select a project pinned for the source role.")
    if selected.support.capability == "browse_index":
        raise HTTPException(409, "Source supports browsing only. Review its preflight.")
    if isinstance(body, CatalogCreate) and body.scope:
        _, repo = projects.open(owner, project_id)
        if not discovery_paths(repo, body.scope):
            raise HTTPException(422, "No supported discovery files exist in this scope.")
    settings = request.app.state.settings
    view = catalogs.submit(
        owner,
        selected,
        body,
        key,
        settings.meta_model_id,
        catalog_id=catalog_id,
        require_connected=settings.auth_mode == "github",
        access_epoch=epoch,
        model_configured=settings.model_configured,
    )
    response.headers["Location"] = f"/api/catalogs/{view.id}?revision={view.revision}"
    return view


@router.post("", response_model=CatalogView, status_code=202)
def create_catalog(
    body: CatalogCreate,
    request: Request,
    response: Response,
    idempotency_key: str = Header(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"),
):
    return submit(request, response, body, idempotency_key)


@router.post("/{catalog_id}/continuations", response_model=CatalogView, status_code=202)
def continue_catalog(
    catalog_id: str,
    body: CatalogContinue,
    request: Request,
    response: Response,
    idempotency_key: str = Header(min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"),
):
    if len(body.trace_feature_ids) > body.options.trace_limit:
        raise HTTPException(422, "Requested features exceed the declared trace limit.")
    return submit(request, response, body, idempotency_key, catalog_id)


@router.get("/{catalog_id}", response_model=CatalogView)
def catalog(catalog_id: str, request: Request, revision: int | None = Query(None, ge=1)):
    return CatalogStore(request.app.state.runs).get(
        request.state.principal.owner_id,
        catalog_id,
        revision,
    )


@router.get("/{catalog_id}/features", response_model=FeaturePage)
def features(
    catalog_id: str,
    request: Request,
    revision: int = Query(ge=1),
    q: str = Query("", max_length=200),
    status: EvidenceStatus | None = None,
    category: str | None = Query(None, max_length=60),
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None, max_length=2048),
    assessment_id: str | None = Query(None, pattern=r"^[a-f0-9]{32}$"),
    mode: BrowseMode = "all",
    sort: FeatureSort | None = None,
    relevance_status: RelevanceStatus | None = None,
    fit_status: FitStatus | None = None,
):
    return CatalogStore(request.app.state.runs).page(
        request.state.principal.owner_id,
        catalog_id,
        revision,
        q=q,
        status=status,
        category=category,
        limit=limit,
        cursor=cursor,
        assessment_id=assessment_id,
        mode=mode,
        sort=sort,
        relevance_status=relevance_status,
        fit_status=fit_status,
    )


@router.get("/{catalog_id}/features/{feature_id}", response_model=FeatureDetail)
def feature(
    catalog_id: str,
    feature_id: str,
    request: Request,
    revision: int = Query(ge=1),
):
    return CatalogStore(request.app.state.runs).feature(
        request.state.principal.owner_id,
        catalog_id,
        revision,
        feature_id,
    )
