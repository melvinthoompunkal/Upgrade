"""Bind offline crawler captures to the exact committed target used for analysis."""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.baseline import ACCEPTANCE_FILES, EXPECTED_PASS, EXPECTED_RED, valid_feature_red
from app.errors import EvidenceError, GraftError
from app.report_publication import publish_json_report
from app.selection_artifacts import ArtifactStore, prepare_directory
from app.tools.repositories import (
    PinnedRepository,
    RepositoryManager,
    registered_target,
    verify_original,
)
from app.tools.runner import IMAGE_INPUTS, PROJECT_ROOT, SANDBOX, prepared_image, run_suite
from app.tools.runner_artifacts import (
    RunnerError,
    classify_result,
    export_files,
    file_hashes,
    parse_junit,
    physical_root,
    read_regular_file,
)
from app.tools.runner_artifacts import TestCapture as Capture
from app.tools.targets import CRAWLER_FILES, CRAWLER_PREFIX

RUNNER_ID = "crawler-demo-pytest-v1"
CAPTURE_FILES = ("stdout.txt", "stderr.txt", "results/junit.xml")
CONTROLLER_FILES = (
    "backend/app/pinned_baseline.py",
    "backend/app/baseline.py",
    "backend/app/contracts.py",
    "backend/app/evidence_contracts.py",
    "backend/app/tools/git.py",
    "backend/app/tools/repositories.py",
    "backend/app/tools/runner.py",
    "backend/app/tools/runner_artifacts.py",
    "backend/app/tools/targets.py",
    *(f"sandbox/{path}" for path in IMAGE_INPUTS),
)
# Reviewed case identities for this registration, not arbitrary pytest suites.
GREEN_CASES = {
    "isolation": {
        "test_identity_capabilities_and_privilege_escalation",
        "test_only_loopback_network_exists",
        "test_host_credentials_git_and_socket_are_absent",
        *(
            f"test_inputs_and_root_filesystem_are_read_only[{path}]"
            for path in (
                "/opt/runner/write-probe",
                "/snapshot/write-probe",
                "/acceptance/write-probe",
            )
        ),
    },
    "baseline": {
        "test_fetch_returns_body_and_resolved_unique_links",
        "test_fetch_preserves_httpx_status_errors",
        "test_fetch_preserves_transport_errors",
        "test_crawl_is_breadth_first_same_origin_and_visits_each_url_once",
        "test_crawl_obeys_page_limit",
        "test_crawl_rejects_nonpositive_limits[0]",
        "test_crawl_rejects_nonpositive_limits[-1]",
        "test_crawl_rejects_non_http_roots",
        *(
            f"test_equivalent_origins_share_canonical_visit_identity[{url}]"
            for url in (
                "https://example.test/",
                "https://EXAMPLE.test/",
                "https://example.test:443/",
                "http://EXAMPLE.test:80/",
                "https://example.test:8443/",
            )
        ),
    },
}


def bind_capture(capture: Capture, suite: str, image_id: str, directory: Path) -> dict:
    """Bind only complete, self-consistent captures from the requested suite/image."""
    if capture.suite != suite or capture.image_id != image_id:
        raise EvidenceError("Capture suite or image does not match the pinned baseline run.")
    recorded = json.loads(read_regular_file(directory, "capture.json"))
    if recorded != json.loads(json.dumps(asdict(capture))):
        raise EvidenceError("Recorded capture differs from the runner result.")
    hashes = file_hashes(directory, CAPTURE_FILES)
    if hashes != capture.artifact_hashes:
        raise EvidenceError("Capture artifacts do not match their recorded hashes.")
    cases = parse_junit(read_regular_file(directory, "results/junit.xml"))
    if cases != capture.cases or classify_result(capture.exit_code, cases) != capture.status:
        raise EvidenceError("Capture status/cases do not agree with its JUnit evidence.")
    return {
        "suite": suite,
        "artifact_id": f"test-captures/{suite}/capture.json",
        "sha256": file_hashes(directory, ("capture.json",))["capture.json"],
        "artifact_hashes": hashes,
    }


def verify_export(root: Path, expected: dict[str, str]) -> bool:
    root = physical_root(root)
    allowed = set(expected)
    for relative in expected:
        allowed.update(
            parent.as_posix() for parent in Path(relative).parents if parent != Path(".")
        )
    for path in root.rglob("*"):
        if (
            path.is_symlink()
            or path.is_junction()
            or path.relative_to(root).as_posix() not in allowed
        ):
            return False
    return file_hashes(root, tuple(expected)) == expected


def record_pinned_baseline(
    target: PinnedRepository, original: dict, directory: Path, image_receipt: Path
) -> tuple[dict, Path]:
    """Use a controller-owned pinned target and retain failure evidence in its run directory."""
    directory = physical_root(directory)
    store = ArtifactStore(directory, {})
    if any(
        (directory / name).exists()
        for name in (
            "snapshot",
            "acceptance",
            "test-captures",
            "baseline-binding.json",
            "report.json",
        )
    ):
        raise RunnerError("Pinned baseline requires a fresh directory without previous outputs.")
    snapshot, acceptance = directory / "snapshot", directory / "acceptance"
    captures: list[Capture] = []
    binding = {
        "schema_version": "1",
        "run_id": directory.name,
        "target_repo_id": target.snapshot.repo_id,
        "target_sha": target.snapshot.commit_sha,
        "target_path_prefix": target.prefix,
        "exported_files": {},
        "acceptance_files": {},
        "runner_id": RUNNER_ID,
        "image_id": None,
        "image_receipt_sha256": None,
        "runner_definition_hashes": {},
        "captures": [],
        "inputs_unchanged": False,
    }
    report = {
        "status": "failed",
        "check": "pinned_target_baseline",
        "created_at": datetime.now(UTC).isoformat(),
        "target": target.snapshot.model_dump(mode="json"),
        "original_target": original,
        "baseline_status": "not_run",
        "retry_diagnostic_status": "not_run",
        "original_target_unchanged": False,
        "captures": [],
        "scope": "Stage 4 W1 only: pinned baseline binding; no selection or target edits.",
    }

    def check_inputs() -> None:
        if (
            not verify_export(snapshot, binding["exported_files"])
            or not verify_export(acceptance, binding["acceptance_files"])
            or file_hashes(SANDBOX / "acceptance", ACCEPTANCE_FILES) != binding["acceptance_files"]
            or file_hashes(PROJECT_ROOT, CONTROLLER_FILES) != binding["runner_definition_hashes"]
            or file_hashes(image_receipt.parent, (image_receipt.name,))[image_receipt.name]
            != binding["image_receipt_sha256"]
        ):
            raise EvidenceError("Pinned baseline inputs or controller definitions changed.")
        if not verify_original(target.git, original):
            raise EvidenceError("Original target changed during pinned baseline capture.")

    try:
        if (
            target.snapshot.repo_id != "crawler-demo"
            or target.snapshot.origin != "registered:crawler-demo"
            or target.prefix != CRAWLER_PREFIX
            or target.inventory.repo_id != target.snapshot.repo_id
            or {f.path for f in target.inventory.files} != set(CRAWLER_FILES)
            or set(target.objects) != set(CRAWLER_FILES)
            or original["head"] != target.snapshot.commit_sha
            or original["prefix"] != target.prefix
        ):
            raise EvidenceError("Pinned target does not match the reviewed crawler registration.")
        target.verify()
        if not verify_original(target.git, original):
            raise EvidenceError("Original target changed since it was pinned.")
        binding["exported_files"] = target.export(CRAWLER_FILES, snapshot)
        expected = {entry.path: entry.sha256 for entry in target.inventory.files}
        if binding["exported_files"] != expected:
            raise EvidenceError("Exported target does not match the pinned inventory.")
        binding["acceptance_files"] = export_files(
            SANDBOX / "acceptance", ACCEPTANCE_FILES, acceptance
        )
        binding["runner_definition_hashes"] = file_hashes(PROJECT_ROOT, CONTROLLER_FILES)
        binding["image_receipt_sha256"] = file_hashes(image_receipt.parent, (image_receipt.name,))[
            image_receipt.name
        ]
        image_id = prepared_image(image_receipt)
        binding["image_id"] = image_id
        for suite in ("isolation", "baseline", "acceptance"):
            check_inputs()
            capture_dir = directory / "test-captures" / suite
            if suite == "baseline":
                report["baseline_status"] = "failed"
            capture = run_suite(
                suite=suite,
                image_id=image_id,
                snapshot=snapshot,
                acceptance=acceptance,
                directory=capture_dir,
            )
            captures.append(capture)
            binding["captures"].append(bind_capture(capture, suite, image_id, capture_dir))
            check_inputs()
            if suite != "acceptance":
                if (
                    capture.status != "passed"
                    or {c.name for c in capture.cases} != GREEN_CASES[suite]
                ):
                    raise RunnerError(f"Pinned {suite} suite did not pass the registered cases.")
                if suite == "baseline":
                    report["baseline_status"] = "passed"
            elif valid_feature_red(capture):
                report["retry_diagnostic_status"] = "expected_red"
            elif capture.status == "passed" and {c.name for c in capture.cases} == (
                set(EXPECTED_RED) | EXPECTED_PASS
            ):
                report["retry_diagnostic_status"] = "already_present"
            else:
                report["retry_diagnostic_status"] = "invalid"
                raise RunnerError("Retry diagnostic did not match the frozen behavior cases.")
        binding["inputs_unchanged"] = True
        report["status"] = "passed"
    except (GraftError, OSError, ValueError, KeyError) as error:
        report["error_code"] = error.code if isinstance(error, GraftError) else "runner_failed"
        report["detail"] = (
            str(error)
            if isinstance(error, GraftError)
            else "Unable to bind pinned baseline evidence."
        )
    finally:
        try:
            target.verify()
            report["original_target_unchanged"] = verify_original(target.git, original)
            if not report["original_target_unchanged"]:
                raise EvidenceError("Original target changed during pinned baseline capture.")
            if binding["image_id"] is not None:
                check_inputs()
                # Recheck completed captures before publishing their bindings.
                for capture, capture_binding in zip(captures, binding["captures"], strict=True):
                    capture_dir = directory / "test-captures" / capture.suite
                    if bind_capture(capture, capture.suite, binding["image_id"], capture_dir) != (
                        capture_binding
                    ):
                        raise EvidenceError("Bound capture artifacts changed before publication.")
        except (GraftError, OSError, ValueError, KeyError) as error:
            report["status"] = "failed"
            binding["inputs_unchanged"] = False
            report.setdefault(
                "error_code", error.code if isinstance(error, GraftError) else "runner_failed"
            )
            report.setdefault(
                "detail", "Pinned baseline preservation or final evidence check failed."
            )
        report["captures"] = [asdict(capture) for capture in captures]
        try:
            published = store.json("baseline-binding.json", binding)
            report["baseline_binding"] = published.model_dump(mode="json")
        except (GraftError, OSError, ValueError):
            report["status"] = "failed"
            report.setdefault("error_code", "publication_failed")
            report.setdefault("detail", "The pinned baseline binding could not be saved.")
        report_path = directory / "report.json"
        publish_json_report(store, report, ("baseline-binding.json",))
    return report, report_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image-receipt", type=Path, default=PROJECT_ROOT / "data" / "runner-image.json"
    )
    args = parser.parse_args()
    try:
        from app.execution import ProcessLock

        with ProcessLock(PROJECT_ROOT / "data/execution.lock"):
            directory = PROJECT_ROOT / "data/artifacts" / f"pinned-baseline-{uuid4().hex}"
            prepare_directory(directory)
            manager = RepositoryManager(directory / "repositories")
            target, original = manager.local_target(registered_target(PROJECT_ROOT, "crawler-demo"))
            report, path = record_pinned_baseline(target, original, directory, args.image_receipt)
    except (GraftError, OSError, ValueError) as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_code": error.code if isinstance(error, GraftError) else "runner_failed",
                    "detail": (
                        str(error)
                        if isinstance(error, GraftError)
                        else "Unable to prepare pinned target."
                    ),
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
                "target_sha": report["target"]["commit_sha"],
                "baseline_status": report["baseline_status"],
                "retry_diagnostic_status": report["retry_diagnostic_status"],
            },
            indent=2,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
