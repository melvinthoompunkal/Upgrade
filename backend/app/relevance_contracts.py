"""Saved target/goal contexts and bounded judgments; relevance is separate from fit."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.catalog_contracts import EvidenceExcerpt, Id
from app.contracts import Contract, FrozenList, Ref
from app.relevance_types import AssessmentSummary, TargetStatus
from app.relevance_types import FitStatus as FitStatus
from app.relevance_types import RelevanceStatus as RelevanceStatus
from app.selection_contracts import CandidateScore, FitCriteria, GateEvidence
from app.text_contracts import CoverageV2, TextRefV2

BrowseMode = Literal["all", "relevant"]
FeatureSort = Literal["category", "name", "relevance"]
ExpansionNote = Annotated[str, Field(min_length=1, max_length=600)]


class AssessmentOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    feature_limit: int = Field(default=8, ge=1, le=8)
    max_requests: int = Field(default=32, ge=1, le=32)
    max_input_tokens: int = Field(default=400_000, ge=1, le=400_000)
    max_output_tokens: int = Field(default=40_000, ge=1, le=40_000)
    max_seconds: int = Field(default=1200, ge=1, le=1200)


class AssessmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    catalog_id: Id
    catalog_revision: int = Field(ge=1)
    target_project: Id | None = None
    goal: str = Field(min_length=1, max_length=2000)
    base_assessment: Id | None = None
    feature_ids: list[Id] = Field(default_factory=list, max_length=8)
    options: AssessmentOptions = Field(default_factory=AssessmentOptions)

    @field_validator("goal")
    @classmethod
    def meaningful_goal(cls, value):
        if not value.strip():
            raise ValueError("Describe a goal before requesting relevance analysis.")
        return value  # Exact text is part of the assessment identity.


class RelevanceJob(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["relevance_assessment"] = "relevance_assessment"
    assessment_id: Id


class RelevanceJudgment(Contract):
    feature_id: Id
    relevance: int = Field(ge=0, le=5)
    reason: str = Field(min_length=1, max_length=600)
    source_refs: FrozenList[Ref] = Field(min_length=1, max_length=12)
    target_refs: FrozenList[Ref] = Field(max_length=12)
    target_status: TargetStatus
    criteria: FitCriteria | None


class IntegrationPoint(Contract):
    target_ref: Ref
    change: str = Field(min_length=1, max_length=600)


class ExpansionRecommendation(Contract):
    source_capability: str = Field(min_length=1, max_length=600)
    existing_target_behavior: str = Field(min_length=1, max_length=1000)
    proposed_extension: str = Field(min_length=1, max_length=1200)
    benefit: str = Field(min_length=1, max_length=800)
    integration_points: FrozenList[IntegrationPoint] = Field(min_length=1, max_length=4)
    constraints: FrozenList[ExpansionNote] = Field(max_length=8)
    uncertainties: FrozenList[ExpansionNote] = Field(min_length=1, max_length=8)
    source_refs: FrozenList[Ref] = Field(min_length=1, max_length=8)
    target_refs: FrozenList[Ref] = Field(min_length=1, max_length=8)


class ExpansionJudgment(RelevanceJudgment):
    """New model output; the original judgment remains readable in saved v1 receipts."""

    expansion: ExpansionRecommendation | None


class IntegrationPointV2(Contract):
    target_ref: TextRefV2 | Ref
    change: str = Field(min_length=1, max_length=600)


class ExpansionRecommendationV2(Contract):
    source_capability: str = Field(min_length=1, max_length=600)
    existing_target_behavior: str = Field(min_length=1, max_length=1000)
    proposed_extension: str = Field(min_length=1, max_length=1200)
    benefit: str = Field(min_length=1, max_length=800)
    integration_points: FrozenList[IntegrationPointV2] = Field(min_length=1, max_length=4)
    constraints: FrozenList[ExpansionNote] = Field(max_length=8)
    uncertainties: FrozenList[ExpansionNote] = Field(min_length=1, max_length=8)
    source_refs: FrozenList[TextRefV2 | Ref] = Field(min_length=1, max_length=8)
    target_refs: FrozenList[TextRefV2 | Ref] = Field(min_length=1, max_length=8)
    transfer_mode: Literal["conceptual_cross_language", "same_language_idea"]


class SemanticJudgmentV2(Contract):
    schema_version: Literal["semantic-judgment-v2"]
    feature_id: Id
    relevance: int = Field(ge=0, le=5)
    reason: str = Field(min_length=1, max_length=600)
    source_refs: FrozenList[TextRefV2 | Ref] = Field(min_length=1, max_length=12)
    target_refs: FrozenList[TextRefV2 | Ref] = Field(max_length=12)
    target_status: TargetStatus
    expansion: ExpansionRecommendationV2 | None


class ComparisonIntegrationPoint[TargetRef: Ref](IntegrationPointV2):
    target_ref: TargetRef


class ComparisonExpansion[SourceRef: Ref, TargetRef: Ref](ExpansionRecommendationV2):
    integration_points: FrozenList[ComparisonIntegrationPoint[TargetRef]] = Field(
        min_length=1, max_length=4
    )
    source_refs: FrozenList[SourceRef] = Field(min_length=1, max_length=8)
    target_refs: FrozenList[TargetRef] = Field(min_length=1, max_length=8)


class ComparisonJudgment[SourceRef: Ref, TargetRef: Ref](SemanticJudgmentV2):
    """Model output admits only the citation contract actually available for each role."""

    @classmethod
    def model_parametrized_name(cls, params: tuple[type, ...]) -> str:
        return "ComparisonJudgment_" + "_".join(ref.__name__ for ref in params)

    source_refs: FrozenList[SourceRef] = Field(min_length=1, max_length=12)
    target_refs: FrozenList[TargetRef] = Field(max_length=12)
    expansion: ComparisonExpansion[SourceRef, TargetRef] | None


class AssessmentCounts(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    discovered: int = 0
    deeply_assessed: int = 0
    relevant: int = 0
    eligible: int = 0
    assessment_failed: int = 0
    unassessed: int = 0


class FeatureAssessment(AssessmentSummary):
    schema_version: Literal["feature-assessment-v1", "feature-assessment-v2"] = (
        "feature-assessment-v1"
    )
    adaptation_reason: str | None = None
    feature_id: Id
    fit: CandidateScore | None = None
    gates: list[GateEvidence] = Field(default_factory=list)
    rejections: list[str] = Field(default_factory=list)
    judgment: SemanticJudgmentV2 | RelevanceJudgment | None = None
    expansion: ExpansionRecommendationV2 | ExpansionRecommendation | None = None
    failure_code: str | None = None
    evidence: list[EvidenceExcerpt] = Field(default_factory=list, max_length=24)
    evidence_limit_reached: bool = False


class ExpansionSummary(BaseModel):
    feature_id: Id
    name: str
    benefit: str


class AssessmentView(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    id: Id
    catalog_id: Id
    catalog_revision: int
    run_id: str
    target_project: str | None
    target_sha: str | None
    target_root: str | None
    target_origin: str | None
    goal: str
    identity: dict
    schema_version: Literal["assessment-v1", "assessment-v2"] = "assessment-v1"
    coverage_v2: dict[str, CoverageV2] = Field(default_factory=dict)
    options: AssessmentOptions
    shortlist: list[str]
    base_assessment: str | None = None
    state: Literal["queued", "running", "complete", "partial", "failed", "canceled"] = "queued"
    created_at: str
    counts: AssessmentCounts = Field(default_factory=AssessmentCounts)
    top3: list[str] = Field(default_factory=list)
    expansion_top3: list[ExpansionSummary] = Field(default_factory=list)
    limit_reasons: list[str] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    cumulative_usage: dict[str, int] = Field(default_factory=dict)
    returned_model_ids: list[str] = Field(default_factory=list)
    failure_code: str | None = None
    qualification: str = (
        "relevance-v2: ratings of 3–5 are relevant, 0–2 less relevant. "
        "Judgments are not probabilities or a claim of global best fit. "
        "Only the disclosed shortlist is assessed. Expansion ideas are ranked by relevance "
        "then feature ID and remain unverified suggestions. General projects have no frozen "
        "acceptance/baseline binding or approved adaptation plan; these fit gates cannot pass."
    )
