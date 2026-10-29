"""Versioned support levels computed from pinned data; authorization is a separate boundary."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.project_adapters import ADAPTER_VERSION, execution_reasons, manifest_data, python_facts
from app.tools.repositories import PinnedRepository


class PythonMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    name: str | None = None
    requires_python: str | None = Field(default=None, alias="requires-python")
    dependencies: list[str] = Field(default_factory=list)
    dynamic: list[str] = Field(default_factory=list)


class ProjectSupport(BaseModel):
    profile_version: Literal["python-evidence-v1"] = "python-evidence-v1"
    repo_id: str
    commit_sha: str
    project_root: str
    capability: Literal["browse_index", "evidence_analysis", "verified_adaptation"] = "browse_index"
    python_files: int
    manifest_sha256: str | None
    execution_supported: bool = False
    adaptation_supported: Literal[False] = False
    reasons: list[str] = Field(default_factory=list)
    name: str | None = None
    python_requirement: str | None = None
    runtime_dependencies: list[str] = Field(default_factory=list)
    dynamic: list[str] = Field(default_factory=list)
    adapter_version: str | None = None
    analysis_reasons: list[str] = Field(default_factory=list)
    execution_reasons: list[str] = Field(default_factory=list)
    acceptance_capabilities: list[str] = Field(default_factory=list)
    notice_policy: Literal["analysis-only-v1"] = "analysis-only-v1"
    notice_paths: list[str] = Field(default_factory=list)
    adaptation_reason: str = "Feature-specific acceptance and allowed-use review are required."


def inspect_project(repo: PinnedRepository) -> ProjectSupport:
    files = {entry.path: entry for entry in repo.inventory.files}
    result = ProjectSupport(
        repo_id=repo.snapshot.repo_id,
        commit_sha=repo.snapshot.commit_sha,
        project_root=repo.prefix,
        python_files=sum(path.endswith(".py") for path in files),
        manifest_sha256=files["pyproject.toml"].sha256 if "pyproject.toml" in files else None,
        notice_paths=[ref.path for ref in repo.snapshot.license_refs],
    )
    if not result.python_files:
        result.analysis_reasons.append("No inventoried Python source files.")
    if "pyproject.toml" not in files:
        result.reasons.append("No Python project manifest at the selected root.")
        result.analysis_reasons = list(result.reasons)
        return result
    try:
        manifest = manifest_data(repo)
        project = PythonMetadata.model_validate(manifest["project"])
    except (ValueError, ValidationError):
        result.reasons.append("The project manifest cannot be statically validated.")
        result.analysis_reasons = list(result.reasons)
        return result
    result.name, result.python_requirement = project.name, project.requires_python
    result.runtime_dependencies, result.dynamic = project.dependencies, project.dynamic
    indexes, errors = python_facts(repo)
    result.analysis_reasons.extend(errors)
    if repo.inventory.limits_reached:
        result.analysis_reasons.append("Inventory limits were reached; select a smaller root.")
    if not project.name:
        result.analysis_reasons.append("A static project name is required.")
    result.execution_reasons = execution_reasons(repo, manifest, indexes)
    result.execution_supported = not (result.analysis_reasons or result.execution_reasons)
    if not result.analysis_reasons:
        result.capability = "evidence_analysis"
    if result.execution_supported:
        result.adapter_version = (
            "python-unittest-v1"
            if any(s.name == "load_tests" for index in indexes.values() for s in index.symbols)
            else ADAPTER_VERSION
        )
    result.reasons = [*result.analysis_reasons, *result.execution_reasons]
    if not result.notice_paths:
        result.adaptation_reason = (
            "No project-root license/notice was inventoried; reuse is unapproved."
        )
    return result
