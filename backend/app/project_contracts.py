"""Public pinned project selection and deterministic structural analysis contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.contracts import Ref, RepoSnapshot, validate_repo_path
from app.evidence_contracts import Coverage
from app.project_support import ProjectSupport


class ProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    url: str = Field(
        max_length=200, pattern=r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"
    )
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
    role: Literal["source", "target"]
    project_root: str = Field(default="", max_length=300)

    @field_validator("project_root")
    @classmethod
    def valid_root(cls, value):
        if value:
            validate_repo_path(value)
        return value


class ProjectSelection(BaseModel):
    id: str
    requested: ProjectRequest
    snapshot: RepoSnapshot
    support: ProjectSupport
    inventory_sha256: str
    root_choices: list[str]
    created_at: str
    repository_snapshot: str | None = None
    private: bool = False


class ProjectAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    kind: Literal["project_analysis"]
    source_project: str = Field(pattern=r"^[a-f0-9]{32}$")
    target_project: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    goal: str = Field(default="", max_length=2000)
    run_baseline: bool = False


class SymbolEvidence(BaseModel):
    name: str
    kind: str
    ref: Ref
    repository_path: str


class ProjectAnalysis(BaseModel):
    schema_version: Literal["python-structure-v1"] = "python-structure-v1"
    project: ProjectSelection
    entry_points: list[SymbolEvidence]
    source_tests: list[SymbolEvidence]
    imports: dict[str, list[str]]
    coverage: Coverage
    limits_reached: list[str]
    qualification: str = (
        "Static definitions/imports are evidence of structure, not a complete feature catalog, "
        "runtime call graph, relevance assessment or verified adaptation."
    )
