"""Contract boundaries only: no provider calls or target execution."""

import copy
import json

import pytest
from pydantic import ValidationError

from app.baseline_contracts import BaselineBinding
from app.models.schema import strict_schema
from app.selection_contracts import (
    GATE_NAMES,
    AssessmentBatch,
    CandidateScore,
    FitResult,
    GateEvidence,
    GateResult,
    TargetProfile,
    TargetProfileProposal,
)

TARGET_REF = {
    "repo_id": "crawler-demo",
    "commit_sha": "a" * 40,
    "path": "src/crawler.py",
    "start_line": 1,
    "end_line": 3,
    "symbol": None,
}
SOURCE_REF = {**TARGET_REF, "repo_id": "scrapy", "commit_sha": "b" * 40}
CRITERION_NAMES = ("gap_fit", "isolation", "dependency_cost", "stack_compatibility", "testability")


def profile_proposal():
    return {
        "stack": {
            "language": "Python",
            "python_requirement": ">=3.12,<3.13",
            "frameworks": [],
            "dependencies": [{"name": "httpx", "kind": "runtime", "refs": [TARGET_REF]}],
            "refs": [TARGET_REF],
        },
        "architecture": {
            "entry_points": [TARGET_REF],
            "modules": [{"path": "src/crawler.py", "role": "Fetch pages.", "refs": [TARGET_REF]}],
        },
        "capabilities": [
            {"id": "seen-urls", "behavior": "Skip visited URLs.", "refs": [TARGET_REF]}
        ],
        "gaps": [
            {
                "id": "missing-retries",
                "description": "Transient 503 is not retried.",
                "basis": "observed_behavior",
                "refs": [TARGET_REF],
            }
        ],
        "style": {
            "naming": "Snake case.",
            "error_handling": "Preserve HTTPX errors.",
            "configuration": "Constructor arguments.",
            "refs": [TARGET_REF],
        },
        "integration_points": [TARGET_REF],
    }


def final_profile():
    return {
        **profile_proposal(),
        "target_sha": TARGET_REF["commit_sha"],
        "tests": {
            "runner_id": "crawler-demo-pytest-v1",
            "test_paths": ["tests/test_crawler.py"],
            "baseline_capture_id": "test-captures/baseline/capture.json",
            "baseline_status": "passed",
        },
        "coverage": {
            "examined_paths": ["src/crawler.py"],
            "excluded_paths": [],
            "unexamined_areas": ["Other file ranges were not read."],
            "limits_reached": [],
        },
        "provenance": {
            "run_id": "run-1",
            "target_repo_id": "crawler-demo",
            "baseline_binding": {"artifact_id": "baseline-binding.json", "sha256": "c" * 64},
            "evidence_reads": {"artifact_id": "evidence-reads-target.json", "sha256": "d" * 64},
            "observed_gaps": [
                {
                    "gap_id": "missing-retries",
                    "capture_id": "test-captures/acceptance/capture.json",
                    "cases": [{"name": "test_transient_503", "outcome": "failed"}],
                }
            ],
        },
    }


def criteria(ratings=(5, 5, 5, 5, 5)):
    return {
        name: {
            "rating": rating,
            "reason": "Supported by source and target.",
            "evidence": [SOURCE_REF, TARGET_REF],
        }
        for name, rating in zip(CRITERION_NAMES, ratings, strict=True)
    }


def assessment():
    return {
        "feature_id": "retry",
        "criteria": criteria(),
        "adaptation": {
            "rationale": "Retry GET/503 at the existing fetch boundary.",
            "target_gap_ids": ["missing-retries"],
            "integration_points": [TARGET_REF],
            "production_paths": ["src/crawler.py"],
            "new_production_paths": [],
            "estimated_changed_lines": 30,
            "existing_dependencies": [{"name": "httpx", "kind": "runtime", "refs": [TARGET_REF]}],
            "runtime_dependency_additions": [],
            "requires_new_service": False,
        },
    }


def scored(feature_id="retry", *, ratings=(5, 5, 5, 5, 5), total=100, eligible=True):
    return {
        "feature_id": feature_id,
        "criteria": criteria(ratings),
        "gates": [
            {"name": name, "passed": True, "reason": "Controller checked."} for name in GATE_NAMES
        ],
        "eligible": eligible,
        "total": total,
    }


def result(candidates=None):
    return {
        "rubric_version": "fit-v1",
        "candidates": candidates or [scored()],
        "top3": ["retry"],
        "selected_feature_id": "retry",
        "rejected": [],
        "selection_reason": "The supported retry slice passes all gates.",
    }


@pytest.mark.parametrize(
    "schema",
    [
        TargetProfileProposal,
        AssessmentBatch,
        TargetProfile,
        FitResult,
        BaselineBinding,
        GateEvidence,
    ],
)
def test_selection_schemas_pass_provider_preflight(schema):
    document = strict_schema(schema)
    assert document["additionalProperties"] is False
    assert set(document["required"]) == set(document["properties"])


@pytest.mark.parametrize(
    "schema,payload",
    [
        (TargetProfileProposal, profile_proposal()),
        (TargetProfile, final_profile()),
        (AssessmentBatch, {"assessments": [assessment()]}),
        (FitResult, result()),
    ],
)
def test_contracts_round_trip_json_and_freeze_nested_collections(schema, payload):
    artifact = schema.model_validate(copy.deepcopy(payload))
    assert json.loads(artifact.model_dump_json()) == payload
    assert schema.model_validate_json(artifact.model_dump_json()) == artifact
    with pytest.raises(ValidationError, match="frozen"):
        artifact.unexpected = "change"
    if isinstance(artifact, TargetProfileProposal):
        assert isinstance(artifact.stack.dependencies, tuple)
        with pytest.raises(AttributeError):
            artifact.stack.dependencies.append("new")
        payload["stack"]["dependencies"].clear()
        assert artifact.stack.dependencies
    elif isinstance(artifact, AssessmentBatch):
        assert isinstance(artifact.assessments[0].criteria.gap_fit.evidence, tuple)
        with pytest.raises(ValidationError, match="frozen"):
            artifact.assessments[0].criteria.gap_fit.rating = 0
    else:
        assert isinstance(artifact.candidates[0].gates, tuple)


@pytest.mark.parametrize(
    "field,value", [("target_sha", "a" * 40), ("tests", {}), ("coverage", {}), ("provenance", {})]
)
def test_profile_model_cannot_set_controller_facts(field, value):
    with pytest.raises(ValidationError, match="Extra inputs"):
        TargetProfileProposal.model_validate({**profile_proposal(), field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("total", 100),
        ("gates", []),
        ("eligible", True),
        ("selected_feature_id", "retry"),
        ("top3", ["retry"]),
    ],
)
def test_matcher_model_cannot_set_controller_decisions(field, value):
    with pytest.raises(ValidationError, match="Extra inputs"):
        AssessmentBatch.model_validate({"assessments": [{**assessment(), field: value}]})


@pytest.mark.parametrize("rating", [-1, 6, True, "5", 4.5])
def test_ratings_are_strict_integers_within_the_rubric(rating):
    payload = assessment()
    payload["criteria"]["gap_fit"]["rating"] = rating
    with pytest.raises(ValidationError):
        AssessmentBatch.model_validate({"assessments": [payload]})


@pytest.mark.parametrize(
    "change", ["missing", "extra", "empty_reason", "long_reason", "no_evidence"]
)
def test_criteria_reject_invalid_or_unsupported_assessments(change):
    payload = assessment()
    if change == "missing":
        del payload["criteria"]["gap_fit"]
    elif change == "extra":
        payload["criteria"]["invented_criterion"] = criteria()["gap_fit"]
    else:
        payload["criteria"]["gap_fit"].update(
            {
                "empty_reason": {"reason": ""},
                "long_reason": {"reason": "x" * 2001},
                "no_evidence": {"evidence": []},
            }[change]
        )
    with pytest.raises(ValidationError):
        AssessmentBatch.model_validate({"assessments": [payload]})


def test_unknown_rating_can_retain_no_evidence_without_becoming_a_pass():
    payload = assessment()
    payload["criteria"]["gap_fit"].update(rating=0, evidence=[])
    artifact = AssessmentBatch.model_validate({"assessments": [payload]})
    assert not artifact.assessments[0].criteria.meets_thresholds


def test_duplicate_assessment_ids_are_rejected():
    with pytest.raises(ValidationError, match="unique"):
        AssessmentBatch.model_validate({"assessments": [assessment(), assessment()]})


def test_infeasible_adaptation_can_disclose_no_integration_or_edit_paths():
    payload = assessment()
    payload["adaptation"].update(
        rationale="No bounded adaptation is supported by the available evidence.",
        target_gap_ids=[],
        integration_points=[],
        production_paths=[],
        new_production_paths=[],
        estimated_changed_lines=0,
    )
    artifact = AssessmentBatch.model_validate({"assessments": [payload]})
    assert artifact.assessments[0].adaptation.production_paths == ()


@pytest.mark.parametrize("change", ["duplicate_path", "new_path_outside_scope", "negative_lines"])
def test_adaptation_scope_rejects_inconsistent_claims(change):
    payload = assessment()
    if change == "duplicate_path":
        payload["adaptation"]["production_paths"] *= 2
    elif change == "new_path_outside_scope":
        payload["adaptation"]["new_production_paths"] = ["src/new.py"]
    else:
        payload["adaptation"]["estimated_changed_lines"] = -1
    with pytest.raises(ValidationError):
        AssessmentBatch.model_validate({"assessments": [payload]})


@pytest.mark.parametrize("change", ["duplicate_claim", "module_path", "duplicate_module"])
def test_profile_claim_identity_and_module_evidence(change):
    payload = profile_proposal()
    if change == "duplicate_claim":
        payload["gaps"][0]["id"] = "seen-urls"
    elif change == "module_path":
        payload["architecture"]["modules"][0]["path"] = "unrelated.py"
    else:
        payload["architecture"]["modules"] *= 2
    with pytest.raises(ValidationError):
        TargetProfileProposal.model_validate(payload)


@pytest.mark.parametrize(
    "section",
    ["stack", "dependency", "entry", "module", "capability", "gap", "style", "integration"],
)
@pytest.mark.parametrize("field,value", [("repo_id", "other"), ("commit_sha", "f" * 40)])
def test_final_profile_checks_snapshot_identity_for_every_citation_group(section, field, value):
    payload = copy.deepcopy(final_profile())
    refs = {
        "stack": payload["stack"]["refs"],
        "dependency": payload["stack"]["dependencies"][0]["refs"],
        "entry": payload["architecture"]["entry_points"],
        "module": payload["architecture"]["modules"][0]["refs"],
        "capability": payload["capabilities"][0]["refs"],
        "gap": payload["gaps"][0]["refs"],
        "style": payload["style"]["refs"],
        "integration": payload["integration_points"],
    }[section]
    refs[0] = {**refs[0], field: value}
    with pytest.raises(ValidationError, match="exact target snapshot"):
        TargetProfile.model_validate(payload)


@pytest.mark.parametrize("change", ["absent", "unknown_gap", "duplicate", "no_cases", "inferred"])
def test_observed_gaps_require_controller_case_bindings(change):
    payload = copy.deepcopy(final_profile())
    evidence = payload["provenance"]["observed_gaps"]
    if change == "absent":
        evidence.clear()
    elif change == "unknown_gap":
        evidence[0]["gap_id"] = "depth"
    elif change == "duplicate":
        evidence *= 2
    elif change == "no_cases":
        evidence[0]["cases"] = []
    else:
        payload["gaps"][0]["basis"] = "goal_inference"
    with pytest.raises(ValidationError):
        TargetProfile.model_validate(payload)


def test_inferred_gaps_do_not_require_or_claim_runtime_evidence():
    payload = final_profile()
    payload["gaps"][0]["basis"] = "goal_inference"
    payload["provenance"]["observed_gaps"] = []
    assert TargetProfile.model_validate(payload).gaps[0].basis == "goal_inference"


@pytest.mark.parametrize("status,passed", [("pass", True), ("fail", False), ("unknown", False)])
def test_only_explicit_controller_gate_pass_can_pass(status, passed):
    evidence = GateEvidence(
        name="target_gap", status=status, reason="Captured evidence.", evidence=[]
    )
    assert GateResult.from_evidence(evidence).passed is passed


@pytest.mark.parametrize(
    "change",
    [
        "fake_total",
        "missing_gate",
        "duplicate_gate",
        "failed_gate",
        "minimum_rating",
        "below_total",
    ],
)
def test_scores_cannot_override_policy(change):
    payload = scored()
    if change == "fake_total":
        payload["total"] = 99
    elif change == "missing_gate":
        payload["gates"].pop()
    elif change == "duplicate_gate":
        payload["gates"][-1] = payload["gates"][0]
    elif change == "failed_gate":
        payload["gates"][0]["passed"] = False
    elif change == "minimum_rating":
        payload = scored(ratings=(5, 5, 5, 5, 2), total=94)
    else:
        payload = scored(ratings=(4, 4, 3, 3, 4), total=74)
    with pytest.raises(ValidationError):
        CandidateScore.model_validate(payload)


def test_exact_threshold_is_eligible_and_short_lists_are_valid():
    payload = result([scored(ratings=(4, 4, 4, 3, 3), total=75)])
    assert FitResult.model_validate(payload).top3 == ("retry",)


def test_no_fit_retains_rejection_and_requires_explicit_null_selection():
    candidate = scored(eligible=False)
    candidate["gates"][4]["passed"] = False
    payload = {
        **result([candidate]),
        "top3": [],
        "selected_feature_id": None,
        "rejected": [{"feature_id": "retry", "reasons": ["Behavior is already present."]}],
    }
    assert FitResult.model_validate(payload).selected_feature_id is None
    del payload["selected_feature_id"]
    with pytest.raises(ValidationError, match="Field required"):
        FitResult.model_validate(payload)


@pytest.mark.parametrize(
    "change",
    [
        "unknown_selection",
        "rejected_in_top",
        "missing_rejection",
        "duplicate_rejection",
        "empty_reasons",
        "duplicate_candidate",
    ],
)
def test_result_rejects_inconsistent_candidate_identity(change):
    failed = scored("depth", eligible=False)
    failed["gates"][0]["passed"] = False
    payload = result([scored(), failed])
    payload["rejected"] = [{"feature_id": "depth", "reasons": ["No supported trace."]}]
    if change == "unknown_selection":
        payload["selected_feature_id"] = "invented"
    elif change == "rejected_in_top":
        payload["top3"].append("depth")
    elif change == "missing_rejection":
        payload["rejected"] = []
    elif change == "duplicate_rejection":
        payload["rejected"] *= 2
    elif change == "empty_reasons":
        payload["rejected"][0]["reasons"] = []
    else:
        payload["candidates"].append(scored())
    with pytest.raises(ValidationError):
        FitResult.model_validate(payload)


def test_tied_candidates_use_stable_ids_and_top_three_never_pads():
    payload = {
        **result([scored(name) for name in ("delta", "beta", "alpha", "gamma")]),
        "top3": ["alpha", "beta", "delta"],
        "selected_feature_id": "alpha",
    }
    assert FitResult.model_validate(payload).top3 == ("alpha", "beta", "delta")
    payload["top3"] = ["delta", "beta", "alpha"]
    with pytest.raises(ValidationError, match="policy order"):
        FitResult.model_validate(payload)


@pytest.mark.parametrize(
    "winner,loser,total",
    [
        ((5, 5, 4, 4, 3), (5, 4, 5, 4, 4), 90),  # isolation before testability/dependency
        ((5, 4, 3, 3, 5), (4, 4, 4, 5, 4), 83),  # testability before dependency
        ((5, 4, 5, 4, 4), (5, 4, 4, 5, 4), 90),  # dependency before feature ID
    ],
)
def test_tie_break_priority_is_independent_of_input_order(winner, loser, total):
    candidates = [
        scored("alpha", ratings=loser, total=total),
        scored("zeta", ratings=winner, total=total),
    ]
    payload = {**result(candidates), "top3": ["zeta", "alpha"], "selected_feature_id": "zeta"}
    assert FitResult.model_validate(payload).top3 == ("zeta", "alpha")
    payload["candidates"].reverse()
    assert FitResult.model_validate(payload).top3 == ("zeta", "alpha")
