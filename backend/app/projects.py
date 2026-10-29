"""Owner-scoped public project pins. No credentials, model transmission or project execution."""

import hashlib
import os
import shutil
import stat
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from app.api.security import require_local_origin
from app.api.storage import RunStore, now
from app.errors import GraftError
from app.execution import ProcessLock
from app.jobs.control import CURRENT
from app.jobs.resources import Resources
from app.project_contracts import ProjectAnalysisRequest, ProjectRequest, ProjectSelection
from app.project_support import inspect_project
from app.selection_artifacts import prepare_directory
from app.tools.repositories import RepositoryManager
from app.tools.runner import PROJECT_ROOT
from app.tools.runner_artifacts import physical_root

router = APIRouter(prefix="/api/projects")


def inventory_digest(repo) -> str:
    return hashlib.sha256(repo.inventory.model_dump_json().encode()).hexdigest()


class ProjectStore:
    def __init__(self, runs: RunStore, root: Path):
        self.runs, self.root = runs, root

    def get(self, owner: str, project_id: str) -> ProjectSelection:
        with self.runs.connect() as db:
            row = db.execute(
                "SELECT descriptor FROM projects WHERE id=? AND owner_id=?", (project_id, owner)
            ).fetchone()
        if row is None:
            raise HTTPException(404, "Project not found.")
        return ProjectSelection.model_validate_json(row["descriptor"])

    def create(self, owner: str, inputs: ProjectRequest) -> ProjectSelection:
        directory = prepare_directory(self.root) / uuid4().hex
        resources = Resources(self.runs)
        reservation = resources.reserve(owner, "projects", directory.name, 200 * 1024 * 1024)
        if context := CURRENT.get():
            context.storage_roots[directory] = 200 * 1024 * 1024
        prepare_directory(directory)
        try:
            selection = self._fetch(directory, inputs)
            with self.runs.connect() as db:
                db.execute(
                    "INSERT INTO projects VALUES (?,?,?)",
                    (selection.id, owner, selection.model_dump_json()),
                )
            resources.complete(reservation)
            return selection
        except Exception:
            # Only this generated operation directory is eligible for recursive cleanup.
            target = physical_root(directory)
            if target.parent != physical_root(self.root):
                raise ValueError("Public project cleanup escaped its storage root.") from None

            def clear_readonly(operation, path, error):
                if not isinstance(error, PermissionError):
                    raise error
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
                operation(path)

            shutil.rmtree(target, onexc=clear_readonly)
            resources.forget(reservation)
            raise

    def _fetch(self, directory: Path, inputs: ProjectRequest) -> ProjectSelection:
        manager = RepositoryManager(directory / "repositories")
        repo = manager.public_snapshot(
            inputs.url, inputs.commit_sha, role=inputs.role, prefix=inputs.project_root
        )
        if sum(p.stat().st_size for p in directory.rglob("*") if p.is_file()) > 200 * 1024 * 1024:
            from app.errors import RepositoryError

            raise RepositoryError("Public snapshot exceeded the 200 MiB storage limit.")
        repo.verify()
        selection = ProjectSelection(
            id=directory.name,
            requested=inputs,
            snapshot=repo.snapshot,
            support=inspect_project(repo),
            inventory_sha256=inventory_digest(repo),
            root_choices=sorted(
                {
                    (
                        (f"{repo.prefix}/" if repo.prefix else "")
                        + entry.path.removesuffix("pyproject.toml")
                    ).rstrip("/")
                    for entry in repo.inventory.files
                    if entry.path == "pyproject.toml" or entry.path.endswith("/pyproject.toml")
                }
            ),
            created_at=now(),
        )
        return selection

    def open(self, owner: str, project_id: str):
        selected = self.get(owner, project_id)
        inputs = selected.requested
        manager = RepositoryManager(self.root / selected.id / "repositories", existing=True)
        repo = manager.reopen(
            role=inputs.role, url=inputs.url, sha=inputs.commit_sha, prefix=inputs.project_root
        )
        if inventory_digest(repo) != selected.inventory_sha256:
            from app.errors import EvidenceError

            raise EvidenceError("Pinned project inventory changed after preflight.")
        repo.verify()
        # Recompute with the current adapter; never trust a prior capability claim.
        support = inspect_project(repo)
        return selected.model_copy(update={"support": support}), repo

    def admit(self, owner: str, request: ProjectAnalysisRequest) -> None:
        for role, project_id in (
            ("source", request.source_project),
            ("target", request.target_project),
        ):
            if project_id is None:
                continue
            selected = self.get(owner, project_id)
            if selected.requested.role != role:
                raise HTTPException(422, f"Select a project pinned for the {role} role.")
            if selected.support.capability == "browse_index":
                raise HTTPException(
                    409, "This project supports browsing only. Review its preflight."
                )
            if (
                role == "target"
                and request.run_baseline
                and not selected.support.execution_supported
            ):
                raise HTTPException(409, "The target has no supported offline test adapter.")
        if request.run_baseline and request.target_project is None:
            raise HTTPException(422, "Select a target before requesting its baseline.")


def project_store(request: Request) -> ProjectStore:
    return ProjectStore(request.app.state.runs, request.app.state.project_root)


@router.post("", response_model=ProjectSelection, status_code=201)
def pin_project(body: ProjectRequest, request: Request):
    require_local_origin(request)
    try:
        with ProcessLock(PROJECT_ROOT / "data/execution.lock"):
            return project_store(request).create(request.state.principal.owner_id, body)
    except GraftError as error:
        raise HTTPException(409, str(error)) from None
    except OSError:
        raise HTTPException(409, "Public project storage is unavailable.") from None


@router.get("/{project_id}", response_model=ProjectSelection)
def get_project(project_id: str, request: Request):
    return project_store(request).get(request.state.principal.owner_id, project_id)
