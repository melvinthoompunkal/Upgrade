"""Receipt integrity and process execution for the local F9 validation command."""

import hashlib
import json
import os
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from xml.etree import ElementTree

from app.contracts import validate_repo_path
from app.tools.runner_artifacts import physical_root, read_regular_file

SCHEMA_VERSION = "f9-local-validation-v1"
REQUIRED_STEPS = ("lint", "format", "backend", "build", "browser", "diff")
PENDING_GATES = {
    "connected_analysis": [
        "Real GitHub App login, granted/ungranted repositories and provider token lifecycle.",
        "Fresh browser-driven public multi-feature discovery and both relevance modes with Meta.",
        "Independent workflow input, count, usage, notice and artifact-hash verification.",
        "Runner-image/deployment dependency review and real provider/hard-stop exit checks.",
    ],
    "verified_adaptation": [
        "All connected-analysis release gates.",
        "Fresh live-model supported source-to-target patch through browser review/download.",
        "Frozen assertion red, unchanged acceptance/regression green and actual scope receipts.",
    ],
    "multi_user_hosting": [
        "F6 hosted isolation, multi-user load, deployment/rollback and hard-stop certification.",
    ],
    "optional_pr_publishing": [
        "Real GitHub user/App write denial, moved base, duplicate and uncertain-write checks.",
        "Exact published patch/notices and unchanged default branch on a development repository.",
    ],
}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def artifact_digest(root: Path, name: str) -> str:
    """Stream potentially large browser traces while preserving physical containment."""
    validate_repo_path(name)
    root = physical_root(root)
    path = root / name
    physical_root(path.parent)
    if path.is_symlink() or path.is_junction() or not path.is_file():
        raise ValueError("Expected a regular validation artifact.")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def source_manifest(root: Path) -> dict[str, str]:
    names = (
        subprocess.check_output(
            [
                "git",
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
                "--",
                "backend",
                "frontend",
                "scripts",
                "deploy",
                "sandbox",
                "examples",
                "docs",
                "README.md",
                "tech_breakdown.md",
                ".gitignore",
                ".python-version",
            ],
            cwd=root,
        )
        .decode()
        .split("\0")
    )
    return {name: digest(read_regular_file(root, name)) for name in sorted(set(names)) if name}


def junit_result(path: Path) -> dict[str, int]:
    """Count cases, cross-check suite counters, and reject empty/ambiguous receipts."""
    tree = ElementTree.fromstring(path.read_bytes())
    if tree.tag not in {"testsuites", "testsuite"}:
        raise ValueError("Invalid JUnit document.")
    suites = list(tree.iter("testsuite"))
    if not suites:
        raise ValueError("JUnit has no test suites.")
    totals = {}
    for suite in [*suites, tree]:
        cases = list(suite.iter("testcase"))
        counts = {
            "tests": len(cases),
            **{
                key: sum(case.find(tag) is not None for case in cases)
                for key, tag in (
                    ("failures", "failure"),
                    ("errors", "error"),
                    ("skipped", "skipped"),
                )
            },
        }
        for key, count in counts.items():
            if (key in suite.attrib and int(suite.get(key)) != count) or (
                key == "tests" and suite.tag == "testsuite" and key not in suite.attrib
            ):
                raise ValueError("JUnit counters disagree with cases.")
        totals = counts
    if not totals["tests"]:
        raise ValueError("Empty JUnit cannot establish verification.")
    totals["passed"] = totals["tests"] - sum(
        totals[key] for key in ("failures", "errors", "skipped")
    )
    return totals


def run_step(
    name: str,
    command: list[str],
    cwd: Path,
    directory: Path,
    env: dict[str, str],
    *,
    junit: str | None = None,
    timeout: int = 1800,
) -> dict:
    print(json.dumps({"step": name, "status": "running"}), flush=True)
    start = monotonic()
    record = {"name": name, "command": command, "status": "failed"}
    log = directory / f"{name}.log"
    with log.open("wb") as output:
        try:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=output, stderr=output)
            try:
                record["exit_code"] = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                record["error"] = "timeout"
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=output,
                        stderr=output,
                        check=False,
                    )
                else:
                    process.kill()
                process.wait()
            if record.get("exit_code") == 0:
                record["status"] = "passed"
        except OSError:
            record["error"] = "command_unavailable"
    if junit:
        record["junit"] = junit
        try:
            record["tests"] = junit_result(directory / junit)
            if any(record["tests"][key] for key in ("failures", "errors", "skipped")):
                record["status"] = "failed"
        except (OSError, ValueError, ElementTree.ParseError):
            record.update(status="failed", error="missing_or_invalid_junit")
    record["seconds"] = round(monotonic() - start, 3)
    print(
        json.dumps({"step": name, "status": record["status"], "seconds": record["seconds"]}),
        flush=True,
    )
    return record


def artifact_manifest(directory: Path) -> dict[str, str]:
    # Excludes the disposable DB/server and target test working directories.
    files = [*directory.glob("*.log"), *directory.glob("*.xml"), *directory.glob("*-results.json")]
    files.extend(directory.glob("*scale.json"))
    files.extend(directory.glob("*-audit.json"))
    files.extend(path for path in (directory / "build").rglob("*") if path.is_file())
    files.extend(path for path in (directory / "browser-artifacts").rglob("*") if path.is_file())
    files.extend(path for path in (directory / "fresh-adaptation").rglob("*") if path.is_file())
    return {
        path.relative_to(directory).as_posix(): artifact_digest(
            directory, path.relative_to(directory).as_posix()
        )
        for path in sorted(files)
    }


def local_status(steps: list[dict], stable: bool) -> str:
    indexed = {step["name"]: step for step in steps}
    return (
        "passed"
        if (
            stable
            and len(indexed) == len(steps)
            and all(
                indexed.get(name, {}).get("status") == "passed"
                and indexed[name].get("exit_code") == 0
                for name in REQUIRED_STEPS
            )
            and all(
                step["status"] == "passed"
                or (step["name"] == "docker" and step["status"] == "not_run")
                for step in steps
            )
        )
        else "failed"
    )


def verify_receipt(path: Path, root: Path) -> dict:
    receipt = json.loads(path.read_bytes())
    if receipt["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported validation receipt.")
    if receipt["sources"] != source_manifest(root):
        raise ValueError("Current source files differ from tested bytes.")
    if not receipt["source_unchanged_during_validation"]:
        raise ValueError("Sources changed while validation ran.")
    if local_status(receipt["steps"], True) != receipt["local_status"]:
        raise ValueError("Validation status disagrees with required steps.")
    if receipt["release_status"] != {name: "pending" for name in PENDING_GATES}:
        raise ValueError("Local checks cannot certify external release gates.")
    artifacts = receipt["artifacts"]
    required = {f"{name}.log" for name in REQUIRED_STEPS} | {
        "backend-junit.xml",
        "browser-junit.xml",
        "browser-results.json",
        "query-scale.json",
        "build/index.html",
    }
    if not required <= artifacts.keys():
        raise ValueError("Missing required validation artifacts.")
    for name, expected in artifacts.items():
        if artifact_digest(path.parent, name) != expected:
            raise ValueError("Validation artifact digest mismatch.")
    for step in receipt["steps"]:
        if "junit" in step:
            if (
                step["junit"] not in artifacts
                or junit_result(path.parent / step["junit"]) != step["tests"]
            ):
                raise ValueError("JUnit evidence differs from the receipt.")
            if step["status"] == "passed" and (
                step.get("exit_code") != 0
                or any(step["tests"][key] for key in ("failures", "errors", "skipped"))
            ):
                raise ValueError("A failed or skipped suite cannot pass.")
    return {
        "integrity": "passed",
        "local_status": receipt["local_status"],
        "release_status": receipt["release_status"],
    }


def environment() -> dict:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "logical_cpus": os.cpu_count(),
        "node": subprocess.check_output(["node", "--version"]).decode().strip(),
    }


def timestamp() -> str:
    return datetime.now(UTC).isoformat()
