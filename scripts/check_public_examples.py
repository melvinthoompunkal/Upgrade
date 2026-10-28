"""Pin and inspect the chosen independent public Python examples without inference/execution."""

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.errors import GraftError  # noqa: E402
from app.project_support import inspect_project  # noqa: E402
from app.selection_artifacts import ArtifactStore, prepare_directory  # noqa: E402
from app.tools.repositories import RepositoryManager  # noqa: E402
from app.tools.runner import PROJECT_ROOT  # noqa: E402

EXAMPLES = {
    "source": "https://github.com/urllib3/urllib3",
    "target": "https://github.com/encode/httpx",
}


def main() -> int:
    directory = PROJECT_ROOT / "data/artifacts" / f"public-project-check-{uuid4().hex}"
    report = {
        "status": "failed",
        "created_at": datetime.now(UTC).isoformat(),
        "projects": [],
        "model_calls": 0,
        "repository_code_executed": False,
        "scope": "Public snapshot and static support preflight, not a release certification.",
    }
    try:
        prepare_directory(directory)
        manager = RepositoryManager(directory / "repositories")
        for role, url in EXAMPLES.items():
            refs = manager.git.run("ls-remote", "--symref", url, "HEAD", max_bytes=4096).decode()
            match = re.search(r"^([0-9a-f]{40})\s+HEAD$", refs, flags=re.MULTILINE)
            branch = re.search(r"^ref: refs/heads/([^\t\r\n]+)\s+HEAD$", refs, flags=re.MULTILINE)
            if not match or not branch:
                raise ValueError("Public HEAD could not be resolved")
            repo = manager.public_snapshot(url, match[1], role=role)
            repo.verify()
            report["projects"].append(
                {
                    "role": role,
                    "requested_ref": branch[1],
                    "snapshot": repo.snapshot.model_dump(mode="json"),
                    "support": inspect_project(repo).model_dump(mode="json"),
                    "inventory_files": len(repo.inventory.files),
                    "inventory_bytes": sum(entry.bytes for entry in repo.inventory.files),
                    "limits_reached": list(repo.inventory.limits_reached),
                }
            )
        report["status"] = "passed"
    except (GraftError, OSError, ValueError) as error:
        report.update(
            error_code=error.code if isinstance(error, GraftError) else "preflight_failed",
            detail=str(error)
            if isinstance(error, GraftError)
            else "Public snapshot preflight failed.",
        )
    try:
        store = ArtifactStore(directory, {})
        for project in report["projects"]:
            store.adopt("repositories/" + project["snapshot"]["inventory_artifact_id"])
        report["artifact_hashes"] = store.verified_hashes()
        store.json("report.json", report)
    except (GraftError, OSError, ValueError):
        print(json.dumps({"status": "failed", "error_code": "publication_failed"}), file=sys.stderr)
        return 1
    print(json.dumps({"status": report["status"], "report": str(directory / "report.json")}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
