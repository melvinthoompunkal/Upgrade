"""Explicit GitHub snapshot retrieval. No model calls or repository execution."""

import hashlib
import os
import shutil
import stat
import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth.access import RepositoryAccess, RepositoryAccessDenied
from app.auth.git_broker import git_credential_channel
from app.auth.github import GitHubRateLimited
from app.auth.repositories import require_connection
from app.contracts import validate_repo_path
from app.errors import GraftError, RepositoryError
from app.execution import ProcessLock
from app.jobs.control import CURRENT
from app.jobs.resources import Resources
from app.selection_artifacts import prepare_directory
from app.tools.repositories import RepositoryManager
from app.tools.runner_artifacts import physical_root

router = APIRouter(prefix="/api")


class SnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    repository_id: int = Field(gt=0)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
    role: Literal["source", "target"]
    project_root: str = Field(default="", max_length=300)

    @field_validator("project_root")
    @classmethod
    def root_path(cls, value):
        if value:
            validate_repo_path(value)
        return value


class SnapshotView(BaseModel):
    id: str
    repository_id: int
    installation_id: int
    full_name: str
    private: bool
    commit_sha: str
    role: Literal["source", "target"]
    project_root: str
    capability: Literal["browse_index"] = "browse_index"
    file_count: int
    text_bytes: int
    inventory_sha256: str
    limits_reached: list[str]
    created_at: float


class SnapshotService:
    def __init__(self, root: Path, access: RepositoryAccess):
        self.root, self.access = root, access

    def create(self, owner: str, inputs: SnapshotRequest) -> SnapshotView:
        grant = self.access.authorize(owner, inputs.repository_id)
        if grant.repository.size > 100 * 1024:
            raise RepositoryError("Repository exceeds the 100 MiB retrieval preflight limit.")
        prepare_directory(self.root)
        # Share the workspace operation lock with CLI/API execution; concurrent snapshots
        # are deliberately excluded in this local stage.
        snapshot_id = uuid4().hex
        directory = self.root / snapshot_id
        resources = Resources(self.access.runs)
        reservation = resources.reserve(
            owner, "private-repositories", snapshot_id, 200 * 1024 * 1024
        )
        if context := CURRENT.get():
            context.storage_roots[directory] = 200 * 1024 * 1024
        directory.mkdir()
        success = False
        credential = None
        try:
            manager = RepositoryManager(directory / "repositories")
            credential = self.access.github.installation_token(
                grant.installation, inputs.repository_id
            )
            self.access.check(grant)
            with git_credential_channel(directory, credential.token.get_secret_value()) as channel:
                repo = manager.fetch_snapshot(
                    f"https://github.com/{grant.repository.full_name}.git",
                    inputs.commit_sha,
                    role=inputs.role,
                    prefix=inputs.project_root,
                    credential_channel=channel,
                )
            if (
                sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())
                > 200 * 1024 * 1024
            ):
                raise RepositoryError("Fetched snapshot exceeded the 200 MiB storage limit.")
            if not repo.inventory.files:
                raise RepositoryError("No supported text files at the selected project root.")
            repo.verify()
            # A successful fetch is not proof of the user's continuing access.
            fresh = self.access.authorize(owner, inputs.repository_id)
            if fresh.epoch != grant.epoch or fresh.installation != grant.installation:
                raise RepositoryAccessDenied()
            view = SnapshotView(
                id=snapshot_id,
                repository_id=inputs.repository_id,
                installation_id=grant.installation,
                full_name=fresh.repository.full_name,
                private=fresh.repository.private,
                commit_sha=inputs.commit_sha,
                role=inputs.role,
                project_root=inputs.project_root,
                file_count=len(repo.inventory.files),
                text_bytes=sum(f.bytes for f in repo.inventory.files),
                inventory_sha256=hashlib.sha256(
                    repo.inventory.model_dump_json().encode()
                ).hexdigest(),
                limits_reached=list(repo.inventory.limits_reached),
                created_at=time.time(),
            )
            with self.access.runs.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                self.access.check_in(db, owner, grant.epoch)
                db.execute(
                    "INSERT INTO repository_snapshots VALUES (?,?,?,?,?,?)",
                    (
                        snapshot_id,
                        owner,
                        inputs.repository_id,
                        grant.installation,
                        view.model_dump_json(),
                        view.created_at,
                    ),
                )
            success = True
            resources.complete(reservation)
            return view
        finally:
            if credential:
                try:
                    self.access.github.revoke_installation_token(
                        credential.token.get_secret_value()
                    )
                except GraftError:
                    # Expiry bounds provider lifetime; no credential is retained for reuse.
                    pass
            if not success:
                # Only remove this operation's generated directory, after physical checks.
                physical_root(directory)
                if directory.parent == self.root:

                    def clear_readonly(operation, path, error):
                        if not isinstance(error, PermissionError):
                            raise error
                        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
                        operation(path)

                    shutil.rmtree(directory, onexc=clear_readonly)
                    resources.forget(reservation)

    def get(self, owner: str, snapshot_id: str) -> SnapshotView:
        with self.access.runs.connect() as db:
            row = db.execute(
                "SELECT descriptor FROM repository_snapshots WHERE id=? AND owner_id=?",
                (snapshot_id, owner),
            ).fetchone()
        if not row:
            raise RepositoryAccessDenied()
        view = SnapshotView.model_validate_json(row["descriptor"])
        grant = self.access.authorize(owner, view.repository_id)
        self.access.check(grant)
        return view.model_copy(
            update={
                "full_name": grant.repository.full_name,
                "private": grant.repository.private,
            }
        )

    def open(self, owner: str, snapshot_id: str):
        view = self.get(owner, snapshot_id)
        manager = RepositoryManager(self.root / view.id / "repositories", existing=True)
        repo = manager.reopen(
            role=view.role,
            url=f"https://github.com/{view.full_name}.git",
            sha=view.commit_sha,
            prefix=view.project_root,
        )
        if hashlib.sha256(repo.inventory.model_dump_json().encode()).hexdigest() != (
            view.inventory_sha256
        ):
            raise RepositoryError("Snapshot inventory differs from its immutable receipt.")
        repo.verify()
        self.get(owner, snapshot_id)
        return view, repo


def service(request: Request) -> SnapshotService:
    state = request.app.state
    if not state.settings.github_private_fetch_enabled:
        raise HTTPException(409, "Private snapshot retrieval is disabled. See GitHub setup.")
    return SnapshotService(
        state.private_repository_root, RepositoryAccess(state.runs, state.auth, state.github)
    )


def access_error(error: GraftError):
    if isinstance(error, GitHubRateLimited):
        return HTTPException(429, str(error), headers={"Retry-After": str(error.retry_after)})
    return HTTPException(404 if isinstance(error, RepositoryAccessDenied) else 409, str(error))


@router.post("/repository-snapshots", response_model=SnapshotView, status_code=201)
def create_snapshot(body: SnapshotRequest, request: Request):
    owner = require_connection(request)
    provider = service(request)
    from app.tools.runner import PROJECT_ROOT

    try:
        with ProcessLock(PROJECT_ROOT / "data/execution.lock"):
            return provider.create(owner, body)
    except GraftError as error:
        raise access_error(error) from None
    except OSError:
        raise HTTPException(409, "Snapshot storage is unavailable. Try again later.") from None


@router.get("/repository-snapshots/{snapshot_id}", response_model=SnapshotView)
def get_snapshot(snapshot_id: str, request: Request):
    owner = require_connection(request)
    try:
        return service(request).get(owner, snapshot_id)
    except GraftError as error:
        raise access_error(error) from None
