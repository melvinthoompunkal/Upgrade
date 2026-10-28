"""Recheck this pass's retained receipts without inference or target execution."""

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.tools.runner_artifacts import read_regular_file  # noqa: E402


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def result(path: Path) -> dict:
    suites = ElementTree.parse(path).getroot()
    nodes = [suites] if suites.tag == "testsuite" else list(suites.findall("testsuite"))
    return {
        key: sum(int(suite.get(key, "0")) for suite in nodes)
        for key in ("tests", "failures", "errors", "skipped")
    }


def workflow(name: str) -> dict:
    if not re.fullmatch(r"(?:fit-select|pinned-baseline)-[a-f0-9]{32}", name):
        raise ValueError("Expected a run receipt identifier")
    directory = ROOT / "data/artifacts" / name
    raw = read_regular_file(directory, "report.json")
    report = json.loads(raw)
    hashes = dict(report.get("artifact_hashes", {}))
    bound = report.get("baseline_binding")
    if bound:
        binding_raw = read_regular_file(directory, bound["artifact_id"])
        assert digest(binding_raw) == bound["sha256"]
        hashes[bound["artifact_id"]] = bound["sha256"]
        binding = json.loads(binding_raw)
        for prefix, entries in (
            ("snapshot", binding["exported_files"]),
            ("acceptance", binding["acceptance_files"]),
        ):
            hashes.update({f"{prefix}/{path}": expected for path, expected in entries.items()})
        for capture in binding["captures"]:
            hashes[capture["artifact_id"]] = capture["sha256"]
            hashes.update(
                {
                    f"test-captures/{capture['suite']}/{path}": expected
                    for path, expected in capture["artifact_hashes"].items()
                }
            )
    for path, expected in hashes.items():
        assert digest(read_regular_file(directory, path)) == expected, path
    fit = (
        json.loads(read_regular_file(directory, "fit-result.json"))["data"]
        if "fit-result.json" in hashes
        else None
    )
    return {
        "report": f"data/artifacts/{name}/report.json",
        "report_sha256": digest(raw),
        "status": report["status"],
        "error_code": report.get("error_code"),
        "stage_completed": report.get("stage_completed"),
        "usage": report.get("usage", {}),
        "model_calls": len(report.get("calls", [])),
        "attempts": len(report.get("attempts", [])),
        "verified_artifacts": len(hashes),
        "source_unchanged": report.get("source_unchanged"),
        "original_target_unchanged": report.get("original_target_unchanged"),
        "worktree_unchanged": report.get("worktree_unchanged"),
        "selected_feature_id": report.get("selected_feature_id"),
        "candidate_scores": [candidate["total"] for candidate in fit["candidates"]] if fit else [],
        "analyzed_feature_count": report.get("analyzed_feature_count"),
        "eligible_feature_count": report.get("eligible_feature_count"),
        "retry_diagnostic_status": report.get("retry_diagnostic_status"),
        "schema_version_selection": report.get("schema_version_selection"),
        "prompt_versions": report.get("prompt_versions"),
        "post_implementation_verified": report.get("post_implementation_verified", False),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", action="append", default=[])
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent
    source_files = []
    for path, pattern in (
        ("backend/app", "*.py"),
        ("backend/tests", "*.py"),
        ("frontend/src", "*"),
        ("frontend/tests", "*.ts"),
        ("scripts", "*.py"),
    ):
        source_files.extend(p for p in (ROOT / path).rglob(pattern) if p.is_file())
    source_files.extend(
        ROOT / p
        for p in (
            "backend/pyproject.toml",
            "backend/uv.lock",
            "frontend/package.json",
            "frontend/package-lock.json",
        )
    )
    file_hashes = {
        p.relative_to(ROOT).as_posix(): digest(p.read_bytes()) for p in sorted(source_files)
    }
    canonical = json.dumps(file_hashes, sort_keys=True, separators=(",", ":")).encode()
    checks = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "base_commit": "9d0ea7297d0844070d7706ca1cb7a2e92c09fb08",
        "implementation_identity": "Base commit plus uncommitted source-file hashes below",
        "source_manifest_sha256": digest(canonical),
        "source_files": file_hashes,
        "tests": {path.name: result(path) for path in sorted(directory.glob("*-tests.xml"))},
        "selection_runs": [workflow(name) for name in args.selection],
        "docker_baseline": workflow("pinned-baseline-9bfd4564a03241fbacc44d8c3416031a"),
        "github_live_verified": False,
        "auth_mode": "local",
        "full_connected_analysis_release_complete": False,
        "verified_adaptation_release_complete": False,
    }
    (directory / "checks.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    public_directory = ROOT / "data/artifacts/public-project-check-9e6343e380d54c76880dde0d16e2a50d"
    report = json.loads(read_regular_file(public_directory, "report.json"))
    for path, expected in report["artifact_hashes"].items():
        assert digest(read_regular_file(public_directory, path)) == expected
    (directory / "public-projects.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "passed",
                "receipt": "audit/full-feature-foundations/checks.json",
                "selection_runs": len(args.selection),
                "source_files": len(file_hashes),
            }
        )
    )


if __name__ == "__main__":
    main()
