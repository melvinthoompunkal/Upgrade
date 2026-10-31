"""Owner-scoped public project pins. No credentials, model transmission or project execution."""

import hashlib
import os
import shutil
import stat
from pathlib import Path, PurePosixPath
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from app.api.security import require_local_origin
from app.api.storage import RunStore, now
from app.deployment import execution_lock_path
from app.errors import GraftError
from app.execution import ProcessLock
from app.hosting.release import require_release
from app.jobs.control import CURRENT
from app.jobs.resources import Resources
from app.project_contracts import (
    PinnedProjectRequest,
    ProjectAnalysisRequest,
    ProjectRequest,
    ProjectSelection,
)
from app.project_privacy import require_project_analysis
from app.project_refs import RefRequest, ResolvedRef, resolve_ref
from app.project_support import inspect_project
from app.selection_artifacts import prepare_directory
from app.support_matrix import SupportMatrix
from app.text_contracts import (
    LEGACY_POLICY,
    LEGACY_TEXT_POLICY,
    TEXT_POLICY,
    TEXT_PROFILE,
    TextInventory,
)
from app.text_inventory import MANIFESTS
from app.text_support import analysis_profiles
from app.tools.repositories import RepositoryManager
from app.tools.runner_artifacts import physical_root

router = APIRouter(prefix="/api/projects")


def inventory_digest(repo) -> str:
    # v1 serialized text entries through InventoryFile and omitted subclass metadata.
    # Preserve those historical identities while v2 binds the complete text contract.
    exclude = (
        {"files": {"__all__": {"role", "classification_basis", "blob_oid", "lines"}}}
        if isinstance(repo.inventory, TextInventory)
        and repo.inventory.policy_version == LEGACY_TEXT_POLICY
        else None
    )
    return hashlib.sha256(repo.inventory.model_dump_json(exclude=exclude).encode()).hexdigest()


class ProjectStore:
    def __init__(self, runs: RunStore, root: Path, *, snapshots=None):
        self.runs, self.root = runs, root
        self.snapshots = snapshots

    def get(self, owner: str, project_id: str) -> ProjectSelection:
        with self.runs.connect() as db:
            row = db.execute(
                "SELECT descriptor FROM projects WHERE id=? AND owner_id=?", (project_id, owner)
            ).fetchone()
        if row is None:
            raise HTTPException(404, "Project not found.")
        selected = ProjectSelection.model_validate_json(row["descriptor"])
        if selected.repository_snapshot:
            if self.snapshots is None:
                raise HTTPException(404, "Project not found.")
            from app.auth.snapshots import access_error

            try:
                view = self.snapshots.get(owner, selected.repository_snapshot)
            except GraftError as error:
                raise access_error(error) from None
            # Visibility changes cannot turn an authorized snapshot into an unchecked public pin.
            selected = selected.model_copy(update={"private": view.private})
        return selected

    def from_snapshot(self, owner: str, snapshot_id: str) -> ProjectSelection:
        if self.snapshots is None:
            raise HTTPException(409, "Authorized snapshot inspection is disabled.")
        epoch = self.snapshots.access.epoch(owner)
        view, repo = self.snapshots.open(owner, snapshot_id)
        selection = ProjectSelection(
            id=uuid4().hex,
            requested=PinnedProjectRequest(
                url=f"https://github.com/{view.full_name}",
                commit_sha=view.commit_sha,
                role=view.role,
                project_root=view.project_root,
            ),
            snapshot=repo.snapshot,
            support=inspect_project(repo),
            inventory_sha256=inventory_digest(repo),
            root_choices=[view.project_root],
            created_at=now(),
            repository_snapshot=view.id,
            private=view.private,
        )
        # No second code copy: the descriptor is an access-checked alias of the F2 snapshot.
        fresh = self.snapshots.get(owner, snapshot_id)
        if fresh.inventory_sha256 != selection.inventory_sha256:
            raise HTTPException(409, "Snapshot inventory changed during inspection.")
        with self.runs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.snapshots.access.check_in(db, owner, epoch)
            db.execute(
                "INSERT INTO projects VALUES (?,?,?)",
                (selection.id, owner, selection.model_dump_json()),
            )
        return selection

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
        policy = TEXT_POLICY if inputs.analysis_profile == TEXT_PROFILE else LEGACY_POLICY
        manager = RepositoryManager(directory / "repositories", inventory_policy=policy)
        repo = manager.public_snapshot(
            inputs.url, inputs.commit_sha, role=inputs.role, prefix=inputs.project_root
        )
        if sum(p.stat().st_size for p in directory.rglob("*") if p.is_file()) > 200 * 1024 * 1024:
            from app.errors import RepositoryError

            raise RepositoryError("Public snapshot exceeded the 200 MiB storage limit.")
        repo.verify()
        selection = ProjectSelection(
            id=directory.name,
            requested=PinnedProjectRequest(
                **{**inputs.model_dump(), "commit_sha": repo.snapshot.commit_sha}
            ),
            snapshot=repo.snapshot,
            support=inspect_project(repo),
            inventory_sha256=inventory_digest(repo),
            inventory_policy=policy,
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
        if isinstance(repo.inventory, TextInventory):
            hints = {repo.prefix}
            for entry in repo.inventory.files:
                if PurePosixPath(entry.path).name in MANIFESTS:
                    hints.add(str(PurePosixPath(entry.path).parent) if "/" in entry.path else "")
                relative = entry.path[len(repo.prefix) + 1 :] if repo.prefix else entry.path
                if entry.role == "source" and "/" in relative:
                    module = relative.split("/", 1)[0]
                    hints.add(f"{repo.prefix}/{module}" if repo.prefix else module)
            selection.root_choices = [repo.prefix, *sorted(hints - {repo.prefix})[:39]]
        return selection

    def open(self, owner: str, project_id: str):
        selected = self.get(owner, project_id)
        require_project_analysis(selected)
        inputs = selected.requested
        manager = RepositoryManager(
            self.root / selected.id / "repositories",
            existing=True,
            inventory_policy=selected.inventory_policy,
        )
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
            require_project_analysis(selected)
            if selected.requested.role != role:
                raise HTTPException(422, f"Select a project pinned for the {role} role.")
            if selected.support.profile_version == TEXT_PROFILE:
                raise HTTPException(409, "Structure only requires the Python evidence profile.")
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
    from app.auth.snapshots import service

    snapshots = (
        service(request) if request.app.state.settings.github_private_fetch_enabled else None
    )
    return ProjectStore(request.app.state.runs, request.app.state.project_root, snapshots=snapshots)


@router.post("/resolve-ref", response_model=ResolvedRef)
def resolve_project_ref(body: RefRequest, request: Request):
    from app.auth.snapshots import access_error

    require_local_origin(request)
    require_release(request.app.state.settings)
    try:
        return resolve_ref(body, request)
    except GraftError as error:
        raise access_error(error) from None


@router.get("/support-matrix", response_model=SupportMatrix)
def support_matrix(request: Request):
    if request.app.state.settings.deployment_mode == "hosted":
        return SupportMatrix(
            analysis_profiles=["python-evidence-v1"],
            text_analysis_release="not_available",
            runtime_profile="public-python-analysis-only",
            runtime_packages={},
            native_adapters=[],
            acceptance_adapters=[],
            source_use_profiles={},
        )
    return SupportMatrix(
        analysis_profiles=analysis_profiles(request.app.state.settings),
        text_analysis_release="local-public-opt-in-preview; live semantic gates pending"
        if TEXT_PROFILE in analysis_profiles(request.app.state.settings)
        else "not_available",
        private_preflight_enabled=request.app.state.settings.github_private_fetch_enabled,
        publishing_enabled=request.app.state.settings.github_publishing_enabled,
    )


@router.post("/from-snapshot/{snapshot_id}", response_model=ProjectSelection, status_code=201)
def inspect_snapshot(snapshot_id: str, request: Request):
    from app.auth.snapshots import access_error

    require_local_origin(request)
    try:
        with ProcessLock(execution_lock_path(request.app.state.settings)):
            return project_store(request).from_snapshot(
                request.state.principal.owner_id, snapshot_id
            )
    except GraftError as error:
        raise access_error(error) from None
    except OSError:
        raise HTTPException(409, "Snapshot storage is unavailable.") from None


@router.post("", response_model=ProjectSelection, status_code=201)
def pin_project(body: ProjectRequest, request: Request):
    require_local_origin(request)
    require_release(request.app.state.settings)
    if body.analysis_profile not in analysis_profiles(request.app.state.settings):
        raise HTTPException(409, "Text analysis preview is available for local public URLs only.")
    try:
        with ProcessLock(execution_lock_path(request.app.state.settings)):
            return project_store(request).create(request.state.principal.owner_id, body)
    except GraftError as error:
        raise HTTPException(409, str(error)) from None
    except OSError:
        raise HTTPException(409, "Public project storage is unavailable.") from None


@router.get("/{project_id}", response_model=ProjectSelection)
def get_project(project_id: str, request: Request):
    return project_store(request).get(request.state.principal.owner_id, project_id)
