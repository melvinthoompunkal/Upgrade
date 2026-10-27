"""Exercise pinned inputs and capture provenance without executing target code on the host."""

import json
from dataclasses import asdict, replace
from pathlib import Path
from xml.etree import ElementTree

import pytest

from app import pinned_baseline
from app.baseline import EXPECTED_PASS, EXPECTED_RED
from app.contracts import RepoSnapshot
from app.tools.repositories import Registration, RepositoryManager, verify_original
from app.tools.runner_artifacts import TestCapture as Capture
from app.tools.runner_artifacts import TestCase as Case
from app.tools.runner_artifacts import classify_result, file_hashes, parse_junit, read_regular_file
from app.tools.targets import CRAWLER_FILES, CRAWLER_PREFIX

IMAGE_ID = "sha256:" + "a" * 64


@pytest.fixture
def pinned_target(committed_repo, tmp_path):
    root, git = committed_repo
    fixture = root / CRAWLER_PREFIX
    for relative in CRAWLER_FILES:
        destination = fixture / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(
            read_regular_file(pinned_baseline.PROJECT_ROOT / CRAWLER_PREFIX, relative)
        )
    git.run("add", "--", CRAWLER_PREFIX, cwd=root)
    git.run("-c", "commit.gpgSign=false", "commit", "-m", "crawler fixture", cwd=root)
    # Neither this dirty production file nor this unregistered file may enter the export.
    (fixture / "src/crawler.py").write_text("raise AssertionError('dirty code must not execute')\n")
    (fixture / ".env").write_text("unregistered placeholder\n")
    directory = tmp_path / "pinned-run"
    directory.mkdir()
    manager = RepositoryManager(directory / "repositories")
    target, original = manager.local_target(
        Registration("crawler-demo", root, CRAWLER_PREFIX, CRAWLER_FILES)
    )
    receipt = tmp_path / "image.json"
    receipt.write_text("{}")
    return target, original, directory, receipt


def write_capture(suite, image_id, directory, cases=None):
    if cases is None:
        if suite == "acceptance":
            cases = [
                Case(name, "failed", f"AssertionError: {message}", "assert missing_behavior")
                for name, message in EXPECTED_RED.items()
            ] + [Case(name, "passed", "", "") for name in sorted(EXPECTED_PASS)]
        else:
            cases = [
                Case(name, "passed", "", "") for name in sorted(pinned_baseline.GREEN_CASES[suite])
            ]
    directory.mkdir(parents=True)
    (directory / "results").mkdir()
    xml = ElementTree.Element("testsuite")
    for case in cases:
        node = ElementTree.SubElement(xml, "testcase", name=case.name)
        if case.outcome != "passed":
            tag = {"failed": "failure", "error": "error", "skipped": "skipped"}[case.outcome]
            ElementTree.SubElement(node, tag, message=case.message).text = case.detail
    (directory / "results/junit.xml").write_bytes(ElementTree.tostring(xml))
    (directory / "stdout.txt").write_text("mock runner output\n")
    (directory / "stderr.txt").write_text("")
    parsed = parse_junit((directory / "results/junit.xml").read_bytes())
    exit_code = 1 if any(c.outcome in {"failed", "error"} for c in parsed) else 0
    capture = Capture(
        suite,
        classify_result(exit_code, parsed),
        exit_code,
        1,
        ("docker", "mocked-in-tests"),
        image_id,
        parsed,
        file_hashes(directory, pinned_baseline.CAPTURE_FILES),
        "",
    )
    (directory / "capture.json").write_text(json.dumps(asdict(capture)), encoding="utf-8")
    return capture


@pytest.fixture
def mocked_runner(monkeypatch):
    calls = []

    def run_suite(*, suite, image_id, snapshot, acceptance, directory):
        calls.append(suite)
        assert set(file_hashes(snapshot, CRAWLER_FILES)) == set(CRAWLER_FILES)
        assert (acceptance / "test_retries.py").is_file()
        return write_capture(suite, image_id, directory)

    monkeypatch.setattr(pinned_baseline, "prepared_image", lambda _: IMAGE_ID)
    monkeypatch.setattr(pinned_baseline, "run_suite", run_suite)
    return calls


def binding_at(directory):
    return json.loads((directory / "baseline-binding.json").read_text())


def test_dirty_target_is_excluded_and_captures_bind_to_committed_bytes(
    pinned_target, mocked_runner
):
    target, original, directory, receipt = pinned_target
    expected = {f.path: f.sha256 for f in target.inventory.files}
    report, path = pinned_baseline.record_pinned_baseline(*pinned_target)
    binding = binding_at(directory)
    assert report["status"] == report["baseline_status"] == "passed"
    assert report["retry_diagnostic_status"] == "expected_red"
    assert report["original_target_unchanged"] and verify_original(target.git, original)
    assert original["uncommitted_changes_excluded"]
    assert mocked_runner == ["isolation", "baseline", "acceptance"]
    assert binding["target_sha"] == target.snapshot.commit_sha == original["head"]
    assert binding["target_path_prefix"] == CRAWLER_PREFIX
    assert binding["image_id"] == IMAGE_ID and binding["inputs_unchanged"]
    assert (
        binding["exported_files"] == file_hashes(directory / "snapshot", CRAWLER_FILES) == expected
    )
    assert (
        binding["image_receipt_sha256"]
        == file_hashes(receipt.parent, (receipt.name,))[receipt.name]
    )
    assert len(binding["captures"]) == 3
    for capture in binding["captures"]:
        capture_path = directory / capture["artifact_id"]
        assert (
            file_hashes(capture_path.parent, (capture_path.name,))[capture_path.name]
            == (capture["sha256"])
        )
        assert (
            file_hashes(capture_path.parent, pinned_baseline.CAPTURE_FILES)
            == (capture["artifact_hashes"])
        )
    assert (
        report["baseline_binding"]["sha256"]
        == file_hashes(directory, ("baseline-binding.json",))["baseline-binding.json"]
    )
    assert json.loads(path.read_text()) == json.loads(json.dumps(report))
    assert not (directory / "snapshot/.env").exists()
    assert not (directory / "snapshot/.git").exists()


@pytest.mark.parametrize("field", ["sha", "prefix", "inventory"])
def test_wrong_target_identity_is_rejected_before_runner(pinned_target, mocked_runner, field):
    target, original, directory, receipt = pinned_target
    if field == "sha":
        data = target.snapshot.model_dump()
        data["commit_sha"] = "f" * 40
        target = replace(target, snapshot=RepoSnapshot(**data))
    elif field == "prefix":
        target = replace(target, prefix="other-fixture")
    else:
        target = replace(target, objects={})
    report, _ = pinned_baseline.record_pinned_baseline(target, original, directory, receipt)
    assert report["status"] == "failed" and not mocked_runner
    assert report["error_code"] == "invalid_evidence"
    assert not binding_at(directory)["inputs_unchanged"]


@pytest.mark.parametrize("changed", ["snapshot", "acceptance", "extra-file", "original"])
def test_input_changes_stop_before_the_next_suite(
    pinned_target, monkeypatch, mocked_runner, changed
):
    target, original, directory, _ = pinned_target
    run = pinned_baseline.run_suite

    def tamper(**arguments):
        capture = run(**arguments)
        if changed == "extra-file":
            (arguments["snapshot"] / ".env").write_text("must not be mounted")
        elif changed == "original":
            root = original["root"]
            (Path(root) / CRAWLER_PREFIX / "src/crawler.py").write_text("second dirty change")
        else:
            selected = arguments[changed]
            path = "src/crawler.py" if changed == "snapshot" else "test_retries.py"
            (selected / path).write_text("mutated input")
        return capture

    monkeypatch.setattr(pinned_baseline, "run_suite", tamper)
    report, _ = pinned_baseline.record_pinned_baseline(*pinned_target)
    assert report["status"] == "failed" and mocked_runner == ["isolation"]
    assert report["error_code"] == "invalid_evidence"
    assert not binding_at(directory)["inputs_unchanged"]
    assert report["original_target_unchanged"] == (changed != "original")
    if changed != "original":
        assert verify_original(target.git, original)


@pytest.mark.parametrize("changed", ["snapshot", "receipt"])
def test_changes_during_image_preparation_prevent_any_execution(
    pinned_target, monkeypatch, mocked_runner, changed
):
    def prepare(receipt):
        path = receipt if changed == "receipt" else pinned_target[2] / "snapshot/src/crawler.py"
        path.write_text("changed during preparation")
        return IMAGE_ID

    monkeypatch.setattr(pinned_baseline, "prepared_image", prepare)
    report, _ = pinned_baseline.record_pinned_baseline(*pinned_target)
    assert report["status"] == "failed" and not mocked_runner
    assert report["error_code"] == "invalid_evidence"


@pytest.mark.parametrize("changed", ["stdout", "capture", "image", "suite", "junit"])
def test_capture_mismatch_or_missing_evidence_cannot_pass(
    pinned_target, monkeypatch, mocked_runner, changed
):
    run = pinned_baseline.run_suite

    def tamper(**arguments):
        capture = run(**arguments)
        directory = arguments["directory"]
        if changed == "stdout":
            (directory / "stdout.txt").unlink()
        elif changed == "capture":
            data = json.loads((directory / "capture.json").read_text())
            data["elapsed_ms"] += 1
            (directory / "capture.json").write_text(json.dumps(data))
        elif changed == "image":
            capture = replace(capture, image_id="sha256:" + "b" * 64)
        elif changed == "suite":
            capture = replace(capture, suite="baseline")
        else:
            (directory / "results/junit.xml").write_text("<testsuite/>")
        return capture

    monkeypatch.setattr(pinned_baseline, "run_suite", tamper)
    report, _ = pinned_baseline.record_pinned_baseline(*pinned_target)
    assert report["status"] == "failed" and mocked_runner == ["isolation"]
    assert len(report["captures"]) == 1
    assert not binding_at(pinned_target[2])["inputs_unchanged"]


def test_tampering_with_an_earlier_capture_is_detected_at_publication(
    pinned_target, monkeypatch, mocked_runner
):
    run = pinned_baseline.run_suite

    def tamper(**arguments):
        capture = run(**arguments)
        if arguments["suite"] == "acceptance":
            (pinned_target[2] / "test-captures/isolation/stdout.txt").write_text("altered")
        return capture

    monkeypatch.setattr(pinned_baseline, "run_suite", tamper)
    report, _ = pinned_baseline.record_pinned_baseline(*pinned_target)
    assert report["status"] == "failed"
    assert not binding_at(pinned_target[2])["inputs_unchanged"]


@pytest.mark.parametrize("failure", ["wrong-cases", "skipped", "assertion"])
def test_only_registered_green_baseline_cases_pass(
    pinned_target, monkeypatch, mocked_runner, failure
):
    run = pinned_baseline.run_suite

    def fail(**arguments):
        if arguments["suite"] != "baseline":
            return run(**arguments)
        mocked_runner.append("baseline")
        outcome = {"wrong-cases": "passed", "skipped": "skipped", "assertion": "failed"}[failure]
        return write_capture(
            "baseline",
            arguments["image_id"],
            arguments["directory"],
            [Case("different_test", outcome, "AssertionError: baseline failed", "")],
        )

    monkeypatch.setattr(pinned_baseline, "run_suite", fail)
    report, _ = pinned_baseline.record_pinned_baseline(*pinned_target)
    assert report["status"] == report["baseline_status"] == "failed"
    assert mocked_runner == ["isolation", "baseline"]


def test_already_green_retry_is_reported_without_manufacturing_red(
    pinned_target, monkeypatch, mocked_runner
):
    run = pinned_baseline.run_suite

    def already_present(**arguments):
        if arguments["suite"] != "acceptance":
            return run(**arguments)
        return write_capture(
            "acceptance",
            arguments["image_id"],
            arguments["directory"],
            [Case(name, "passed", "", "") for name in sorted(set(EXPECTED_RED) | EXPECTED_PASS)],
        )

    monkeypatch.setattr(pinned_baseline, "run_suite", already_present)
    report, _ = pinned_baseline.record_pinned_baseline(*pinned_target)
    assert report["status"] == report["baseline_status"] == "passed"
    assert report["retry_diagnostic_status"] == "already_present"


def test_unrelated_retry_failures_do_not_establish_the_expected_gap(
    pinned_target, monkeypatch, mocked_runner
):
    run = pinned_baseline.run_suite

    def unrelated(**arguments):
        if arguments["suite"] != "acceptance":
            return run(**arguments)
        cases = [
            Case(name, "failed", "AssertionError: unrelated behavior", "") for name in EXPECTED_RED
        ] + [Case(name, "passed", "", "") for name in sorted(EXPECTED_PASS)]
        return write_capture("acceptance", arguments["image_id"], arguments["directory"], cases)

    monkeypatch.setattr(pinned_baseline, "run_suite", unrelated)
    report, _ = pinned_baseline.record_pinned_baseline(*pinned_target)
    assert report["status"] == "failed" and report["baseline_status"] == "passed"
    assert report["retry_diagnostic_status"] == "invalid"


def test_missing_image_retains_sanitized_pinned_failure_receipt(
    pinned_target, monkeypatch, mocked_runner
):
    def unavailable(_):
        raise OSError("sensitive details must not be copied")

    monkeypatch.setattr(pinned_baseline, "prepared_image", unavailable)
    report, path = pinned_baseline.record_pinned_baseline(*pinned_target)
    assert report["status"] == "failed" and not mocked_runner
    assert "sensitive" not in path.read_text()
    assert report["target"]["commit_sha"] == pinned_target[0].snapshot.commit_sha


def test_reusing_a_run_does_not_overwrite_its_receipts(pinned_target, mocked_runner):
    report, path = pinned_baseline.record_pinned_baseline(*pinned_target)
    before = path.read_bytes()
    assert report["status"] == "passed"
    with pytest.raises(pinned_baseline.RunnerError, match="fresh directory"):
        pinned_baseline.record_pinned_baseline(*pinned_target)
    assert path.read_bytes() == before
