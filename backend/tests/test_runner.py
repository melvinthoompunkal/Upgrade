import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.baseline import EXPECTED_PASS, EXPECTED_RED, main, valid_feature_red
from app.tools import runner
from app.tools.runner_artifacts import (
    MAX_ARTIFACT_BYTES,
    RunnerError,
    classify_result,
    export_files,
    parse_junit,
    read_regular_file,
)
from app.tools.runner_artifacts import TestCapture as Capture
from app.tools.runner_artifacts import TestCase as Case


def red_capture() -> Capture:
    cases = tuple(
        Case(name, "failed", f"AssertionError: {message}", "")
        for name, message in EXPECTED_RED.items()
    )
    cases += tuple(Case(name, "passed", "", "") for name in sorted(EXPECTED_PASS))
    return Capture("acceptance", "failed", 1, 1, (), "sha256:" + "a" * 64, cases, {}, "")


def test_valid_red_requires_the_frozen_assertion_failures_and_control_cases() -> None:
    capture = red_capture()
    assert valid_feature_red(capture)
    assert not valid_feature_red(replace(capture, status="error"))
    assert not valid_feature_red(replace(capture, exit_code=2))
    assert not valid_feature_red(replace(capture, cases=capture.cases[:-1]))
    for outcome, message in [
        ("error", "ImportError"),
        ("skipped", "xfail"),
        ("failed", "RuntimeError: unavailable"),
        ("failed", "AssertionError: unrelated"),
    ]:
        changed = (replace(capture.cases[0], outcome=outcome, message=message), *capture.cases[1:])
        assert not valid_feature_red(replace(capture, cases=changed))


@pytest.mark.parametrize(
    "outcome,exit_code,expected",
    [
        ("passed", 0, "passed"),
        ("failed", 1, "failed"),
        ("failed", 0, "error"),
        ("passed", 1, "error"),
        ("skipped", 0, "error"),
        ("error", 1, "error"),
    ],
)
def test_exit_and_junit_must_agree(outcome: str, exit_code: int, expected: str) -> None:
    cases = (Case("test_case", outcome, "", ""),)
    assert classify_result(exit_code, cases) == expected


def test_junit_preserves_assertion_evidence_and_rejects_invalid_reports() -> None:
    cases = parse_junit(
        b'<testsuites><testsuite><testcase name="recovery">'
        b'<failure message="AssertionError: missing">assert page</failure>'
        b'</testcase><testcase name="control"/></testsuite></testsuites>'
    )
    assert cases[0].outcome == "failed"
    assert cases[0].detail == "assert page"
    assert cases[1].outcome == "passed"
    for content in [
        b"",
        b"<testsuites/>",
        b"<anything/>",
        b"<!DOCTYPE x><testsuite/>",
        b'<testsuite><testcase name="x"/><testcase name="x"/></testsuite>',
        b"x" * (MAX_ARTIFACT_BYTES + 1),
    ]:
        with pytest.raises(RunnerError):
            parse_junit(content)


def test_export_copies_only_registered_regular_files(tmp_path: Path) -> None:
    source = tmp_path / "target"
    source.mkdir()
    (source / "crawler.py").write_text("# source")
    (source / ".env").write_text("credential placeholder")
    (source / ".git").mkdir()
    destination = tmp_path / "export"
    hashes = export_files(source, ("crawler.py",), destination)
    assert set(hashes) == {"crawler.py"}
    assert sorted(p.name for p in destination.iterdir()) == ["crawler.py"]
    assert (source / ".env").read_text() == "credential placeholder"
    with pytest.raises(ValueError):
        read_regular_file(source, "../target/.env")
    (source / "oversized").write_bytes(b"x" * (MAX_ARTIFACT_BYTES + 1))
    with pytest.raises(RunnerError, match="size limit"):
        read_regular_file(source, "oversized")


def test_export_rejects_junctions_before_reading_outside_root(tmp_path: Path) -> None:
    import os
    import subprocess

    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("placeholder")
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(source / "link"), str(outside)],
            check=True,
            capture_output=True,
        )
    else:
        (source / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(RunnerError, match="links or junctions"):
        read_regular_file(source, "link/secret")


def test_registered_command_enforces_execution_boundary(tmp_path: Path) -> None:
    command = runner.container_command(
        name="graft-test-unit",
        suite="baseline",
        image_id="sha256:x",
        snapshot=tmp_path,
        acceptance=tmp_path,
        results=tmp_path,
    )
    for flag in [
        "--pull=never",
        "--network=none",
        "--read-only",
        "--user=65532:65532",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges=true",
        "--cpus=2",
        "--memory=1g",
        "--pids-limit=64",
        "--noconftest",
        "--timeout=10",
    ]:
        assert flag in command
    mounts = [arg for arg in command if arg.startswith("type=bind,")]
    assert len(mounts) == 3
    assert sum(m.endswith(",readonly") for m in mounts) == 2
    assert "--env-file" not in command
    with pytest.raises(RunnerError, match="Unknown"):
        runner.container_command(
            name="x",
            suite="arbitrary",
            image_id="x",
            snapshot=tmp_path,
            acceptance=tmp_path,
            results=tmp_path,
        )


def test_stale_or_invalid_image_receipt_fails_before_docker(tmp_path: Path, monkeypatch) -> None:
    def unexpected_docker(*args):
        raise AssertionError("invalid receipts must never reach Docker")

    monkeypatch.setattr(runner, "docker", unexpected_docker)
    receipt = tmp_path / "image.json"
    with pytest.raises(RunnerError, match="Build the runner"):
        runner.prepared_image(receipt)
    receipt.write_text(json.dumps({"image_id": "latest", "input_hashes": {}}))
    with pytest.raises(RunnerError, match="Build the runner"):
        runner.prepared_image(receipt)
    receipt.write_text(json.dumps({"image_id": "sha256:" + "a" * 64, "input_hashes": {}}))
    with pytest.raises(RunnerError, match="inputs changed"):
        runner.prepared_image(receipt)


def test_cli_fails_without_prepared_image_and_never_runs_host_tests(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["baseline", "--image-receipt", str(tmp_path / "absent")])
    assert main() == 1
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err)["error_code"] == "runner_failed"
