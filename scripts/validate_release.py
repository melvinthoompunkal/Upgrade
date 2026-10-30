"""Run fresh local F9 checks and retain hash-bound evidence; never certifies live gates."""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import sysconfig
from pathlib import Path
from uuid import uuid4

from app.errors import GraftError
from app.selection_artifacts import prepare_directory

# Supports both `python scripts/validate_release.py` and `python -m scripts.validate_release`.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.release_validation import (  # noqa: E402
    PENDING_GATES,
    SCHEMA_VERSION,
    artifact_manifest,
    environment,
    local_status,
    run_step,
    source_manifest,
    timestamp,
    verify_receipt,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--with-docker",
        action="store_true",
        help="Also run a fresh offline F7 Docker adaptation test.",
    )
    parser.add_argument(
        "--with-audits",
        action="store_true",
        help="Query public Python/npm vulnerability databases (requires network and uvx).",
    )
    parser.add_argument(
        "--verify",
        type=Path,
        help="Recheck a receipt's hashes against current source bytes; run no tests.",
    )
    args = parser.parse_args()
    if args.verify:
        try:
            print(json.dumps(verify_receipt(args.verify, ROOT)))
            return 0
        except (OSError, ValueError, KeyError, TypeError, GraftError):
            print(
                json.dumps(
                    {
                        "integrity": "failed",
                        "error": "Receipt, artifacts or source bytes did not validate.",
                    }
                )
            )
            return 1
    directory = ROOT / "data/release-validation" / uuid4().hex
    prepare_directory(directory)
    # Git's Windows loose-object/pack paths can exceed MAX_PATH inside nested fixtures.
    # Receipts stay grouped; disposable pytest roots use short, unique workspace paths.
    test_root = ROOT / "data" / f"f9t-{directory.name[:8]}"
    docker_root = ROOT / "data" / f"f9d-{directory.name[:8]}"
    if test_root.exists() or docker_root.exists():
        raise ValueError("Refusing to reuse a pytest working directory.")
    before = source_manifest(ROOT)
    env = os.environ.copy()
    env.pop("GRAFT_F7_DOCKER", None)
    env["GRAFT_SCALE_RECEIPT"] = str(directory / "query-scale.json")
    env["GRAFT_RELEASE_ROOT"] = str(directory)
    env["GRAFT_RELEASE_PYTHON"] = sys.executable
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        env["GRAFT_RELEASE_PORT"] = str(port_socket.getsockname()[1])
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm") or "npm"
    python = sys.executable
    lint = [python, "-m", "ruff"]
    scope = ["--config", "backend/pyproject.toml", "backend", "scripts", "sandbox", "examples"]
    steps = []
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "started_at": timestamp(),
        "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT)
        .decode()
        .strip(),
        "environment": environment(),
        "sources": before,
        "scope": (
            "Local controlled-provider regression, synthetic saved-query/browser scale and "
            "production build. No live model/GitHub account validation."
        ),
        "release_status": {name: "pending" for name in PENDING_GATES},
        "pending_release_gates": PENDING_GATES,
        "publishing_enabled": False,
        "dependency_audit_requested": args.with_audits,
        "test_work_roots": [
            test_root.relative_to(ROOT).as_posix(),
            docker_root.relative_to(ROOT).as_posix(),
        ],
        "steps": steps,
        "local_status": "running",
    }
    path = directory / "receipt.json"
    write_json(path, receipt)

    def check(name, command, cwd=ROOT, *, junit=None, step_env=env):
        record = run_step(name, command, cwd, directory, step_env, junit=junit)
        steps.append(record)
        write_json(path, receipt)
        return record["status"] == "passed"

    check("lint", [*lint, "check", *scope])
    check("format", [*lint, "format", "--check", *scope])
    if args.with_audits:
        audit_env = {**env, "UV_TOOL_DIR": str(ROOT / ".uv-cache/tools")}
        check(
            "python-audit",
            [
                shutil.which("uvx") or "uvx",
                "--cache-dir",
                str(ROOT / ".uv-cache"),
                "--from",
                "pip-audit==2.10.1",
                "pip-audit",
                "--path",
                sysconfig.get_path("purelib"),
                "--format",
                "json",
                "--output",
                str(directory / "python-audit.json"),
            ],
            step_env=audit_env,
        )
        check("npm-audit", [npm, "audit", "--json"], ROOT / "frontend")
    check(
        "backend",
        [
            python,
            "-m",
            "pytest",
            "backend/tests",
            "--maxfail=1",
            "-k",
            "not test_independent_docker_red_to_green",
            f"--basetemp={test_root}",
            f"--junitxml={directory / 'backend-junit.xml'}",
        ],
        junit="backend-junit.xml",
    )
    if args.with_docker:
        env["GRAFT_RELEASE_DOCKER"] = "1"
        docker_env = {
            **env,
            "GRAFT_F7_DOCKER": "1",
            "GRAFT_F9_DOCKER_ARTIFACTS": str(directory / "fresh-adaptation"),
        }
        check(
            "docker",
            [
                python,
                "-m",
                "pytest",
                "backend/tests/test_adaptation.py::test_independent_docker_red_to_green",
                f"--basetemp={docker_root}",
                f"--junitxml={directory / 'docker-junit.xml'}",
            ],
            junit="docker-junit.xml",
            step_env=docker_env,
        )
    else:
        env.pop("GRAFT_RELEASE_DOCKER", None)
        steps.append(
            {
                "name": "docker",
                "status": "not_run",
                "reason": "Use --with-docker for fresh offline execution with controlled models.",
            }
        )
    built = check("build", [npm, "run", "build"], ROOT / "frontend")
    if built:
        shutil.copytree(ROOT / "frontend/dist", directory / "build")
        check(
            "browser",
            [npm, "run", "smoketest", "--", "--config=playwright.release.config.ts"],
            ROOT / "frontend",
            junit="browser-junit.xml",
        )
    else:
        steps.append({"name": "browser", "status": "blocked", "reason": "Production build failed."})
    check("diff", ["git", "diff", "--check"])
    receipt["source_unchanged_during_validation"] = before == source_manifest(ROOT)
    receipt["local_status"] = local_status(steps, receipt["source_unchanged_during_validation"])
    receipt["finished_at"] = timestamp()
    receipt["artifacts"] = artifact_manifest(directory)
    write_json(path, receipt)
    print(
        json.dumps(
            {
                "local_status": receipt["local_status"],
                "receipt": path.relative_to(ROOT).as_posix(),
                "release_status": receipt["release_status"],
            }
        ),
        flush=True,
    )
    return 0 if receipt["local_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
