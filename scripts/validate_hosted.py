"""Inspect hosted admission or prepare an unapproved deployment-evidence template, offline."""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

from app.api.storage import RunStore
from app.config import Settings
from app.errors import GraftError
from app.hosting.release import GATES, PROFILES, build_digest, released_profiles
from app.hosting.status import WorkerStatus

ROOT = Path(__file__).resolve().parents[1]


def template(settings: Settings) -> dict:
    return {
        "schema_version": "hosted-release-v1",
        "deployment_id": settings.deployment_id,
        "origin": settings.app_base_url,
        "build_sha256": build_digest(ROOT),
        "created": time.time(),
        "expires": time.time() + 7 * 86400,
        "profiles": list(PROFILES),
        "approved_by": "",
        "evidence": {
            gate: {"path": f"{gate}.json", "sha256": "", "outcome": "pending"} for gate in GATES
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    try:
        if args.env_file:
            settings = Settings(_env_file=args.env_file)
        else:
            from app.hosting.runtime import deployment_settings

            settings = deployment_settings()
        if settings.deployment_mode != "hosted":
            raise ValueError("Hosted configuration is required.")
        if args.template:
            # Never manufacture passing evidence or overwrite an operator's existing approval.
            with args.template.open("x", encoding="utf-8") as output:
                json.dump(template(settings), output, indent=2)
            print(json.dumps({"status": "pending", "admission_enabled": False}))
            return 0
        profiles = released_profiles(settings)
        store = RunStore(settings.data_root / "web/runs.sqlite3", settings)
        available = WorkerStatus(store).available()
        print(
            json.dumps(
                {
                    "status": "passed" if profiles and available else "blocked",
                    "released_profiles": profiles,
                    "worker_available": available,
                    "target_execution_enabled": False,
                    "pending_gates": [] if profiles else list(GATES),
                }
            )
        )
        return 0 if profiles and available else 1
    except (OSError, ValueError, GraftError, sqlite3.Error):
        print(
            json.dumps(
                {
                    "status": "failed",
                    "message": "Check hosted configuration and evidence.",
                }
            )
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
