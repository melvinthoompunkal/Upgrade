"""Serve a disposable, read-only production build for local browser validation."""

import argparse
import json
import shutil
from pathlib import Path

import uvicorn
from starlette.responses import JSONResponse

from app.api.artifacts import digest, summary_from_report
from app.api.storage import RunStore
from app.config import Settings
from app.main import create_app
from app.selection_artifacts import prepare_directory
from app.tools.runner import PROJECT_ROOT
from app.tools.runner_artifacts import read_regular_file
from scripts.release_fixtures import SIZES, seed_catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    # Refuse a user database, redirected directory, or repeated seed.
    root = args.root.resolve()
    if not root.is_relative_to(PROJECT_ROOT / "data/release-validation"):
        parser.error("Use an empty directory under data/release-validation.")
    prepare_directory(args.root)
    if any(root.iterdir()):
        parser.error("Use an empty directory under data/release-validation.")
    settings = Settings(
        _env_file=None,
        auth_mode="local",
        model_api_key=None,
        github_private_fetch_enabled=False,
        app_base_url=f"http://127.0.0.1:{args.port}",
    )
    runs = RunStore(root / "web/runs.sqlite3", settings)
    for size in SIZES:
        seed_catalog(runs, size, relevant_count=3)
    fresh = root.parent / "fresh-adaptation"
    if fresh.is_dir():
        raw = read_regular_file(fresh, "report.json")
        report = json.loads(raw)
        if report["status"] != "verified":
            raise ValueError("Fresh adaptation did not verify.")
        for name, expected in report["artifact_hashes"].items():
            if digest(read_regular_file(fresh, name)) != expected:
                raise ValueError("Fresh adaptation artifact digest mismatch.")
        shutil.copytree(fresh, root / "artifacts/fresh-adaptation")
        runs.import_report(
            summary_from_report("d" * 32, "verified_adaptation", report, historical=False),
            "fresh-adaptation/report.json",
            digest(raw),
        )
    app = create_app(
        settings, data_root=root, start_worker=False, frontend_root=root.parent / "build"
    )

    @app.middleware("http")
    async def read_only(request, call_next):
        if request.method not in {"GET", "HEAD"}:
            return JSONResponse({"detail": "Read-only validation server."}, status_code=405)
        return await call_next(request)

    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
