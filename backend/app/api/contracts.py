"""Typed transport contracts; domain results retain their canonical validators."""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.catalog_contracts import CatalogJob
from app.contracts import ModelCall, Ref, RepoSnapshot
from app.evidence_contracts import Coverage, FeatureCard
from app.project_contracts import ProjectAnalysis, ProjectAnalysisRequest
from app.relevance_contracts import RelevanceJob
from app.selection_contracts import AssessmentBatch, FitResult, TargetProfile

RunKind = Literal[
    "fit_select",
    "source_trace",
    "pinned_baseline",
    "local_baseline",
    "meta_smoke",
    "project_analysis",
    "catalog_discovery",
    "relevance_assessment",
]


class SourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_url: str = Field(max_length=200)
    source_sha: str
    target: Literal["crawler-demo"] = "crawler-demo"
    goal: str = Field(min_length=1, max_length=2000)

    @field_validator("source_url")
    @classmethod
    def public_github(cls, value: str) -> str:
        if not re.fullmatch(
            r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", value
        ):
            raise ValueError("Use a public https://github.com/owner/repository URL.")
        return value

    @field_validator("source_sha")
    @classmethod
    def full_sha(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", value):
            raise ValueError("Enter the full lowercase commit SHA for this source repository.")
        return value


class SelectionRequest(SourceRequest):
    kind: Literal["fit_select"]


class TraceRequest(SourceRequest):
    kind: Literal["source_trace"]


class CheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["pinned_baseline", "local_baseline", "meta_smoke"]


RunRequest = Annotated[
    SelectionRequest
    | TraceRequest
    | CheckRequest
    | ProjectAnalysisRequest
    | CatalogJob
    | RelevanceJob,
    Field(discriminator="kind"),
]


class Problem(BaseModel):
    code: str
    message: str


class RunSummary(BaseModel):
    id: str
    kind: RunKind
    title: str
    status: Literal["queued", "running", "finished"]
    stage: str
    outcome: str | None = None
    created_at: str
    updated_at: str
    revision: int = 1
    historical: bool = False
    analyzed_feature_count: int | None = None
    eligible_feature_count: int | None = None
    error: Problem | None = None
    cancel_requested: bool = False


class RunPage(BaseModel):
    items: list[RunSummary]
    next_cursor: int | None


class Event(BaseModel):
    sequence: int
    run_id: str
    timestamp: str
    stage: str
    message: str


class EventPage(BaseModel):
    items: list[Event]
    next_cursor: int


class Artifact(BaseModel):
    id: str
    label: str
    kind: str
    size: int
    sha256: str
    available: bool


class Reference(BaseModel):
    id: str
    ref: Ref
    category: str
    excerpt_available: bool = False


class ReferenceDetail(Reference):
    text: str | None = None
    sha256: str | None = None


class TestCase(BaseModel):
    name: str
    outcome: str
    message: str = ""
    detail: str = ""


class TestSuite(BaseModel):
    suite: str
    status: str
    elapsed_ms: int
    image_id: str
    cases: list[TestCase]
    diagnostic: str = ""
    expected_diagnostic: bool = False


class RunDetail(RunSummary):
    relevance_assessment_id: str | None = None
    catalog_id: str | None = None
    catalog_revision: int | None = None
    project_analyses: list[ProjectAnalysis] = Field(default_factory=list)
    schema_version: str = "1"
    goal: str = ""
    source: RepoSnapshot | None = None
    target: RepoSnapshot | None = None
    target_path_prefix: str | None = None
    original_target_unchanged: bool | None = None
    retry_diagnostic_status: str | None = None
    features: list[FeatureCard] = Field(default_factory=list)
    fit: FitResult | None = None
    profile: TargetProfile | None = None
    assessment: AssessmentBatch | None = None
    coverage: Coverage | None = None
    tests: list[TestSuite] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)
    calls: list[ModelCall] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    limits: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class Capabilities(BaseModel):
    schema_version: str = "1"
    kinds: list[RunKind]
    patch_supported: bool = False
    model_configured: bool
    model_id: str
    checked_at: str
    image_receipt_present: bool
    worker_available: bool
    diagnostics_allowed: bool = True
    scope: str = (
        "Public Python project structure; optional native baseline. Crawler fit is an example."
    )


class Target(BaseModel):
    id: str = "crawler-demo"
    name: str = "Crawler demo"
    stack: str = "Python 3.12 / HTTPX"
    input_semantics: str = "Committed HEAD; local changes are excluded"
    head: str | None = None
    dirty: bool | None = None
    checked_at: str


class ArtifactText(BaseModel):
    text: str
    truncated: bool = False
