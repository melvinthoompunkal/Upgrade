"""Profile evidence uses disposable Git blobs and mocked containers, never host imports."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from test_meta_client import completion, settings

from app.errors import EvidenceError, UnsupportedTargetError
from app.models.meta_client import MetaClient
from app.selection_artifacts import ArtifactStore, read_log
from app.selection_contracts import TargetProfileProposal
from app.target_profiler import CAPABILITY_TESTS, TargetProfiler, inspect_target


def ref(session, path, symbol=None):
    if symbol:
        s = next(s for s in session.index(path).symbols if s.name == symbol)
        start, end = s.start_line, s.end_line
    else:
        start, end = 1, len(session.repo.read(path).splitlines())
    return {
        "repo_id": session.repo.snapshot.repo_id,
        "commit_sha": session.repo.snapshot.commit_sha,
        "path": path,
        "start_line": start,
        "end_line": end,
        "symbol": symbol,
    }


def read_all(session):
    for entry in session.repo.inventory.files:
        size = len(session.repo.read(entry.path).splitlines())
        for start in range(1, size + 1, 200):
            session.read_range(entry.path, start, min(start + 199, size))


def proposal_data(session, *, gap=True):
    fetch = ref(session, "src/crawler.py", "Crawler.fetch")
    crawl = ref(session, "src/crawler.py", "Crawler.crawl")
    manifest = ref(session, "pyproject.toml")
    return {
        "stack": {
            "language": "Python",
            "python_requirement": ">=3.12,<3.13",
            "frameworks": [],
            "dependencies": [{"name": "httpx", "kind": "runtime", "refs": [manifest]}],
            "refs": [manifest],
        },
        "architecture": {
            "entry_points": [fetch],
            "modules": [{"path": "src/crawler.py", "role": "Fetch and traverse.", "refs": [crawl]}],
        },
        "capabilities": [
            {
                "id": name,
                "behavior": name,
                "refs": [
                    fetch if name == "fetch" else crawl,
                    ref(session, "tests/test_crawler.py", test),
                ],
            }
            for name, test in CAPABILITY_TESTS.items()
        ]
        + [
            {
                "id": "client-ownership",
                "behavior": "Caller owns client.",
                "refs": [ref(session, "src/crawler.py", "Crawler")],
            }
        ],
        "gaps": [
            {
                "id": "retry-503",
                "description": "503 recovery is absent.",
                "basis": "observed_behavior",
                "refs": [fetch],
            }
        ]
        if gap
        else [],
        "style": {
            "naming": "snake_case",
            "error_handling": "HTTPX status errors",
            "configuration": "Explicit keyword limits",
            "refs": [fetch, crawl],
        },
        "integration_points": [fetch],
    }


def finalized_profile(session, baseline, *, gap=True):
    read_all(session)
    profiler = TargetProfiler(None, session, baseline)
    proposal = TargetProfileProposal.model_validate(proposal_data(session, gap=gap))
    store = ArtifactStore(baseline.directory, {"run_id": baseline.binding.run_id})
    reads = store.json("profile-reads.json", read_log(session, []))
    return profiler.finalize(proposal, store.adopt("baseline-binding.json"), reads)


def test_profile_tool_protocol_and_capture_binding(baseline_context):
    session, baseline, _ = baseline_context
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["reasoning_effort"] == "medium"
        if len(requests) == 1:
            calls = [
                {
                    "id": f"read-{i}",
                    "type": "function",
                    "function": {
                        "name": "read_range",
                        "arguments": json.dumps(
                            {
                                "path": f.path,
                                "start_line": 1,
                                "end_line": len(session.repo.read(f.path).splitlines()),
                            }
                        ),
                    },
                }
                for i, f in enumerate(session.repo.inventory.files)
            ]
            return httpx.Response(200, json=completion(calls=calls))
        assert len([m for m in body["messages"] if m["role"] == "tool"]) == 3
        return httpx.Response(200, json=completion(proposal_data(session)))

    async def run():
        async with MetaClient(
            settings(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        ) as client:
            profiler = TargetProfiler(client, session, baseline)
            proposal = await profiler.propose("resilience")
            store = ArtifactStore(baseline.directory, {})
            profile = profiler.finalize(
                proposal,
                store.adopt("baseline-binding.json"),
                store.json("reads.json", read_log(session, profiler.events)),
            )
            assert profile.tests.baseline_status == "passed"
            assert len(profile.provenance.observed_gaps[0].cases) == 7
            assert len(profile.coverage.examined_paths) == 3
            assert len(profiler.events) == 3 and client.budget.requests == 2

    asyncio.run(run())


@pytest.mark.parametrize(
    "change",
    [
        "wrong-sha",
        "wrong-repo",
        "wrong-symbol",
        "unread",
        "depth-observed",
        "duplicate-gap",
        "wrong-stack",
        "wrong-test",
    ],
)
def test_invalid_profile_claims_are_rejected(baseline_context, change):
    session, baseline, _ = baseline_context
    if change != "unread":
        read_all(session)
    data = proposal_data(session)
    if change.startswith("wrong-") and change not in {"wrong-stack", "wrong-test"}:
        field = {"wrong-sha": "commit_sha", "wrong-repo": "repo_id", "wrong-symbol": "symbol"}[
            change
        ]
        data["gaps"][0]["refs"][0][field] = "a" * 40 if field == "commit_sha" else "fake"
    elif change == "depth-observed":
        data["gaps"][0]["id"] = "depth-limit"
    elif change == "duplicate-gap":
        data["gaps"][0]["id"] = "duplicate-visits-missing"
        # The registered equivalent identity is reserved; independent prose is provisional only.
        data["gaps"][0]["basis"] = "observed_behavior"
    elif change == "wrong-stack":
        data["stack"]["dependencies"][0]["name"] = "scrapy"
    elif change == "wrong-test":
        data["capabilities"][0]["refs"] = [ref(session, "src/crawler.py", "Crawler.fetch")]
    with pytest.raises(EvidenceError):
        TargetProfiler(None, session, baseline).validate(TargetProfileProposal.model_validate(data))


@pytest.mark.parametrize("change", ["sha", "prefix", "run", "snapshot", "acceptance", "logs"])
def test_baseline_revalidation_rejects_cross_run_or_tampered_bytes(baseline_context, change):
    session, baseline, _ = baseline_context
    if change in {"sha", "prefix", "run"}:
        field = {"sha": "target_sha", "prefix": "target_path_prefix", "run": "run_id"}[change]
        data = baseline.binding.model_dump()
        data[field] = "a" * 40 if change == "sha" else "wrong"
        baseline = replace(baseline, binding=type(baseline.binding).model_validate(data))
    else:
        path = {
            "snapshot": "snapshot/src/crawler.py",
            "acceptance": "acceptance/test_retries.py",
            "logs": "test-captures/baseline/stdout.txt",
        }[change]
        (baseline.directory / path).write_text("tampered")
    with pytest.raises(EvidenceError):
        baseline.verify(session.repo)


@pytest.mark.parametrize(
    "manifest",
    [
        '[project]\nrequires-python=">=3.11"\ndependencies=["httpx==0.28.1"]',
        '[project]\nrequires-python=">=3.12,<3.13"\ndynamic=["dependencies"]',
        '[project]\ndependencies=["httpx[http2]==0.28.1"]',
        '[project]\ndependencies=["other>=1; python_version>3"]',
        "[broken",
    ],
)
def test_unsupported_manifests_never_execute_or_install(baseline_context, monkeypatch, manifest):
    session, _, _ = baseline_context
    original = type(session.repo).read
    monkeypatch.setattr(
        type(session.repo),
        "read",
        lambda repo, path: manifest if path == "pyproject.toml" else original(repo, path),
    )
    with pytest.raises(UnsupportedTargetError):
        inspect_target(session)
