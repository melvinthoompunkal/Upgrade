"""Immutable Stage 4 proposals and controller results.

Model-facing schemas are TargetProfileProposal and AssessmentBatch. Repository
read validation, capture verification, and catalog membership belong to the
controller; schema validation alone cannot establish the truth of a code claim.
"""

from typing import Annotated, Literal, get_args

from pydantic import Field, model_validator

from app.baseline_contracts import ArtifactDigest
from app.behavior_contracts import RetryBehavior
from app.contracts import CommitSHA, Contract, FrozenList, NonEmptyString, Ref, RepoPath
from app.evidence_contracts import Coverage, Dependency

FeatureID = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Explanation = Annotated[str, Field(min_length=1, max_length=2000)]


class TargetStack(Contract):
    language: NonEmptyString
    python_requirement: NonEmptyString
    frameworks: FrozenList[NonEmptyString]
    dependencies: FrozenList[Dependency]
    refs: FrozenList[Ref] = Field(min_length=1)


class TargetModule(Contract):
    path: RepoPath
    role: Explanation
    refs: FrozenList[Ref] = Field(min_length=1)

    @model_validator(mode="after")
    def module_evidence(self) -> "TargetModule":
        if not any(ref.path == self.path for ref in self.refs):
            raise ValueError("Module role requires evidence from its own path")
        return self


class TargetArchitecture(Contract):
    entry_points: FrozenList[Ref] = Field(min_length=1)
    modules: FrozenList[TargetModule] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_modules(self) -> "TargetArchitecture":
        if len({module.path for module in self.modules}) != len(self.modules):
            raise ValueError("Architecture module paths must be unique")
        return self


class TargetCapability(Contract):
    id: FeatureID
    behavior: Explanation
    refs: FrozenList[Ref] = Field(min_length=1)


class TargetGap(Contract):
    id: FeatureID
    description: Explanation
    basis: Literal["goal_inference", "observed_behavior"]
    refs: FrozenList[Ref] = Field(min_length=1)


class TargetStyle(Contract):
    naming: Explanation
    error_handling: Explanation
    configuration: Explanation
    refs: FrozenList[Ref] = Field(min_length=1)


class TargetProfileProposal(Contract):
    """Model claims; no controller SHA, capture IDs, coverage, or test status."""

    stack: TargetStack
    architecture: TargetArchitecture
    capabilities: FrozenList[TargetCapability]
    gaps: FrozenList[TargetGap]
    style: TargetStyle
    integration_points: FrozenList[Ref] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_claim_ids(self) -> "TargetProfileProposal":
        claims = (*self.capabilities, *self.gaps)
        if len({claim.id for claim in claims}) != len(claims):
            raise ValueError("Capability and gap IDs must be unique")
        return self

    def references(self) -> tuple[Ref, ...]:
        """Enumerate citations for the controller's read-window validation."""
        return (
            *self.stack.refs,
            *(ref for dependency in self.stack.dependencies for ref in dependency.refs),
            *self.architecture.entry_points,
            *(ref for module in self.architecture.modules for ref in module.refs),
            *(ref for claim in (*self.capabilities, *self.gaps) for ref in claim.refs),
            *self.style.refs,
            *self.integration_points,
        )


class ProfileTests(Contract):
    runner_id: NonEmptyString
    test_paths: FrozenList[RepoPath] = Field(min_length=1)
    baseline_capture_id: RepoPath
    baseline_status: Literal["passed", "failed"]

    @model_validator(mode="after")
    def unique_test_paths(self) -> "ProfileTests":
        if len(set(self.test_paths)) != len(self.test_paths):
            raise ValueError("Test paths must be unique")
        return self


class ObservedCase(Contract):
    name: NonEmptyString
    outcome: Literal["passed", "failed"]


class ObservedGapEvidence(Contract):
    gap_id: FeatureID
    capture_id: RepoPath
    cases: FrozenList[ObservedCase] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cases(self) -> "ObservedGapEvidence":
        if len({case.name for case in self.cases}) != len(self.cases):
            raise ValueError("Observed case identities must be unique")
        return self


class ProfileProvenance(Contract):
    run_id: NonEmptyString
    target_repo_id: NonEmptyString
    baseline_binding: ArtifactDigest
    evidence_reads: ArtifactDigest
    observed_gaps: FrozenList[ObservedGapEvidence]


class TargetProfile(TargetProfileProposal):
    target_sha: CommitSHA
    tests: ProfileTests
    coverage: Coverage
    provenance: ProfileProvenance

    @model_validator(mode="after")
    def matching_provenance(self) -> "TargetProfile":
        if any(
            ref.repo_id != self.provenance.target_repo_id or ref.commit_sha != self.target_sha
            for ref in self.references()
        ):
            raise ValueError("Profile references must belong to the exact target snapshot")
        observed_ids = {gap.id for gap in self.gaps if gap.basis == "observed_behavior"}
        evidence_ids = [evidence.gap_id for evidence in self.provenance.observed_gaps]
        if len(set(evidence_ids)) != len(evidence_ids) or set(evidence_ids) != observed_ids:
            raise ValueError("Observed gaps require exactly one controller capture/case binding")
        return self


class Criterion(Contract):
    rating: int = Field(ge=0, le=5)
    reason: Explanation
    evidence: FrozenList[Ref]

    @model_validator(mode="after")
    def supported_rating(self) -> "Criterion":
        if self.rating > 0 and not self.evidence:
            raise ValueError("A positive rating requires evidence")
        return self


class FitCriteria(Contract):
    gap_fit: Criterion
    isolation: Criterion
    dependency_cost: Criterion
    stack_compatibility: Criterion
    testability: Criterion

    @property
    def weighted_total(self) -> int:
        return (
            7 * self.gap_fit.rating
            + 5 * self.isolation.rating
            + 3 * self.dependency_cost.rating
            + 3 * self.stack_compatibility.rating
            + 2 * self.testability.rating
        )

    @property
    def meets_thresholds(self) -> bool:
        return self.weighted_total >= 75 and all(
            criterion.rating >= 3
            for criterion in (
                self.gap_fit,
                self.isolation,
                self.stack_compatibility,
                self.testability,
            )
        )


class AdaptationPlan(Contract):
    rationale: Explanation
    behavior_contract: RetryBehavior | None
    target_gap_ids: FrozenList[FeatureID]
    integration_points: FrozenList[Ref]
    production_paths: FrozenList[RepoPath]
    new_production_paths: FrozenList[RepoPath]
    estimated_changed_lines: int = Field(ge=0)
    existing_dependencies: FrozenList[Dependency]
    runtime_dependency_additions: FrozenList[NonEmptyString]
    requires_new_service: bool

    @model_validator(mode="after")
    def consistent_scope(self) -> "AdaptationPlan":
        for values in (self.target_gap_ids, self.production_paths, self.new_production_paths):
            if len(set(values)) != len(values):
                raise ValueError("Adaptation IDs and paths must be unique")
        if not set(self.new_production_paths).issubset(self.production_paths):
            raise ValueError("New production paths must be included in the proposed scope")
        return self


class CandidateAssessment(Contract):
    feature_id: FeatureID
    criteria: FitCriteria
    adaptation: AdaptationPlan


class AssessmentBatch(Contract):
    assessments: FrozenList[CandidateAssessment] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique_candidates(self) -> "AssessmentBatch":
        if len({item.feature_id for item in self.assessments}) != len(self.assessments):
            raise ValueError("Assessment feature IDs must be unique")
        return self


GateName = Literal[
    "source_provenance",
    "target_provenance",
    "supported_execution",
    "passing_baseline",
    "target_gap",
    "isolated_adaptation",
    "existing_dependencies",
    "source_use_policy",
    "acceptance_readiness",
    "sufficient_coverage",
]
GATE_NAMES: tuple[GateName, ...] = get_args(GateName)


class GateEvidence(Contract):
    """Controller evaluation; unknown evidence never passes a selection gate."""

    name: GateName
    status: Literal["pass", "fail", "unknown"]
    reason: Explanation
    evidence: FrozenList[Ref]


class GateResult(Contract):
    name: GateName
    passed: bool
    reason: Explanation

    @classmethod
    def from_evidence(cls, evidence: GateEvidence) -> "GateResult":
        return cls(name=evidence.name, passed=evidence.status == "pass", reason=evidence.reason)


class CandidateScore(Contract):
    feature_id: FeatureID
    criteria: FitCriteria
    gates: FrozenList[GateResult] = Field(min_length=len(GATE_NAMES), max_length=len(GATE_NAMES))
    eligible: bool
    total: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def consistent_score(self) -> "CandidateScore":
        if {gate.name for gate in self.gates} != set(GATE_NAMES):
            raise ValueError("Every selection gate must appear exactly once")
        if self.total != self.criteria.weighted_total:
            raise ValueError("Total must match the exact fit-v1 weighted ratings")
        eligible = self.criteria.meets_thresholds and all(gate.passed for gate in self.gates)
        if self.eligible != eligible:
            raise ValueError("Eligibility must agree with every gate and rating threshold")
        return self

    def ranking_key(self) -> tuple[int, int, int, int, str]:
        return (
            -self.total,
            -self.criteria.isolation.rating,
            -self.criteria.testability.rating,
            -self.criteria.dependency_cost.rating,
            self.feature_id,
        )


class RejectedCandidate(Contract):
    feature_id: FeatureID
    reasons: FrozenList[Explanation] = Field(min_length=1)


class FitResult(Contract):
    rubric_version: Literal["fit-v1"]
    candidates: FrozenList[CandidateScore] = Field(min_length=1, max_length=8)
    top3: FrozenList[FeatureID] = Field(max_length=3)
    selected_feature_id: FeatureID | None
    rejected: FrozenList[RejectedCandidate]
    selection_reason: Explanation

    @model_validator(mode="after")
    def consistent_selection(self) -> "FitResult":
        if len({item.feature_id for item in self.candidates}) != len(self.candidates):
            raise ValueError("Scored feature IDs must be unique")
        eligible = sorted(
            (item for item in self.candidates if item.eligible), key=CandidateScore.ranking_key
        )
        expected_top = tuple(item.feature_id for item in eligible[:3])
        if self.top3 != expected_top:
            raise ValueError("top3 must contain only the first eligible candidates in policy order")
        if self.selected_feature_id != (expected_top[0] if expected_top else None):
            raise ValueError("Selection must be the first eligible candidate or null")
        rejected_ids = [item.feature_id for item in self.rejected]
        if len(set(rejected_ids)) != len(rejected_ids) or set(rejected_ids) != {
            item.feature_id for item in self.candidates if not item.eligible
        }:
            raise ValueError("Rejections must include every ineligible candidate exactly once")
        return self
