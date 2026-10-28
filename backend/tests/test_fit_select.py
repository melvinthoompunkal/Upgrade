"""Whole Stage 4A protocol with real Git snapshots and mocked Meta/Docker only."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from test_fit_matcher import batch_data
from test_fit_matcher import matching_context as matching_context
from test_meta_client import completion, settings
from test_pinned_baseline import write_capture
from test_target_profiler import proposal_data

from app import fit_select, pinned_baseline
from app.baseline import EXPECTED_PASS, EXPECTED_RED
from app.config import Settings
from app.models.budget import ModelBudget
from app.models.meta_client import MetaClient
from app.selection_artifacts import ArtifactStore
from app.tools.repositories import Registration, RepositoryManager
from app.tools.runner_artifacts import TestCase as Case
from app.tools.targets import CRAWLER_FILES, CRAWLER_PREFIX


@pytest.fixture
def workflow(matching_context, monkeypatch, tmp_path):
    matcher = matching_context
    root = tmp_path / "workflow"
    root.mkdir()
    # The fixture's registered repository owns the original target checkout.
    original_root = Path(
        json.loads((matcher.baseline.directory / "report.json").read_text())["original_target"][
            "root"
        ]
    )
    monkeypatch.setattr(fit_select, "PROJECT_ROOT", root)
    monkeypatch.setattr(fit_select, "Settings", settings)
    monkeypatch.setattr(
        fit_select,
        "registered_target",
        lambda _, target_id: Registration(
            "crawler-demo", original_root, CRAWLER_PREFIX, CRAWLER_FILES
        ),
    )

    def retrieve(manager, url, sha):
        storage = manager.directory / "source.git"
        manager.git.run(
            "clone",
            "--bare",
            "--no-local",
            "--no-hardlinks",
            "--template=",
            "--",
            str(matcher.source.repo.storage),
            str(storage),
        )
        return manager._inventory(storage, "source", url, matcher.source.repo.snapshot.commit_sha)

    monkeypatch.setattr(RepositoryManager, "public_source", retrieve)
    state = {"mode": "selected", "requests": []}

    def handler(request):
        body = json.loads(request.content)
        state["requests"].append(body)
        schema = body["response_format"]["json_schema"]["name"]
        if state["mode"] == "malformed":
            return httpx.Response(200, json=completion({"invalid": True}))
        if schema == "SourceTrace":
            if not any(m["role"] == "tool" for m in body["messages"]):
                paths = {
                    *[
                        r.path
                        for c in matcher.trace.features
                        for r in (
                            *c.entry_points,
                            *c.implementation,
                            *c.source_tests,
                            *c.license_refs,
                        )
                    ]
                }
                calls = [
                    {
                        "id": f"read-{i}",
                        "type": "function",
                        "function": {
                            "name": "read_range",
                            "arguments": json.dumps(
                                {
                                    "path": path,
                                    "start_line": 1,
                                    "end_line": len(matcher.source.repo.read(path).splitlines()),
                                }
                            ),
                        },
                    }
                    for i, path in enumerate(sorted(paths))
                ]
                return httpx.Response(200, json=completion(calls=calls))
            return httpx.Response(200, json=completion(matcher.trace.model_dump(mode="json")))
        payload = json.loads(body["messages"][1]["content"])
        target_sha = payload["target"]["commit_sha"]
        if schema == "TargetProfileProposal":
            if not any(m["role"] == "tool" for m in body["messages"]):
                calls = [
                    {
                        "id": f"target-{i}",
                        "type": "function",
                        "function": {
                            "name": "read_range",
                            "arguments": json.dumps(
                                {
                                    "path": f.path,
                                    "start_line": 1,
                                    "end_line": len(matcher.target.repo.read(f.path).splitlines()),
                                }
                            ),
                        },
                    }
                    for i, f in enumerate(matcher.target.repo.inventory.files)
                ]
                return httpx.Response(200, json=completion(calls=calls))
            data = proposal_data(matcher.target, gap=state["mode"] != "already-present")
        else:
            data = batch_data(matcher)
            if state["mode"] == "already-present":
                data["assessments"][0]["adaptation"]["target_gap_ids"] = []
            if state["mode"] == "tamper":
                directory = next((root / "data/artifacts").iterdir())
                (directory / "test-captures/baseline/stdout.txt").write_text("changed")
        data = json.loads(
            json.dumps(data).replace(matcher.target.repo.snapshot.commit_sha, target_sha)
        )
        return httpx.Response(200, json=completion(data))

    monkeypatch.setattr(
        fit_select,
        "MetaClient",
        lambda config, budget: MetaClient(
            config,
            budget=budget,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ),
    )
    args = argparse.Namespace(
        target="crawler-demo",
        goal="retry transient failures",
        source_url=matcher.source.repo.snapshot.origin,
        source_sha=matcher.source.repo.snapshot.commit_sha,
        image_receipt=matcher.baseline.image_receipt,
    )
    return args, state


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("selected", "selected"),
        ("already-present", "no_safe_fit"),
        ("malformed", "failed"),
        ("tamper", "failed"),
    ],
)
def test_complete_workflow_outcomes_artifacts_and_preservation(
    workflow, monkeypatch, mode, expected
):
    args, state = workflow
    state["mode"] = mode
    if mode == "already-present":
        run = pinned_baseline.run_suite

        def green(**kwargs):
            if kwargs["suite"] != "acceptance":
                return run(**kwargs)
            return write_capture(
                "acceptance",
                kwargs["image_id"],
                kwargs["directory"],
                [
                    Case(name, "passed", "", "")
                    for name in sorted(set(EXPECTED_RED) | EXPECTED_PASS)
                ],
            )

        monkeypatch.setattr(pinned_baseline, "run_suite", green)
    report, path = asyncio.run(fit_select.record_fit_selection(args))
    assert report["status"] == expected, report.get("detail")
    assert json.loads(path.read_text()) == report
    assert report["original_target_unchanged"] and report["worktree_unchanged"]
    assert not report["post_implementation_verified"] and not report["actual_patch_scope_checked"]
    assert not list(path.parent.rglob("changes.patch"))
    for name, digest in report["artifact_hashes"].items():
        assert hashlib.sha256((path.parent / name).read_bytes()).hexdigest() == digest
    if expected != "failed":
        assert report["usage"]["requests"] == 5
        assert report["analyzed_feature_count"] == 1
        assert report["unavailable_artifacts"] == []
        profile = json.loads((path.parent / "target-profile.json").read_text())["data"]
        reads = profile["provenance"]["evidence_reads"]
        assert reads["sha256"] == report["artifact_hashes"][reads["artifact_id"]]
        binding = json.loads((path.parent / "baseline-binding.json").read_text())
        assert binding["target_sha"] == profile["target_sha"] == report["target"]["commit_sha"]
        assert report["selected_feature_id"] == ("bounded-retries" if mode == "selected" else None)
    elif mode == "tamper":
        assert report["error_code"] == "invalid_evidence"
        assert "test-captures/baseline/stdout.txt" not in report["artifact_hashes"]
    else:
        assert report["error_code"] == "invalid_model_output"
        assert "fit-result.json" in report["unavailable_artifacts"]


def test_budget_exhaustion_between_stages_retains_attempts(workflow, monkeypatch):
    args, _ = workflow
    monkeypatch.setattr(fit_select, "ModelBudget", lambda: ModelBudget(max_requests=2))
    report, _ = asyncio.run(fit_select.record_fit_selection(args))
    assert report["status"] == "failed" and report["error_code"] == "budget_exhausted"
    assert report["stage"] == "source_traced"
    assert report["usage"]["requests"] == len(report["attempts"]) == 2
    assert "source-trace.json" in report["artifact_hashes"]
    assert "target-profile.json" in report["unavailable_artifacts"]


def test_mid_artifact_failure_retains_sanitized_receipt(workflow, monkeypatch):
    args, _ = workflow
    publish = ArtifactStore.derived

    def fail(store, artifact_id, *positional, **kwargs):
        if artifact_id == "source-catalog.json":
            raise OSError("private provider content must not leak")
        return publish(store, artifact_id, *positional, **kwargs)

    monkeypatch.setattr(ArtifactStore, "derived", fail)
    report, path = asyncio.run(fit_select.record_fit_selection(args))
    assert report["status"] == "failed"
    assert "private provider" not in path.read_text()
    assert "source-trace.json" in report["artifact_hashes"]
    assert "source-catalog.json" in report["unavailable_artifacts"]


def test_missing_credentials_receipt_precedes_retrieval(tmp_path, monkeypatch):
    monkeypatch.setattr(fit_select, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        fit_select, "Settings", lambda: Settings(_env_file=None, model_api_key=None)
    )
    args = argparse.Namespace(goal="resilience")
    report, path = asyncio.run(fit_select.record_fit_selection(args))
    assert report["status"] == "failed" and report["error_code"] == "missing_configuration"
    assert not (path.parent / "repositories").exists()
    assert report["usage"]["requests"] == 0


@pytest.mark.parametrize("status,code", [("selected", 0), ("no_safe_fit", 0), ("failed", 1)])
def test_cli_exit_semantics(monkeypatch, status, code):
    async def record(_):
        return {"status": status, "analyzed_feature_count": 1}, Path("receipt.json")

    monkeypatch.setattr(fit_select, "record_fit_selection", record)
    monkeypatch.setattr("sys.argv", ["graft-fit-select"])
    assert fit_select.main() == code


def test_optional_markdown_failure_preserves_structured_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(fit_select, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        fit_select, "Settings", lambda: Settings(_env_file=None, model_api_key=None)
    )
    publish = ArtifactStore.publish

    def fail_markdown(store, name, content):
        if name == "report.md":
            raise PermissionError("private path and error body")
        return publish(store, name, content)

    monkeypatch.setattr(ArtifactStore, "publish", fail_markdown)
    report, path = asyncio.run(fit_select.record_fit_selection(argparse.Namespace(goal="retry")))
    assert json.loads(path.read_text()) == report
    assert report["error_code"] == "missing_configuration"
    assert "report.md" in report["unavailable_artifacts"]
    assert "private path" not in path.read_text()


def test_failure_counts_agree_in_json_and_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(fit_select, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        fit_select, "Settings", lambda: Settings(_env_file=None, model_api_key=None)
    )
    report, path = asyncio.run(fit_select.record_fit_selection(argparse.Namespace(goal="retry")))
    assert report["analyzed_feature_count"] == 0
    text = path.with_suffix(".md").read_text()
    assert "Analyzed source features: **0**" in text
    assert "Eligible features: **0**" in text


def test_unwritable_output_cli_has_sanitized_terminal_diagnostic(tmp_path, monkeypatch, capsys):
    (tmp_path / "data").write_text("blocks artifact directory creation")
    monkeypatch.setattr(fit_select, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", ["graft-fit-select"])
    assert fit_select.main() == 1
    output = capsys.readouterr()
    assert not output.out
    error = json.loads(output.err)
    assert error["error_code"] in {"publication_failed", "selection_failed"}
    assert str(tmp_path) not in output.err


@pytest.mark.parametrize("change", ["missing", "extra", "type"])
def test_damaged_capture_is_retained_as_invalid_evidence(workflow, monkeypatch, change):
    from app.selection_baseline import SelectionBaseline

    args, _ = workflow
    from_run = SelectionBaseline.from_run

    def corrupt(directory, target, image):
        path = directory / "test-captures/baseline/capture.json"
        data = json.loads(path.read_text())
        if change == "missing":
            del data["diagnostic"]
        elif change == "extra":
            data["unexpected"] = "untrusted data"
        else:
            data["elapsed_ms"] = {"wrong": "shape"}
        path.write_text(json.dumps(data))
        return from_run(directory, target, image)

    monkeypatch.setattr(SelectionBaseline, "from_run", corrupt)
    report, path = asyncio.run(fit_select.record_fit_selection(args))
    assert report["error_code"] == "invalid_evidence"
    assert json.loads(path.read_text())["status"] == "failed"
    assert "test-captures/baseline/capture.json" not in report["artifact_hashes"]
    # The same retained failure is inspectable through the actual API projection.
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(
        create_app(settings(), data_root=path.parent.parent.parent, start_worker=False)
    ) as client:
        run = next(
            item
            for item in client.get("/api/runs").json()["items"]
            if item["id"] == report["run_id"]
        )
        detail = client.get(f"/api/runs/{run['id']}").json()
        assert detail["error"]["code"] == "invalid_evidence"
        assert detail["analyzed_feature_count"] == 0
