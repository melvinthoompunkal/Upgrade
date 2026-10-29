"""Record ordinary-green and feature-red evidence for the declared crawler fixture."""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.errors import GraftError
from app.report_publication import publish_json_report
from app.selection_artifacts import ArtifactStore, prepare_directory
from app.tools.runner import PROJECT_ROOT, SANDBOX, prepared_image, run_suite
from app.tools.runner_artifacts import TestCapture, export_files, file_hashes
from app.tools.targets import CRAWLER_FILES as TARGET_FILES

ACCEPTANCE_FILES = ("test_retries.py",)
EXPECTED_RED = {
    "test_transient_503_recovers[one-503]": "retry recovery missing:",
    "test_transient_503_recovers[two-503s]": "retry recovery missing:",
    "test_persistent_503_stops_after_three_attempts": "retry exhaustion must",
    "test_recovered_body_is_parsed_for_links": "retry recovery must precede",
}
EXPECTED_PASS = {
    "test_success_is_attempted_once",
    "test_permanent_404_is_attempted_once",
    "test_successful_traversal_is_unchanged",
}


def valid_feature_red(capture: TestCapture) -> bool:
    """Accept only this known behavioral gap, with no collection/error/skip substitutes."""
    if capture.status != "failed" or capture.exit_code != 1:
        return False
    failures = {case.name: case for case in capture.cases if case.outcome == "failed"}
    passed = {case.name for case in capture.cases if case.outcome == "passed"}
    return (
        failures.keys() == EXPECTED_RED.keys()
        and passed == EXPECTED_PASS
        and len(capture.cases) == len(EXPECTED_RED) + len(EXPECTED_PASS)
        and all(
            case.message.startswith(f"AssertionError: {EXPECTED_RED[name]}")
            for name, case in failures.items()
        )
    )


def record_baseline(image_receipt: Path, *, artifact_root: Path | None = None) -> tuple[dict, Path]:
    target = PROJECT_ROOT / "examples" / "crawler-target"
    run_dir = (artifact_root or PROJECT_ROOT / "data/artifacts") / f"crawler-baseline-{uuid4().hex}"
    prepare_directory(run_dir)
    image_id = prepared_image(image_receipt)
    snapshot = run_dir / "snapshot"
    acceptance = run_dir / "acceptance"
    target_hashes = export_files(target, TARGET_FILES, snapshot)
    acceptance_hashes = export_files(SANDBOX / "acceptance", ACCEPTANCE_FILES, acceptance)
    captures: list[TestCapture] = []
    for suite in ("isolation", "baseline", "acceptance"):
        capture = run_suite(
            suite=suite,
            image_id=image_id,
            snapshot=snapshot,
            acceptance=acceptance,
            directory=run_dir / "test-captures" / suite,
        )
        captures.append(capture)
        if suite != "acceptance" and capture.status != "passed":
            break
    unchanged = (
        file_hashes(target, TARGET_FILES) == file_hashes(snapshot, TARGET_FILES) == target_hashes
        and file_hashes(SANDBOX / "acceptance", ACCEPTANCE_FILES)
        == file_hashes(acceptance, ACCEPTANCE_FILES)
        == acceptance_hashes
    )
    passed = (
        len(captures) == 3
        and unchanged
        and captures[0].status == captures[1].status == "passed"
        and valid_feature_red(captures[2])
    )
    report = {
        "status": "passed" if passed else "failed",
        "check": "crawler_baseline_and_missing_retry_behavior",
        "created_at": datetime.now(UTC).isoformat(),
        "image_id": image_id,
        "target_id": "crawler-demo",
        "target_hashes": target_hashes,
        "acceptance_hashes": acceptance_hashes,
        "runner_definition_hashes": file_hashes(
            PROJECT_ROOT,
            (
                "backend/app/baseline.py",
                "backend/app/tools/runner.py",
                "backend/app/tools/runner_artifacts.py",
            ),
        ),
        "inputs_unchanged": unchanged,
        "captures": [asdict(capture) for capture in captures],
        "scope": "Working-file baseline only; retry diagnostics are not adaptation verification.",
    }
    report_path = run_dir / "report.json"
    publish_json_report(ArtifactStore(run_dir, {}), report)
    return report, report_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image-receipt", type=Path, default=PROJECT_ROOT / "data" / "runner-image.json"
    )
    args = parser.parse_args()
    try:
        from app.execution import cli_execution

        with cli_execution(PROJECT_ROOT):
            report, path = record_baseline(args.image_receipt)
    except (GraftError, OSError, ValueError) as error:
        detail = str(error) if isinstance(error, GraftError) else "Unable to prepare runner files."
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_code": error.code if isinstance(error, GraftError) else "runner_failed",
                    "detail": detail,
                }
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(path),
                "suites": [
                    {
                        "suite": c["suite"],
                        "status": c["status"],
                        "exit_code": c["exit_code"],
                        "tests": len(c["cases"]),
                    }
                    for c in report["captures"]
                ],
            },
            indent=2,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
