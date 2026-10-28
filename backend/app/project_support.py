"""Static Python support inspection. A readable project is not an executable adapter."""

import tomllib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.tools.repositories import PinnedRepository


class PythonMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    name: str | None = None
    requires_python: str | None = Field(default=None, alias="requires-python")
    dependencies: list[str] = Field(default_factory=list)
    dynamic: list[str] = Field(default_factory=list)


class ProjectSupport(BaseModel):
    profile_version: Literal["python-static-v1"] = "python-static-v1"
    repo_id: str
    commit_sha: str
    project_root: str
    capability: Literal["browse_index"] = "browse_index"
    python_files: int
    manifest_sha256: str | None
    execution_supported: Literal[False] = False
    adaptation_supported: Literal[False] = False
    reasons: list[str] = Field(default_factory=list)
    name: str | None = None
    python_requirement: str | None = None
    runtime_dependencies: list[str] = Field(default_factory=list)
    dynamic: list[str] = Field(default_factory=list)


def inspect_project(repo: PinnedRepository) -> ProjectSupport:
    files = {entry.path: entry for entry in repo.inventory.files}
    result = ProjectSupport(
        repo_id=repo.snapshot.repo_id,
        commit_sha=repo.snapshot.commit_sha,
        project_root=repo.prefix,
        python_files=sum(path.endswith(".py") for path in files),
        manifest_sha256=files["pyproject.toml"].sha256 if "pyproject.toml" in files else None,
    )
    if not result.python_files:
        result.reasons.append("No inventoried Python source files.")
    if "pyproject.toml" not in files:
        result.reasons.append("No Python project manifest at the selected root.")
        return result
    try:
        project = PythonMetadata.model_validate(
            tomllib.loads(repo.read("pyproject.toml")).get("project", {})
        )
    except (tomllib.TOMLDecodeError, ValidationError):
        result.reasons.append("The project manifest cannot be statically validated.")
        return result
    result.name, result.python_requirement = project.name, project.requires_python
    result.runtime_dependencies, result.dynamic = project.dependencies, project.dynamic
    result.reasons.append(
        "General feature discovery and a reviewed offline test profile are required."
    )
    return result
