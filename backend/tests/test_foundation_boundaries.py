"""Audit reproductions at decoding and filesystem boundaries."""

import asyncio
import hashlib
import json
import os
import subprocess
from importlib import import_module

import pytest

from app.capture_metadata import decode_capture
from app.config import Settings
from app.errors import EvidenceError, GraftError
from app.execution import ProcessLock
from app.selection_artifacts import ArtifactStore
from app.workflow_inputs import TraceInputs, WorkflowContext


@pytest.mark.parametrize(
    "change", ["missing", "extra", "string-integer", "boolean-integer", "nested-extra"]
)
def test_even_hash_matching_capture_requires_strict_metadata(baseline_context, change):
    _, baseline, _ = baseline_context
    raw = (baseline.directory / "test-captures/baseline/capture.json").read_bytes()
    data = json.loads(raw)
    if change == "missing":
        del data["diagnostic"]
    elif change == "extra":
        data["unexpected"] = True
    elif change == "string-integer":
        data["elapsed_ms"] = "1"
    elif change == "boolean-integer":
        data["exit_code"] = False
    else:
        data["cases"][0]["extra"] = "invalid"
    raw = json.dumps(data).encode()
    with pytest.raises(EvidenceError, match="schema"):
        decode_capture(raw, hashlib.sha256(raw).hexdigest())


def test_digest_check_precedes_json_decoding():
    with pytest.raises(EvidenceError, match="bytes differ"):
        decode_capture(b"not even JSON", "0" * 64)


@pytest.mark.parametrize("redirect_root", [False, True])
def test_artifact_junction_rejected_before_any_outside_write(tmp_path, redirect_root):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "artifacts"
    root.mkdir()
    link = root / "exports"
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    store = ArtifactStore(link if redirect_root else root, {})
    try:
        with pytest.raises(GraftError):
            store.publish("probe.txt" if redirect_root else "exports/probe.txt", b"must not write")
        assert not list(outside.iterdir())
    finally:
        if os.name == "nt":
            os.rmdir(link)
        else:
            link.unlink()


@pytest.mark.parametrize("workflow", ["source_trace", "local_baseline"])
def test_workflow_rejects_linked_artifact_ancestor_before_creation(tmp_path, monkeypatch, workflow):
    from test_audit_regressions import make_link

    from app import baseline, source_trace

    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "redirected"
    make_link(link, outside)

    def must_not_run(*args):
        pytest.fail("Linked artifact roots must be rejected before provider/Docker access.")

    try:
        with pytest.raises(GraftError):
            if workflow == "source_trace":
                monkeypatch.setattr(source_trace, "MetaClient", must_not_run)
                asyncio.run(
                    source_trace.record_source_trace(
                        TraceInputs(
                            "https://github.com/example/source", "a" * 40, "crawler-demo", "retry"
                        ),
                        context=WorkflowContext(
                            link / "nested/artifacts", Settings(_env_file=None, model_api_key=None)
                        ),
                    )
                )
            else:
                monkeypatch.setattr(baseline, "prepared_image", must_not_run)
                baseline.record_baseline(tmp_path / "image.json", artifact_root=link / "nested")
        assert not list(outside.iterdir())
    finally:
        if os.name == "nt":
            os.rmdir(link)
        else:
            link.unlink()


@pytest.mark.parametrize(
    "module_name,service",
    [
        ("baseline", "record_baseline"),
        ("pinned_baseline", "record_pinned_baseline"),
        ("source_trace", "record_source_trace"),
        ("fit_select", "record_fit_selection"),
        ("smoke", "_run_configured"),
    ],
)
def test_cli_reports_busy_without_starting_work(
    tmp_path, monkeypatch, capsys, module_name, service
):
    module = import_module(f"app.{module_name}")
    # The smoke CLI imports its project root from the runner at invocation time.
    root_owner = import_module("app.tools.runner") if module_name == "smoke" else module
    monkeypatch.setattr(root_owner, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("sys.argv", [module_name])

    def must_not_run(*args, **kwargs):
        pytest.fail("A busy CLI must not start its workflow.")

    monkeypatch.setattr(module, service, must_not_run)
    with ProcessLock(tmp_path / "data/execution.lock"):
        assert module.main() == 1
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err)["error_code"] == "execution_busy"
    assert str(tmp_path) not in output.err
