"""F9 rejects empty/stale/tampered evidence and preserves command failures."""

import os
import subprocess
import sys

import pytest

from scripts.release_validation import (
    PENDING_GATES,
    REQUIRED_STEPS,
    SCHEMA_VERSION,
    artifact_manifest,
    junit_result,
    local_status,
    run_step,
    source_manifest,
    verify_receipt,
    write_json,
)

PASSING = (
    '<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="check"/></testsuite>'
)


@pytest.mark.parametrize(
    "raw",
    [
        '<testsuite tests="0"/>',
        "<testsuites/>",
        '<testsuite tests="2"><testcase name="one"/></testsuite>',
        '<testsuite tests="1" failures="0"><testcase name="one"><failure/></testcase></testsuite>',
        '<testsuite tests="1" skipped="0"><testcase name="one"><skipped/></testcase></testsuite>',
        '<testsuites tests="99"><testsuite tests="1">'
        '<testcase name="one"/></testsuite></testsuites>',
    ],
)
def test_junit_must_have_cases_and_honest_counters(tmp_path, raw):
    path = tmp_path / "junit.xml"
    path.write_text(raw)
    with pytest.raises(ValueError):
        junit_result(path)


def test_nonzero_exit_and_skips_cannot_pass_even_with_junit(tmp_path):
    (tmp_path / "passing.xml").write_text(PASSING)
    record = run_step(
        "probe",
        [sys.executable, "-c", "raise SystemExit(7)"],
        tmp_path,
        tmp_path,
        os.environ.copy(),
        junit="passing.xml",
    )
    assert record["status"] == "failed" and record["exit_code"] == 7
    (tmp_path / "skipped.xml").write_text(
        '<testsuite tests="1" skipped="1"><testcase name="one"><skipped/></testcase></testsuite>'
    )
    record = run_step(
        "probe",
        [sys.executable, "-c", "pass"],
        tmp_path,
        tmp_path,
        os.environ.copy(),
        junit="skipped.xml",
    )
    assert record["status"] == "failed" and record["tests"]["skipped"] == 1


def test_missing_report_and_missing_command_fail_closed(tmp_path):
    missing = run_step(
        "probe",
        [sys.executable, "-c", "pass"],
        tmp_path,
        tmp_path,
        os.environ.copy(),
        junit="missing.xml",
    )
    assert missing["status"] == "failed" and missing["error"] == "missing_or_invalid_junit"
    missing = run_step(
        "probe", [str(tmp_path / "absent-command")], tmp_path, tmp_path, os.environ.copy()
    )
    assert missing["status"] == "failed" and missing["error"] == "command_unavailable"


@pytest.fixture
def receipt(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "--template=", str(root)], check=True, capture_output=True)
    (root / "backend").mkdir()
    (root / "backend/app.py").write_text("# tested bytes\n")
    directory = tmp_path / "receipt"
    directory.mkdir()
    steps = [{"name": name, "status": "passed", "exit_code": 0} for name in REQUIRED_STEPS]
    for name in REQUIRED_STEPS:
        (directory / f"{name}.log").write_text("Completed\n")
    for name in ("backend", "browser"):
        report = f"{name}-junit.xml"
        (directory / report).write_text(PASSING)
        next(step for step in steps if step["name"] == name).update(
            junit=report, tests=junit_result(directory / report)
        )
    (directory / "browser-results.json").write_text("{}")
    (directory / "query-scale.json").write_text("{}")
    (directory / "build").mkdir()
    (directory / "build/index.html").write_text("<!doctype html>")
    data = {
        "schema_version": SCHEMA_VERSION,
        "sources": source_manifest(root),
        "steps": steps,
        "source_unchanged_during_validation": True,
        "local_status": "passed",
        "release_status": {name: "pending" for name in PENDING_GATES},
        "artifacts": artifact_manifest(directory),
    }
    path = directory / "receipt.json"
    write_json(path, data)
    return root, path, data


def test_intact_receipt_rechecks_current_uncommitted_source_bytes(receipt):
    root, path, _ = receipt
    assert verify_receipt(path, root)["integrity"] == "passed"
    (root / "backend/app.py").write_text("# later change\n")
    with pytest.raises(ValueError, match="source"):
        verify_receipt(path, root)


@pytest.mark.parametrize(
    "change",
    ["log", "missing_artifact", "missing_step", "duplicate_step", "release_claim", "skipped_suite"],
)
def test_tampered_or_incomplete_receipts_never_certify_release(receipt, change):
    root, path, data = receipt
    if change == "log":
        (path.parent / "backend.log").write_text("Changed")
    elif change == "missing_artifact":
        del data["artifacts"]["browser-junit.xml"]
    elif change == "missing_step":
        data["steps"].pop()
    elif change == "duplicate_step":
        data["steps"].append(data["steps"][0])
    elif change == "release_claim":
        data["release_status"]["connected_analysis"] = "complete"
    else:
        data["steps"][2]["tests"]["skipped"] = 1
    write_json(path, data)
    with pytest.raises(ValueError):
        verify_receipt(path, root)


def test_source_drift_and_incomplete_steps_fail_local_validation():
    steps = [{"name": name, "status": "passed", "exit_code": 0} for name in REQUIRED_STEPS]
    assert local_status(steps, True) == "passed"
    assert local_status(steps, False) == "failed"
    assert local_status(steps[:-1], True) == "failed"
    steps[0]["status"] = "not_run"
    assert local_status(steps, True) == "failed"


def test_browser_serves_the_captured_build_instead_of_another_dist(tmp_path):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    build = tmp_path / "captured-build"
    (build / "assets").mkdir(parents=True)
    (build / "index.html").write_text("<html>Captured release build</html>")
    with TestClient(
        create_app(
            Settings(
                _env_file=None,
                auth_mode="local",
                model_api_key=None,
                github_private_fetch_enabled=False,
            ),
            data_root=tmp_path / "isolated-data",
            start_worker=False,
            frontend_root=build,
        )
    ) as client:
        assert client.get("/catalogs/123").text == "<html>Captured release build</html>"
        assert client.get("/api/unknown").status_code == 404


def test_validation_server_rejects_an_outside_destination_before_creation(tmp_path):
    from app.tools.runner import PROJECT_ROOT

    destination = tmp_path / "must-not-be-created"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.release_server",
            "--root",
            str(destination),
            "--port",
            "5290",
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 2 and not destination.exists()
