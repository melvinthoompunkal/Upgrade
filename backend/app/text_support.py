"""Text availability and structural/execution support are independent decisions."""

from collections import Counter
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field

from app.text_contracts import TEXT_PROFILE


class ProjectSupportV2(BaseModel):
    profile_version: Literal["repository-text-v1"] = TEXT_PROFILE
    schema_version: Literal["project-support-v2"] = "project-support-v2"
    repo_id: str
    commit_sha: str
    project_root: str
    capability: Literal["browse_index", "evidence_analysis"]
    analysis_available: bool
    detected_languages: dict[str, int]
    readable_source_files: int
    readable_files: int
    python_files: int
    manifest_sha256: str | None
    manifest_paths: list[str]
    structural_coverage: str = "text_only; optional parsers disabled"
    execution_supported: Literal[False] = False
    adaptation_supported: Literal[False] = False
    name: str
    reasons: list[str] = Field(default_factory=list)
    analysis_reasons: list[str] = Field(default_factory=list)
    execution_reasons: list[str] = ["Text analysis has no admitted execution profile."]
    adaptation_reason: str = (
        "Generic text cards require an independently reviewed adaptation profile; "
        "a Python file does not admit this repository to patch execution."
    )
    adapter_version: None = None
    notice_policy: Literal["analysis-only-v1"] = "analysis-only-v1"
    notice_paths: list[str]
    inventory_partial: bool
    inventory_limits: list[str]


def inspect_text_project(repo):
    files = repo.inventory.files
    sources = [f for f in files if f.role == "source"]
    manifests = [f for f in files if f.role == "manifest"]
    reasons = [] if any(f.bytes for f in files) else ["No admitted nonempty readable text."]
    name = repo.snapshot.origin.rstrip("/").split("/")[-1]
    if repo.prefix:
        name += " / " + PurePosixPath(repo.prefix).name
    return ProjectSupportV2(
        repo_id=repo.snapshot.repo_id,
        commit_sha=repo.snapshot.commit_sha,
        project_root=repo.prefix,
        capability="browse_index" if reasons else "evidence_analysis",
        analysis_available=not reasons,
        detected_languages=dict(Counter(f.language for f in sources)),
        readable_source_files=len(sources),
        readable_files=len(files),
        python_files=sum(f.language == "Python" for f in files),
        manifest_sha256=next(
            (
                f.sha256
                for f in manifests
                if f.path == (f"{repo.prefix}/pyproject.toml" if repo.prefix else "pyproject.toml")
            ),
            None,
        ),
        manifest_paths=[f.path for f in manifests],
        name=name,
        reasons=reasons,
        analysis_reasons=reasons,
        notice_paths=[r.path for r in repo.snapshot.license_refs],
        inventory_partial=bool(repo.inventory.limits_reached),
        inventory_limits=list(repo.inventory.limits_reached),
    )


def analysis_profiles(settings):
    profiles = ["python-evidence-v1"]
    if settings.deployment_mode == "local" and settings.auth_mode == "local":
        profiles.append(TEXT_PROFILE)
    return profiles


def require_analysis_profile(selected, settings):
    from app.errors import UnsupportedSourceError

    if selected.support.profile_version == TEXT_PROFILE and (
        TEXT_PROFILE not in analysis_profiles(settings) or selected.private
    ):
        raise UnsupportedSourceError("Text analysis preview requires a local public URL pin.")
