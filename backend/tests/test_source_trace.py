import argparse
import asyncio
import hashlib
import json
from dataclasses import replace
from importlib import import_module
from pathlib import Path

import httpx
import pytest
from test_evidence import card
from test_meta_client import completion, settings

from app import source_trace
from app.config import Settings
from app.errors import EvidenceError
from app.evidence_contracts import Coverage, SourceTrace
from app.models.meta_client import MetaClient
from app.tools.git import Git
from app.tools.repositories import Registration
from app.tracer import SourceTracer
from app.workflow_inputs import TraceInputs, WorkflowContext


@pytest.fixture
def trace_workflow(session, committed_repo, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.tracer.FOCUS_PATHS", ("src/retry.py", "pyproject.toml", "tests/test_retry.py")
    )

    class Tracer(SourceTracer):
        events = []

        def __init__(self, client, evidence, **kwargs):
            self.client, self.evidence = client, evidence

        async def trace(self, goal):
            self.evidence.read_range("src/retry.py", 1, 4)
            response = await self.client.structured(stage="trace", messages=[], schema=SourceTrace)
            return response.data

    trace = SourceTrace(
        source_sha=session.repo.snapshot.commit_sha,
        features=[card(session)],
        coverage=Coverage(
            examined_paths=["src/retry.py"],
            excluded_paths=[],
            unexamined_areas=[],
            limits_reached=[],
        ),
    )
    monkeypatch.setattr(source_trace, "SourceTracer", Tracer)
    monkeypatch.setattr(source_trace, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        source_trace,
        "registered_target",
        lambda *_: Registration("target", committed_repo[0]),
    )
    git_run = Git.run

    def local_transport(git, *args, **kwargs):
        return git_run(
            git,
            *(
                str(committed_repo[0]) if a == "https://github.com/scrapy/scrapy" else a
                for a in args
            ),
            **kwargs,
        )

    monkeypatch.setattr(Git, "run", local_transport)
    monkeypatch.setattr(
        source_trace,
        "MetaClient",
        lambda config: MetaClient(
            config,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(200, json=completion(trace.model_dump(mode="json")))
                )
            ),
        ),
    )
    inputs = TraceInputs("https://github.com/scrapy/scrapy", trace.source_sha, "target", "retry")
    return inputs, WorkflowContext(tmp_path / "artifacts", settings())


@pytest.mark.parametrize("latest", [False, True])
def test_trace_publishes_complete_hash_checked_artifacts(trace_workflow, latest):
    inputs, context = trace_workflow
    expected_sha = inputs.source_sha
    if latest:
        inputs = replace(inputs, source_sha=None)
    report, path = asyncio.run(source_trace.record_source_trace(inputs, context=context))
    assert report["status"] == "passed"
    assert report["analyzed_feature_count"] == 1
    assert report["usage"]["requests"] == 1
    assert json.loads(path.read_bytes()) == report
    assert report["source"]["commit_sha"] == expected_sha
    bundle = json.loads((path.parent / "trace-bundle.json").read_bytes())
    assert bundle["source"]["commit_sha"] == expected_sha
    for name, expected in report["artifact_hashes"].items():
        assert hashlib.sha256((path.parent / name).read_bytes()).hexdigest() == expected


@pytest.mark.parametrize("name", ["feature-card.json", "evidence-reads.json"])
def test_sidecar_failure_retains_json_usage_and_intact_artifacts(trace_workflow, monkeypatch, name):
    inputs, context = trace_workflow
    write = Path.write_bytes

    def fail_sidecar(path, content):
        # Match both the old direct destination and atomic publication's temporary file.
        if name in path.name:
            raise PermissionError("private host path and failure detail")
        return write(path, content)

    monkeypatch.setattr(Path, "write_bytes", fail_sidecar)
    report, path = asyncio.run(source_trace.record_source_trace(inputs, context=context))
    assert report["status"] == "failed"
    assert report["usage"]["requests"] == 1
    assert "source-trace.json" in report["artifact_hashes"]
    assert report["analyzed_feature_count"] == 1
    assert name not in report["artifact_hashes"]
    assert name in report["unavailable_artifacts"]
    assert json.loads(path.read_bytes()) == report
    assert "private host path" not in path.read_text()

    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(
        create_app(settings(), data_root=context.artifact_root.parent, start_worker=False)
    ) as client:
        detail = client.get(f"/api/runs/{report['run_id']}")
        assert detail.status_code == 200
        assert detail.json()["outcome"] == "failed"
        assert detail.json()["analyzed_feature_count"] == 1
        assert "private host path" not in detail.text


def test_read_log_failure_preserves_original_trace_error(trace_workflow, monkeypatch):
    inputs, context = trace_workflow

    async def reject(tracer, goal):
        tracer.evidence.read_range("src/retry.py", 1, 4)
        raise EvidenceError("The source trace was rejected.")

    monkeypatch.setattr(source_trace.SourceTracer, "trace", reject)
    write = Path.write_bytes

    def fail_read_log(path, content):
        if "evidence-reads.json" in path.name:
            raise PermissionError("private failure detail")
        return write(path, content)

    monkeypatch.setattr(Path, "write_bytes", fail_read_log)
    report, path = asyncio.run(source_trace.record_source_trace(inputs, context=context))
    assert report["error_code"] == "invalid_evidence"
    assert report["detail"] == "The source trace was rejected."
    assert report["analyzed_feature_count"] == 0
    assert json.loads(path.read_bytes()) == report
    assert "private failure" not in path.read_text()


def test_missing_key_saves_sanitized_failure_receipt_before_git_or_inference(tmp_path, monkeypatch):
    monkeypatch.setattr(source_trace, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        source_trace, "Settings", lambda: Settings(_env_file=None, model_api_key=None)
    )
    args = argparse.Namespace(
        source_url="https://github.com/scrapy/scrapy",
        source_sha="a" * 40,
        target="crawler-demo",
        goal="bounded retries",
    )
    report, path = asyncio.run(source_trace.record_source_trace(args))
    assert report["status"] == "failed"
    assert report["error_code"] == "missing_configuration"
    assert json.loads(path.read_text()) == report
    assert not (path.parent / "repositories").exists()


@pytest.mark.parametrize("module_name", ["source_trace", "fit_select"])
def test_other_source_rejected_by_service_before_git_docker_or_inference(
    tmp_path, monkeypatch, module_name
):
    module = import_module(f"app.{module_name}")

    def reject_work(*args, **kwargs):
        pytest.fail("An unsupported crawler source must stop before repository or Docker work.")

    monkeypatch.setattr(module, "RepositoryManager", reject_work)
    if module_name == "fit_select":
        monkeypatch.setattr(module, "record_pinned_baseline", reject_work)
    inputs = TraceInputs(
        "https://github.com/openclaw/openclaw", None, "crawler-demo", "Improve web scraping."
    )
    context = WorkflowContext(tmp_path / "artifacts", settings())
    record = getattr(
        module, f"record_{'fit_selection' if module_name == 'fit_select' else module_name}"
    )
    report, path = asyncio.run(record(inputs, context=context))
    assert report["error_code"] == "unsupported_source"
    assert "crawler example supports only" in report["detail"]
    assert report["usage"]["requests"] == 0
    assert not (path.parent / "repositories").exists()
    assert json.loads(path.read_bytes()) == report


def test_missing_retry_files_stop_trace_before_target_retrieval(trace_workflow, monkeypatch):
    inputs, context = trace_workflow
    monkeypatch.setattr("app.tracer.FOCUS_PATHS", ("missing-retry.py",))
    report, path = asyncio.run(source_trace.record_source_trace(inputs, context=context))
    assert report["error_code"] == "unsupported_source"
    assert report["usage"]["requests"] == 0
    assert not (path.parent / "repositories/target.git").exists()
    assert json.loads(path.read_bytes()) == report


def test_unwritable_trace_receipt_has_sanitized_cli_diagnostic(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(source_trace, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        source_trace, "Settings", lambda: Settings(_env_file=None, model_api_key=None)
    )
    monkeypatch.setattr("sys.argv", ["graft-source-trace"])
    write = Path.write_bytes

    def fail_report(path, content):
        if "report.json" in path.name:
            raise PermissionError("private host path and failure detail")
        return write(path, content)

    monkeypatch.setattr(Path, "write_bytes", fail_report)
    assert source_trace.main() == 1
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err)["error_code"] == "publication_failed"
    assert "private host path" not in output.err
    assert str(tmp_path) not in output.err
