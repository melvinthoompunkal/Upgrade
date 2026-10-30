"""Reading review is inert; only the CSRF-protected, explicit POST can publish."""

import json

from fastapi import APIRouter, Header, HTTPException, Request

from app.api.change_review import SavedChanges
from app.api.routes import artifacts
from app.auth.repositories import require_connection
from app.auth.snapshots import access_error
from app.deployment import execution_lock_path
from app.errors import GraftError
from app.execution import ProcessLock
from app.publishing.contracts import Publication, PublishRequest, PublishReview
from app.publishing.patch import PublishPatch
from app.publishing.store import PublicationStore
from app.publishing.workflow import publish

router = APIRouter(prefix="/api/runs/{run_id}/pull-request")


@router.get("", response_model=PublishReview)
def publishing_review(run_id: str, request: Request):
    saved = SavedChanges(artifacts(request, run_id))
    state, owner = request.app.state, request.state.principal.owner_id
    enabled = state.settings.github_publishing_enabled and saved.review.verdict == "verified"
    view = PublishReview(
        enabled=enabled,
        reason="Review the exact patch and notices before creating a draft pull request."
        if enabled
        else "Pull request publishing is disabled. Download the patch and notices for review.",
        publication=PublicationStore(state.runs).get(owner, run_id),
    )
    if saved.review.verdict == "verified":
        patch = PublishPatch(saved)
        view.target_origin, view.base_sha = patch.target.url, patch.target.commit_sha
        view.patch_sha256, view.notices_sha256 = patch.patch_sha256, patch.notices_sha256
        with state.runs.connect() as db:
            rows = db.execute(
                "SELECT data FROM repository_cache WHERE owner_id=?", (owner,)
            ).fetchall()
        name = patch.target.url.removeprefix("https://github.com/").removesuffix(".git")
        for row in rows:
            repo = json.loads(row[0])
            if repo["full_name"].casefold() == name.casefold():
                view.repository_id, view.base_branch = repo["id"], repo["default_branch"]
                break
    return view


@router.post("", response_model=Publication)
def publish_pull_request(
    run_id: str,
    body: PublishRequest,
    request: Request,
    idempotency_key: str = Header(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_-]+$"),
):
    state = request.app.state
    if not state.settings.github_publishing_enabled:
        raise HTTPException(409, "Pull request publishing is disabled by the operator.")
    owner = require_connection(request)
    try:
        with ProcessLock(execution_lock_path(state.settings)):
            patch = PublishPatch(SavedChanges(artifacts(request, run_id)))
            if (
                body.patch_sha256 != patch.patch_sha256
                or body.notices_sha256 != patch.notices_sha256
            ):
                raise HTTPException(
                    409, "The reviewed patch or notices changed. Reopen change review."
                )
            return publish(state, owner, run_id, idempotency_key, body, patch)
    except GraftError as error:
        raise access_error(error) from None
