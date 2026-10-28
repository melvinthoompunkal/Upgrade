import asyncio
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from test_meta_client import completion, settings
from test_target_profiler import finalized_profile, read_all, ref  # noqa: F401

from app import fit_matcher
from app.errors import BudgetError, EvidenceError
from app.evidence_contracts import SourceTrace
from app.fit_matcher import FitMatcher
from app.models.budget import ModelBudget
from app.models.meta_client import MetaClient
from app.selection_contracts import AssessmentBatch
from app.tools.evidence import EvidenceSession
from app.tools.repositories import SCRAPY_URL, Registration, RepositoryManager

SOURCE_FILES = {
    "scrapy/downloadermiddlewares/retry.py": (
        "def get_retry_request():\n    return 2\n\n"
        "class RetryMiddleware:\n"
        "    def _retry(self):\n        return 2\n"
        "    def process_response(self):\n        return self._retry()\n"
    ),
    "scrapy/settings/default_settings.py": "RETRY_TIMES = 2\nRETRY_HTTP_CODES = [503]\n",
    "tests/test_downloadermiddleware_retry.py": (
        "class TestRetry:\n"
        "    def test_503(self):\n        assert True\n"
        "    def test_404(self):\n        assert True\n"
        "class TestMaxRetryTimes:\n"
        "    def test_with_settings_zero(self):\n        assert True\n"
    ),
}


@pytest.fixture
def matching_context(baseline_context, committed_repo, tmp_path, monkeypatch):
    target, baseline, _ = baseline_context
    root, git = committed_repo
    for path, content in SOURCE_FILES.items():
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
    (root / "LICENSE").write_bytes(
        (Path(__file__).parent / "fixtures/scrapy-LICENSE.txt").read_text(encoding="utf-8").encode()
    )
    git.run("add", "--", *SOURCE_FILES, "LICENSE", cwd=root)
    git.run("-c", "commit.gpgSign=false", "commit", "-m", "source fixture", cwd=root)
    manager = RepositoryManager(tmp_path / "source-manager")
    repo, _ = manager.local_target(Registration("source", root))
    repo = replace(repo, snapshot=repo.snapshot.model_copy(update={"origin": SCRAPY_URL}))
    source = EvidenceSession(repo)
    read_all(source)
    monkeypatch.setattr(fit_matcher, "SCRAPY_SHA", repo.snapshot.commit_sha)
    profile = finalized_profile(target, baseline)
    implementation = [
        ref(source, "scrapy/downloadermiddlewares/retry.py", "RetryMiddleware._retry"),
        ref(source, "scrapy/settings/default_settings.py"),
    ]
    trace = SourceTrace.model_validate(
        {
            "source_sha": repo.snapshot.commit_sha,
            "features": [
                {
                    "feature_id": "bounded-retries",
                    "name": "Bounded retries",
                    "behavior": "Retry GET 503.",
                    "entry_points": [implementation[0]],
                    "implementation": implementation,
                    "dependencies": [],
                    "source_tests": [
                        ref(source, "tests/test_downloadermiddleware_retry.py", s)
                        for s in (
                            "TestRetry.test_503",
                            "TestRetry.test_404",
                            "TestMaxRetryTimes.test_with_settings_zero",
                        )
                    ],
                    "acceptance_behaviors": ["503 recovery and exhaustion"],
                    "transferable_slice": "Bounded GET retries",
                    "excluded_behaviors": ["Scrapy internals"],
                    "license_spdx": "BSD-3-Clause",
                    "license_refs": [ref(source, "LICENSE")],
                    "evidence_status": "supported",
                }
            ],
            "coverage": {
                "examined_paths": [],
                "excluded_paths": [],
                "unexamined_areas": [],
                "limits_reached": [],
            },
        }
    )
    return FitMatcher(None, source, target, trace, profile, baseline)


def batch_data(matcher, rating=5):
    target = matcher.target
    fetch = ref(target, "src/crawler.py", "Crawler.fetch")
    return {
        "assessments": [
            {
                "feature_id": "bounded-retries",
                "criteria": {
                    name: {
                        "rating": rating,
                        "reason": "Supported by code and tests",
                        "evidence": [fetch],
                    }
                    for name in (
                        "gap_fit",
                        "isolation",
                        "dependency_cost",
                        "stack_compatibility",
                        "testability",
                    )
                },
                "adaptation": {
                    "rationale": "Bound three GET attempts in fetch",
                    "target_gap_ids": ["retry-503"],
                    "integration_points": [fetch],
                    "production_paths": ["src/crawler.py"],
                    "new_production_paths": [],
                    "estimated_changed_lines": 25,
                    "existing_dependencies": [
                        {
                            "name": "httpx",
                            "kind": "runtime",
                            "refs": [ref(target, "pyproject.toml")],
                        }
                    ],
                    "runtime_dependency_additions": [],
                    "requires_new_service": False,
                },
            }
        ]
    }


def test_controller_computes_selection_and_preserves_source_coupling(matching_context):
    matcher = matching_context
    result, gates = matcher.select(AssessmentBatch.model_validate(batch_data(matcher)))
    assert result.selected_feature_id == "bounded-retries"
    assert result.candidates[0].total == 100
    assert all(g["status"] == "pass" for g in gates[0]["gates"])


def test_direct_retry_counter_evidence_can_replace_its_delegating_wrapper(matching_context):
    matcher = matching_context
    trace = matcher.trace.model_dump(mode="json")
    trace["features"][0]["implementation"][0] = ref(
        matcher.source, "scrapy/downloadermiddlewares/retry.py", "get_retry_request"
    )
    matcher.trace = SourceTrace.model_validate(trace)
    result, _ = matcher.select(AssessmentBatch.model_validate(batch_data(matcher)))
    assert result.selected_feature_id == "bounded-retries"


@pytest.mark.parametrize(
    "change,gate",
    [
        ("partial", "source_provenance"),
        ("notice", "source_use_policy"),
        ("dependency", "existing_dependencies"),
        ("empty-deps", "existing_dependencies"),
        ("service", "existing_dependencies"),
        ("scope", "isolated_adaptation"),
        ("test-edit", "isolated_adaptation"),
        ("missing-gap", "target_gap"),
        ("missing-tests", "sufficient_coverage"),
    ],
)
def test_high_score_never_overrides_failed_gate(matching_context, change, gate):
    matcher = matching_context
    data = batch_data(matcher)
    plan = data["assessments"][0]["adaptation"]
    if change in {"partial", "notice", "missing-tests"}:
        trace = matcher.trace.model_dump(mode="json")
        field, value = {
            "partial": ("evidence_status", "partial"),
            "notice": ("license_spdx", "MIT"),
            "missing-tests": ("source_tests", []),
        }[change]
        trace["features"][0][field] = value
        matcher.trace = SourceTrace.model_validate(trace)
    elif change == "dependency":
        plan["runtime_dependency_additions"] = ["scrapy"]
    elif change == "empty-deps":
        plan["existing_dependencies"] = []
    elif change == "service":
        plan["requires_new_service"] = True
    elif change == "scope":
        plan["estimated_changed_lines"] = 401
    elif change == "test-edit":
        plan["production_paths"] = ["tests/test_crawler.py"]
    elif change == "missing-gap":
        plan["target_gap_ids"] = []
    result, _ = matcher.select(AssessmentBatch.model_validate(data))
    assert result.selected_feature_id is None and result.top3 == ()
    assert result.candidates[0].total == 100
    assert not next(g.passed for g in result.candidates[0].gates if g.name == gate)
    assert result.rejected[0].reasons


def test_exact_catalog_and_repo_membership_are_required(matching_context):
    matcher = matching_context
    for change in ("id", "repo", "range"):
        data = batch_data(matcher)
        item = data["assessments"][0]
        if change == "id":
            item["feature_id"] = "invented"
        elif change == "repo":
            item["criteria"]["gap_fit"]["evidence"][0]["repo_id"] = "unknown"
        else:
            item["criteria"]["gap_fit"]["evidence"][0]["end_line"] = 900
        with pytest.raises(EvidenceError):
            matcher.select(AssessmentBatch.model_validate(data))


@pytest.mark.parametrize("repeat", [False, True])
def test_one_qualified_clarification_round_with_denials(matching_context, repeat):
    matcher = matching_context
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["reasoning_effort"] == "high"
        if len(requests) == 1 or repeat:
            return httpx.Response(
                200,
                json=completion(
                    calls=[
                        {
                            "id": "read",
                            "type": "function",
                            "function": {
                                "name": "target_read_range",
                                "arguments": json.dumps(
                                    {"path": "missing.py", "start_line": 1, "end_line": 1}
                                ),
                            },
                        }
                    ]
                ),
            )
        assert body["messages"][-1]["tool_call_id"] == "read"
        assert "invalid_evidence" in body["messages"][-1]["content"]
        return httpx.Response(200, json=completion(batch_data(matcher)))

    async def run():
        async with MetaClient(
            settings(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ) as client:
            matcher.client = client
            if repeat:
                with pytest.raises(EvidenceError, match="single clarification"):
                    await matcher.assess("resilience")
            else:
                assert (await matcher.assess("resilience")).assessments[
                    0
                ].feature_id == "bounded-retries"
            assert client.budget.requests == 2
            assert matcher.events[0]["status"] == "rejected"

    asyncio.run(run())


def test_shared_budget_does_not_reset_for_matching(matching_context):
    async def run():
        budget = ModelBudget(max_requests=1)
        async with MetaClient(
            settings(),
            budget=budget,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json=completion()))
            ),
        ) as client:
            await client.request(stage="previous_stage", messages=[])
            matching_context.client = client
            with pytest.raises(BudgetError):
                await matching_context.assess("resilience")
            assert budget.requests == 1

    asyncio.run(run())
