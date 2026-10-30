"""F7 controller invariants, with real pinned Git inputs and controlled provider/runner I/O."""

import json
import os

import pytest
from adaptation_fixtures import (
    AdaptationProvider,
    Captures,
    context,
    execute,
)
from fastapi import HTTPException
from test_projects import independent as independent

from app.adaptation_acceptance import acceptance_state
from app.adaptation_contracts import AcceptanceSpec, AdaptationOptions
from app.adaptation_inputs import adaptation_inputs
from app.adaptation_policy import sha256
from app.api.artifacts import RunArtifacts, digest, summary_from_report
from app.models.schema import response_format


@pytest.fixture
def inputs(independent, tmp_path, monkeypatch):
    return context(independent, tmp_path, monkeypatch)


def test_verified_binding_export_and_preservation(inputs, tmp_path, monkeypatch):
    projects, request, source, target, root, git, target_root = inputs
    before = git.run("status", "--porcelain", cwd=root)
    target_before = git.run("status", "--porcelain", cwd=target_root)
    report, path, provider, captures = execute(inputs, tmp_path, monkeypatch)
    assert report["status"] == "verified", report
    result = report["adaptation"]
    assert result["verdict"] == "verified" and result["repair_count"] == 0
    assert result["changed_files"] == 1 and result["new_files"] == 0
    assert result["protected_tests_unchanged"] and report["original_target_unchanged"]
    patch = path.parent / result["patch_artifact"]
    assert sha256(patch.read_bytes()) == result["patch_sha256"]
    assert "Permission is hereby granted" in patch.read_text()
    assert source.snapshot.commit_sha != target.snapshot.commit_sha
    assert source.snapshot.origin != target.snapshot.origin
    assert git.run("status", "--porcelain", cwd=root) == before
    assert git.run("status", "--porcelain", cwd=target_root) == target_before
    assert provider.implementations == 1 and report["usage"]["requests"] == 4
    assert len(captures.calls) == 5
    frozen = json.loads((path.parent / "frozen-binding.json").read_text())
    assert frozen["contract_sha256"] == report["artifact_hashes"]["behavior-contract.json"]
    summary = summary_from_report("adaptation", "verified_adaptation", report)
    api = RunArtifacts(
        path.parent.parent,
        {
            "summary": summary.model_dump_json(),
            "report": f"{path.parent.name}/report.json",
            "digest": digest(path.read_bytes()),
            "request": request.model_dump_json(),
        },
    )
    detail = api.detail()
    assert detail.adaptation.verdict == "verified" and len(detail.tests) == 5
    exported = next(a for a in detail.artifacts if a.label == result["patch_artifact"])
    assert exported.available and exported.sha256 == result["patch_sha256"]
    assert api.published(result["patch_artifact"]) == patch.read_bytes()
    assert api.references()


@pytest.mark.parametrize("mode", ["green", "exception", "skip"])
def test_initial_acceptance_never_confuses_green_exception_or_skip_with_red(
    inputs, tmp_path, monkeypatch, mode
):
    report, _, provider, _ = execute(inputs, tmp_path, monkeypatch, captures=Captures(red=mode))
    assert report["status"] == ("already_present" if mode == "green" else "failed")
    assert provider.implementations == 0
    assert report["adaptation"]["verdict"] != "verified"


def test_ordinary_baseline_failure_blocks_implementation(inputs, tmp_path, monkeypatch):
    report, _, provider, captures = execute(
        inputs, tmp_path, monkeypatch, captures=Captures(baseline_failed=True)
    )
    assert report["status"] == "baseline_failed" and provider.implementations == 0
    assert len(captures.calls) == 2


@pytest.mark.parametrize("bad_rounds,expected", [(1, "verified"), (4, "exhausted")])
def test_repair_allowance_and_cumulative_binding(
    inputs, tmp_path, monkeypatch, bad_rounds, expected
):
    # Bad proposals still change production; acceptance remains red until a later repair.
    provider = AdaptationProvider(bad_rounds=bad_rounds)
    report, path, provider, _ = execute(
        inputs, tmp_path, monkeypatch, provider=provider, captures=Captures(bad_rounds=bad_rounds)
    )
    assert report["status"] == expected, report
    assert provider.implementations <= 4
    assert report["adaptation"]["repair_count"] == (1 if expected == "verified" else 3)
    assert len(report["adaptation"]["rounds"]) == provider.implementations
    assert (path.parent / "acceptance-spec.json").is_file()
    assert report["usage"]["requests"] == 3 + provider.implementations


@pytest.mark.parametrize(
    "content",
    [
        "import unprepared_package\ndef chunks(items):\n    return []\n",
        "def chunks(items):\n    return []\n" + "# extra\n" * 401,
        "import importlib\ndef chunks(items):\n    return importlib.import_module('foreign')\n",
    ],
)
def test_actual_dependency_scope_and_dynamic_import_rejections(
    inputs, tmp_path, monkeypatch, content
):
    report, _, provider, _ = execute(
        inputs, tmp_path, monkeypatch, provider=AdaptationProvider(content=content)
    )
    assert report["status"] == "exhausted"
    assert report["adaptation"]["verdict"] == "unverified" and provider.implementations == 4
    assert all(r["status"] == "rejected" for r in report["adaptation"]["rounds"])


def test_bad_preimage_never_runs_patch(inputs, tmp_path, monkeypatch):
    report, _, _, captures = execute(
        inputs, tmp_path, monkeypatch, provider=AdaptationProvider(corrupt_preimage=True)
    )
    assert report["status"] == "exhausted" and len(captures.calls) == 3


@pytest.mark.parametrize("target", ["acceptance", "snapshot", "capture", "junit"])
def test_tampering_retains_failure_receipt(inputs, tmp_path, monkeypatch, target):
    def tamper(snapshot, acceptance, directory):
        file = {
            "acceptance": acceptance / "test_feature.py",
            "snapshot": snapshot / "src/sequence.py",
            "capture": directory / "capture.json",
            "junit": directory / "results/junit.xml",
        }[target]
        file.write_bytes(b"tampered")

    report, path, _, _ = execute(inputs, tmp_path, monkeypatch, captures=Captures(tamper=tamper))
    assert report["status"] == "failed" and report["adaptation"]["verdict"] == "unverified"
    assert path.is_file() and report["captures"]
    assert report["adaptation"]["patch_artifact"] in report["artifact_hashes"]


def test_foreign_selection_is_unknown_and_no_model_call(inputs):
    projects, request, *_ = inputs
    with pytest.raises(HTTPException) as caught:
        adaptation_inputs(request, projects, "foreign-owner", "muse-spark-1.3")
    assert caught.value.status_code == 404


def test_current_adapter_change_is_rejected_before_inference(inputs, tmp_path, monkeypatch):
    projects, _, _, target, *_ = inputs
    original_open = projects.open

    def changed_adapter(owner, project_id):
        pin, repo = original_open(owner, project_id)
        if project_id == target.id:
            pin = pin.model_copy(
                update={
                    "support": pin.support.model_copy(
                        update={"adapter_version": "unreviewed-next-version"}
                    )
                }
            )
        return pin, repo

    monkeypatch.setattr(projects, "open", changed_adapter)
    report, _, provider, _ = execute(inputs, tmp_path, monkeypatch)
    assert report["status"] == "failed" and report["error_code"] == "invalid_evidence"
    assert provider.calls == []


def test_acceptance_contract_schema_and_repair_cap():
    from app.adaptation_contracts import AdaptationPlan, PatchProposal

    for contract in (AcceptanceSpec, AdaptationPlan, PatchProposal):
        assert response_format(contract)["type"] == "json_schema"
    with pytest.raises(ValueError):
        AdaptationOptions(max_repairs=4)


def test_api_admission_idempotency_and_stale_context(inputs, tmp_path):
    from fastapi.testclient import TestClient
    from test_meta_client import settings

    from app.main import create_app

    _, request, *_ = inputs
    with TestClient(create_app(settings(), data_root=tmp_path, start_worker=False)) as client:
        client.app.state.worker.available = True
        headers = {"Origin": "http://127.0.0.1:5173", "Idempotency-Key": "f7-adaptation-submission"}
        first = client.post("/api/runs", json=request.model_dump(mode="json"), headers=headers)
        assert first.status_code == 202, first.text
        again = client.post("/api/runs", json=request.model_dump(mode="json"), headers=headers)
        assert again.json()["id"] == first.json()["id"]
        changed = request.model_copy(update={"options": AdaptationOptions(max_repairs=0)})
        assert (
            client.post(
                "/api/runs", json=changed.model_dump(mode="json"), headers=headers
            ).status_code
            == 409
        )
        foreign = request.model_copy(update={"assessment_id": "0" * 32})
        assert (
            client.post(
                "/api/runs", json=foreign.model_dump(mode="json"), headers=headers
            ).status_code
            == 404
        )
        client.app.state.worker.available = False


@pytest.mark.skipif(
    os.environ.get("GRAFT_F7_DOCKER") != "1", reason="Explicit offline Docker receipt"
)
def test_independent_docker_red_to_green(inputs, tmp_path, monkeypatch):
    report, path, _, _ = execute(inputs, tmp_path, monkeypatch, docker=True)
    assert report["status"] == "verified", report
    spec = AcceptanceSpec.model_validate_json((path.parent / "acceptance-spec.json").read_bytes())
    from app.capture_metadata import decode_capture

    red_path = "test-captures/before/project_acceptance/capture.json"
    red = decode_capture((path.parent / red_path).read_bytes(), report["artifact_hashes"][red_path])
    assert acceptance_state(red, spec) == "red"
