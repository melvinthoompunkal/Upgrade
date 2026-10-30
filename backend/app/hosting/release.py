"""Fail-closed operator evidence gate. Integrity does not certify the evidence's meaning."""

import hashlib
import json
import time
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.errors import GraftError
from app.tools.runner_artifacts import read_regular_file

PROFILES = ("project_analysis", "catalog_discovery", "relevance_assessment")
GATES = (
    "github_lifecycle",
    "tls_origin_proxy",
    "owner_isolation",
    "quotas_load_fairness",
    "provider_cancellation",
    "hard_stop_recovery",
    "retention_backup_restore",
    "key_rotation",
    "migration_rollback",
    "monitoring",
    "independent_review",
)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1, max_length=300)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    outcome: Literal["passed"]


class HostedRelease(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    schema_version: Literal["hosted-release-v1"]
    deployment_id: str
    origin: str
    build_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created: float = Field(allow_inf_nan=False)
    expires: float = Field(allow_inf_nan=False)
    profiles: list[Literal["project_analysis", "catalog_discovery", "relevance_assessment"]]
    approved_by: str = Field(min_length=1, max_length=100)
    evidence: dict[str, Evidence]


def build_digest(root: Path) -> str:
    """Bind executable application, built browser, locked dependencies and deployment units."""
    files = [root / "backend/pyproject.toml", root / "backend/uv.lock"]
    for directory in ("backend/app", "frontend/dist", "deploy/hosted"):
        for path in (root / directory).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                files.append(path)
    if not (root / "frontend/dist/index.html").is_file():
        raise ValueError("Build the frontend before preparing a hosted release.")
    hashes = {
        path.relative_to(root).as_posix(): hashlib.sha256(
            read_regular_file(root, path.relative_to(root).as_posix())
        ).hexdigest()
        for path in sorted(files)
    }
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


def released_profiles(settings: Settings) -> tuple[str, ...]:
    """Read only operator-owned, read-only release/evidence files; never accept browser approval."""
    from app.tools.runner import PROJECT_ROOT

    path = settings.hosted_release_path
    if settings.deployment_mode != "hosted" or path is None:
        return ()
    try:
        raw = read_regular_file(path.parent, path.name)
        release = HostedRelease.model_validate_json(raw)
        moment = time.time()
        if (
            release.deployment_id != settings.deployment_id
            or release.origin != settings.app_base_url
            or not release.created <= moment < release.expires
            or release.expires - release.created > 30 * 86400
            or not release.profiles
            or len(release.profiles) != len(set(release.profiles))
            or set(release.evidence) != set(GATES)
            or release.build_sha256 != build_digest(PROJECT_ROOT)
        ):
            return ()
        for evidence in release.evidence.values():
            content = read_regular_file(path.parent, evidence.path)
            if not content or hashlib.sha256(content).hexdigest() != evidence.sha256:
                return ()
        if read_regular_file(path.parent, path.name) != raw:
            return ()
        return tuple(release.profiles)
    except (GraftError, OSError, ValueError, TypeError):
        return ()


def require_release(settings: Settings) -> None:
    if settings.deployment_mode == "hosted" and not released_profiles(settings):
        raise HTTPException(503, "Hosted admission is paused pending current deployment evidence.")


def require_job(settings: Settings, request) -> None:
    if settings.deployment_mode != "hosted":
        return
    if request.kind not in PROFILES or getattr(request, "run_baseline", False):
        raise HTTPException(
            409, "Target execution and this workflow are unavailable on this deployment."
        )
    if request.kind not in released_profiles(settings):
        raise HTTPException(503, "Hosted admission is paused pending current deployment evidence.")
