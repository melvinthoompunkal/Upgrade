"""Owner-scoped, manifest-backed change review. Publishing remains disabled."""

from fastapi import APIRouter, Query, Request

from app.api.change_review import SavedChanges
from app.api.routes import artifacts
from app.change_review_contracts import ChangeReview, DiffPage

router = APIRouter(prefix="/api/runs/{run_id}/changes")


@router.get("", response_model=ChangeReview)
def change_review(run_id: str, request: Request):
    return SavedChanges(artifacts(request, run_id)).review


@router.get("/files/{file_id}", response_model=DiffPage)
def change_file(
    run_id: str,
    file_id: str,
    request: Request,
    offset: int = Query(0, ge=0, le=100_000),
    limit: int = Query(200, ge=1, le=200),
):
    return SavedChanges(artifacts(request, run_id)).page(file_id, offset, limit)
