"""Canceled runs export only evidence registered before interruption and still intact."""

import hashlib

from app.errors import GraftError
from app.jobs.control import execution_context
from app.selection_artifacts import ArtifactStore
from app.tools.runner_artifacts import read_regular_file


def stopped_report(context, code):
    with context.store.connect() as db:
        rows = db.execute(
            "SELECT directory,path,sha256 FROM published_artifacts WHERE run_id=?",
            (context.run_id,),
        ).fetchall()
    with context.store.usage.connect() as db:
        usage = dict(
            db.execute(
                "SELECT resource,amount FROM job_metrics WHERE run_id=?", (context.run_id,)
            ).fetchall()
        )
    if not rows:
        return None
    directory = context.artifact_root / rows[0]["directory"]
    hashes = {}
    for row in rows:
        if row["directory"] != directory.name or row["path"] == "report.json":
            continue
        try:
            raw = read_regular_file(directory, row["path"])
        except (GraftError, OSError, ValueError):
            continue
        if hashlib.sha256(raw).hexdigest() == row["sha256"]:
            hashes[row["path"]] = row["sha256"]
    report = {
        "status": code,
        "error_code": code,
        "detail": "Execution stopped. Intact evidence is retained.",
        "artifact_hashes": hashes,
        "usage": usage,
        "scope": "Partial evidence only. In-flight provider usage may be unknown.",
    }
    # This narrow finalizer cannot resume inference or publish a successful result.
    with execution_context(None):
        ArtifactStore(directory, {}).json("report.json", report)
    raw = read_regular_file(directory, "report.json")
    return (
        directory.relative_to(context.artifact_root) / "report.json"
    ).as_posix(), hashlib.sha256(raw).hexdigest()
