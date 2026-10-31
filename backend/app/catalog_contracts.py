"""Persistent source discovery contracts, separate from target fit and adaptation."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.contracts import Contract, FrozenList, Ref, validate_repo_path
from app.evidence_contracts import FeatureCard
from app.relevance_types import AssessmentSummary
from app.text_contracts import CoverageV2, FeatureCardV2, TextRefV2

Id = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
EvidenceStatus = Literal["unverified", "supported", "partial", "unsupported"]
ScanState = Literal["queued", "running", "complete_for_scope", "partial", "failed", "canceled"]


class DiscoveryOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    max_files: int = Field(default=32, ge=0, le=256)
    max_candidates: int = Field(default=500, ge=1, le=500)
    trace_limit: int = Field(default=8, ge=0, le=8)
    max_requests: int = Field(default=32, ge=1, le=32)
    max_input_tokens: int = Field(default=400_000, ge=1, le=400_000)
    max_output_tokens: int = Field(default=40_000, ge=1, le=40_000)
    max_seconds: int = Field(default=1200, ge=1, le=1200)


class CatalogCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_project: Id
    scope: str = Field(default="", max_length=300)
    options: DiscoveryOptions = Field(default_factory=DiscoveryOptions)
    use_cache: bool = True

    @field_validator("scope")
    @classmethod
    def normalized_scope(cls, value):
        return validate_repo_path(value) if value else value


class CatalogContinue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    base_revision: int = Field(ge=1)
    options: DiscoveryOptions = Field(default_factory=DiscoveryOptions)
    trace_feature_ids: list[Id] = Field(default_factory=list, max_length=8)


class CatalogJob(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["catalog_discovery"] = "catalog_discovery"
    catalog_id: Id
    revision: int = Field(ge=1)


class CandidateHint(Contract):
    behavior_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=600)
    category: str = Field(min_length=1, max_length=60)
    discovery_refs: FrozenList[Ref] = Field(min_length=1, max_length=12)
    parent_key: str | None


class CandidateBatch(Contract):
    candidates: FrozenList[CandidateHint] = Field(max_length=8)


class FeatureSummary(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    feature_id: Id
    name: str
    summary: str
    category: str
    group: str
    parent_feature_id: str | None = None
    aliases: list[str] = Field(default_factory=list)
    evidence_status: EvidenceStatus = "unverified"
    trace_state: Literal["untraced", "validated", "failed"] = "untraced"
    assessment: AssessmentSummary | None = None


class EvidenceExcerpt(BaseModel):
    ref: TextRefV2 | Ref
    text: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class FeatureDetail(FeatureSummary):
    schema_version: Literal["catalog-feature-v1", "catalog-feature-v2"] = "catalog-feature-v1"
    behavior_key: str
    discovery_refs: list[TextRefV2 | Ref]
    card: FeatureCardV2 | FeatureCard | None = None
    evidence: list[EvidenceExcerpt] = Field(default_factory=list)
    examined_paths: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    failure_code: str | None = None
    qualification: str = (
        "Source tests are cited, not executed. Imports do not prove dependency closure. "
        "Analysis-only: source reuse, relevance, eligibility and adaptation are unassessed."
    )


class CatalogCounts(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    discovered: int = 0
    code_supported: int = 0
    partial: int = 0
    unsupported: int = 0
    unverified: int = 0
    deeply_traced: int = 0


class CatalogView(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    id: Id
    revision: int
    base_revision: int | None = None
    run_id: str
    source_project: str
    source_sha: str
    project_root: str
    scope: str
    identity: dict
    schema_version: Literal["catalog-v1", "catalog-v2"] = "catalog-v1"
    analysis_profile: Literal["python-evidence-v1", "repository-text-v1"] = "python-evidence-v1"
    coverage_v2: CoverageV2 | None = None
    seed_offsets: dict[str, int] = Field(default_factory=dict)
    merge_mappings: dict[str, str] = Field(default_factory=dict)
    state: ScanState = "queued"
    created_at: str
    counts: CatalogCounts = Field(default_factory=CatalogCounts)
    options: DiscoveryOptions
    processed_paths: list[str] = Field(default_factory=list)
    remaining_paths: list[str] = Field(default_factory=list)
    examined_paths: list[str] = Field(default_factory=list)
    excluded_paths: list[str] = Field(default_factory=list)
    unexamined_areas: list[str] = Field(default_factory=list)
    limit_reasons: list[str] = Field(default_factory=list)
    failure_code: str | None = None
    batches: list[dict] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    cumulative_usage: dict[str, int] = Field(default_factory=dict)
    returned_model_ids: list[str] = Field(default_factory=list)
    continuation_available: bool = False
    cache_hit: bool = False
    qualification: str = (
        "All discovered features in this revision and declared scope; bounded automated "
        "discovery does not establish every capability in the repository."
    )


class FeaturePage(BaseModel):
    catalog_id: Id
    catalog_revision: int
    items: list[FeatureSummary]
    counts: CatalogCounts
    matching_filter: int
    next_cursor: str | None
    assessment_id: str | None = None
    mode: Literal["all", "relevant"] = "all"
    assessment_counts: dict[str, int] | None = None
    limit_reasons: list[str] = Field(default_factory=list)
