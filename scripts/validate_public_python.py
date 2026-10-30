"""Record F10 readiness without model calls, GitHub requests or target execution."""

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4
from xml.etree.ElementTree import ParseError

from pydantic import ValidationError

from app.auth.crypto import CredentialCipher
from app.config import Settings
from app.errors import GraftError
from app.selection_artifacts import prepare_directory
from app.tools.runner import prepared_image
from app.tools.runner_artifacts import read_regular_file

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.release_validation import (  # noqa: E402
    digest,
    source_manifest,
    timestamp,
    verify_receipt,
    write_json,
)

SCHEMA_VERSION = "f10-public-python-readiness-v1"
LIVE_GATES = {
    "github_lifecycle": "Fresh App sign-in, grants/denials, rotation and revocation (F2).",
    "catalog_relevance": "Fresh browser-driven Meta discovery and both saved browsing modes.",
    "verified_adaptation": "Live supported adaptation, frozen red-to-green and native regression.",
    "independent_evidence": "Pinned inputs, counts, usage, notices, patch hashes and preservation.",
    "cancellation_recovery": "Real provider cancellation and owned-process/container recovery.",
    "release_review": "Applicable section 14 negative, dependency and accessibility checks.",
}


@dataclass(frozen=True)
class Check:
    name: str
    status: Literal["passed", "blocked", "failed"]
    detail: str


def configuration_checks(settings: Settings | None) -> list[Check]:
    if settings is None:
        return [
            Check("configuration", "failed", "Correct backend settings; see docs/github-setup.md.")
        ]
    github = (
        settings.auth_mode == "github"
        and bool(settings.github_client_id.strip())
        and settings.github_client_secret is not None
        and bool(settings.github_client_secret.get_secret_value().strip())
        and bool(settings.github_app_slug.strip())
        and settings.token_encryption_keys is not None
    )
    checks = [
        Check(
            "github_configuration",
            "passed" if github else "blocked",
            "GitHub App mode, client ID/secret, slug and encryption keys must be configured; "
            "presence does not verify provider access.",
        ),
        Check(
            "model_configuration",
            "passed" if settings.model_configured else "blocked",
            "Configure MODEL_API_KEY; presence does not verify Meta connectivity or semantics.",
        ),
        Check(
            "local_origin",
            "passed"
            if urlsplit(settings.app_base_url).hostname in {"127.0.0.1", "localhost", "::1"}
            else "blocked",
            "Use the same loopback APP_BASE_URL, browser origin and registered callback for F10.",
        ),
    ]
    if settings.token_encryption_keys:
        try:
            CredentialCipher(settings.token_encryption_keys.get_secret_value())
        except GraftError:
            checks.append(
                Check("encryption_keys", "failed", "Configure valid Fernet rotation keys.")
            )
        else:
            checks.append(Check("encryption_keys", "passed", "Local keyring parses successfully."))
    return checks


def local_checks(root: Path) -> list[Check]:
    checks = [
        Check(
            "python",
            "passed" if sys.version_info[:2] == (3, 12) else "blocked",
            "Use the locked backend Python 3.12 environment.",
        ),
        Check(
            "browser_dependencies",
            "passed"
            if (root / "frontend/node_modules/@playwright/test/package.json").is_file()
            else "blocked",
            "Install frontend dependencies with npm ci; F9 verifies build/browser execution.",
        ),
    ]
    try:
        prepared_image(root / "data/runner-image.json")
    except (OSError, ValueError, GraftError):
        checks.append(
            Check(
                "prepared_runner",
                "blocked",
                "Start Docker Desktop and build the reviewed runner with scripts/build_runner.py.",
            )
        )
    else:
        checks.append(
            Check(
                "prepared_runner", "passed", "Build-input hashes and local Docker image ID match."
            )
        )
    return checks


def f9_check(root: Path, path: Path | None) -> tuple[Check, str | None]:
    if path is None:
        return (
            Check(
                "f9_evidence",
                "blocked",
                "Supply a fresh --with-docker F9 receipt via --f9-receipt.",
            ),
            None,
        )
    try:
        raw = read_regular_file(path.parent, path.name)
        result = verify_receipt(path, root)
        receipt = json.loads(raw)
        # Integrity alone also permits retained failed runs; readiness requires a green run.
        if result["local_status"] != "passed" or not any(
            step["name"] == "docker"
            and step["status"] == "passed"
            and step.get("exit_code") == 0
            and step.get("junit") == "docker-junit.xml"
            for step in receipt["steps"]
        ):
            raise ValueError("A passing fresh Docker run is required.")
        if read_regular_file(path.parent, path.name) != raw:
            raise ValueError("F9 receipt changed during verification.")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ParseError, GraftError):
        return (
            Check(
                "f9_evidence",
                "failed",
                "Require passing F9 for current sources, intact artifacts and fresh Docker checks.",
            ),
            None,
        )
    return Check(
        "f9_evidence", "passed", "Current F9 source/artifact hashes and Docker checks pass."
    ), digest(raw)


def readiness_status(checks: list[Check], *, stable: bool) -> str:
    if not stable or any(check.status == "failed" for check in checks):
        return "failed"
    if not checks or any(check.status != "passed" for check in checks):
        return "blocked"
    return "ready_for_live_validation"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--f9-receipt",
        type=Path,
        help="Integrity-check a current F9 --with-docker receipt; runs no F9 tests.",
    )
    args = parser.parse_args(argv)
    try:
        directory = prepare_directory(ROOT / "data/public-python-validation" / uuid4().hex)
        before = source_manifest(ROOT)
        started = timestamp()
        try:
            settings = Settings()
        except (ValidationError, GraftError):
            settings = None
        checks = configuration_checks(settings) + local_checks(ROOT)
        evidence, receipt_hash = f9_check(ROOT, args.f9_receipt)
        checks.append(evidence)
        stable = before == source_manifest(ROOT)
        status = readiness_status(checks, stable=stable)
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "started_at": started,
            "finished_at": timestamp(),
            "scope": "Local public-Python v1 readiness only; no live workflow is certified.",
            "status": status,
            "checks": [asdict(check) for check in checks],
            "sources": before,
            "source_unchanged_during_validation": stable,
            "f9_receipt_sha256": receipt_hash,
            "f10_status": "pending_live_validation",
            "live_gates": {
                name: {"status": "pending", "requires": text} for name, text in LIVE_GATES.items()
            },
            "publishing_enabled": False,
        }
        path = directory / "receipt.json"
        write_json(path, receipt)
        print(
            json.dumps(
                {
                    "status": status,
                    "receipt": path.relative_to(ROOT).as_posix(),
                    "checks": receipt["checks"],
                }
            )
        )
        return {"ready_for_live_validation": 0, "failed": 1, "blocked": 2}[status]
    except (OSError, ValueError, KeyError, TypeError, GraftError):
        print(json.dumps({"status": "failed", "error": "Unable to retain F10 readiness evidence."}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
