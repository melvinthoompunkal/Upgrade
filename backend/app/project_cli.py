"""CLI for the same public project pin/analysis service used by the browser worker."""

import argparse
import json

from pydantic import ValidationError

from app.api.migrations import LOCAL_OWNER
from app.api.storage import RunStore
from app.errors import GraftError
from app.execution import ProcessLock
from app.project_analysis import record_project_analysis
from app.project_contracts import ProjectAnalysisRequest, ProjectRequest
from app.projects import ProjectStore
from app.tools.runner import PROJECT_ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-root", default="")
    parser.add_argument("--target-url")
    parser.add_argument("--target-sha")
    parser.add_argument("--target-root", default="")
    parser.add_argument("--goal", default="")
    parser.add_argument("--run-baseline", action="store_true")
    args = parser.parse_args()
    if bool(args.target_url) != bool(args.target_sha) or args.run_baseline and not args.target_url:
        parser.error("A target needs both --target-url and --target-sha; baseline needs a target.")
    try:
        inputs = [
            (
                role,
                ProjectRequest(
                    url=url,
                    commit_sha=sha,
                    project_root=root,
                    role=role,
                ),
            )
            for role, url, sha, root in (
                ("source", args.source_url, args.source_sha, args.source_root),
                ("target", args.target_url, args.target_sha, args.target_root),
            )
            if url
        ]
        with ProcessLock(PROJECT_ROOT / "data/execution.lock"):
            store = ProjectStore(
                RunStore(PROJECT_ROOT / "data/web/runs.sqlite3"), PROJECT_ROOT / "data/projects"
            )
            selections = {role: store.create(LOCAL_OWNER, item) for role, item in inputs}
            request = ProjectAnalysisRequest(
                kind="project_analysis",
                source_project=selections["source"].id,
                target_project=selections["target"].id if "target" in selections else None,
                goal=args.goal,
                run_baseline=args.run_baseline,
            )
            report, path = record_project_analysis(
                request,
                store,
                LOCAL_OWNER,
                PROJECT_ROOT / "data/artifacts",
            )
            print(json.dumps({"status": report["status"], "report": str(path)}))
            return 0 if report["status"] == "passed" else 1
    except (GraftError, ValidationError, OSError) as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_code": error.code
                    if isinstance(error, GraftError)
                    else "invalid_project",
                    "detail": str(error)
                    if isinstance(error, GraftError)
                    else "Unable to prepare the public project inputs.",
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
