"""Offline operator tools. No provider requests, inference or public diagnostics endpoint."""

import argparse
import hashlib
import json
import shutil
import sqlite3
import time
from contextlib import ExitStack
from pathlib import Path

from app.api.contracts import RunSummary
from app.api.storage import RunStore
from app.config import Settings
from app.execution import ProcessLock
from app.jobs.containers import reconcile_containers
from app.jobs.leases import JobLeases
from app.jobs.resources import Resources, tree_bytes
from app.jobs.retention import Retention
from app.selection_artifacts import prepare_directory
from app.tools.runner import PROJECT_ROOT
from app.tools.runner_artifacts import physical_root


def hashes(root):
    tree_bytes(root)  # Reject links before copying/reading any managed content.
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path != root / "backup-manifest.json":
            with path.open("rb") as stream:
                result[path.relative_to(root).as_posix()] = hashlib.file_digest(
                    stream, "sha256"
                ).hexdigest()
    return result


def backup(store, root, destination):
    if destination.exists():
        raise ValueError("Backup requires a new destination directory.")
    if destination.absolute().is_relative_to(root.absolute()):
        raise ValueError("Keep backups outside the active data directory.")
    prepare_directory(destination)
    target = destination / "web/runs.sqlite3"
    prepare_directory(target.parent)
    with store.connect() as source, sqlite3.connect(target) as output:
        source.backup(output)
    with (
        store.usage.connect() as source,
        sqlite3.connect(target.with_name("usage.sqlite3")) as output,
    ):
        source.backup(output)
    for domain in ("artifacts", "projects", "private-repositories"):
        source = root / domain
        if source.exists():
            tree_bytes(source)
            shutil.copytree(source, destination / domain)
    manifest = {
        "schema": "upgrade-backup-v1",
        "created": time.time(),
        "hashes": hashes(destination),
    }
    (destination / "backup-manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return {"state": "complete", "file_count": len(manifest["hashes"])}


def restore(source, destination, *, deletions_store=None):
    physical_root(source)
    manifest = json.loads((source / "backup-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "upgrade-backup-v1" or manifest["hashes"] != hashes(source):
        raise ValueError("Backup integrity verification failed.")
    if time.time() - manifest["created"] > 30 * 86400:
        raise ValueError("Backup is outside the supported 30-day restore window.")
    if destination.exists():
        raise ValueError("Restore requires a new destination directory.")
    if destination.absolute().is_relative_to(source.absolute()):
        raise ValueError("Restore outside the backup directory.")
    prepare_directory(destination.parent)
    shutil.copytree(source, destination)
    store = RunStore(destination / "web/runs.sqlite3")
    with store.connect() as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Restored database integrity verification failed.")
        db.execute("BEGIN IMMEDIATE")
        for row in db.execute(
            "SELECT summary FROM runs WHERE json_extract(summary,'$.status')!='finished'"
        ).fetchall():
            JobLeases(store).settle_in(db, RunSummary.model_validate_json(row[0]), "interrupted")
    if deletions_store:
        with deletions_store.connect() as db:
            owners = [
                r[0]
                for r in db.execute(
                    "SELECT DISTINCT owner_id FROM cleanup_receipts "
                    "WHERE state='complete' AND created>?",
                    (manifest["created"],),
                )
            ]
        for owner in owners:
            Retention(store, destination).delete_owner(owner)
    Resources(store).settle_artifacts(destination / "artifacts")
    Retention(store, destination).prune()
    return {"state": "complete", "automatic_replay": False}


def metrics(store):
    with store.connect() as db:
        outcomes = dict(
            db.execute(
                "SELECT coalesce(json_extract(summary,'$.outcome'),"
                "json_extract(summary,'$.status')),count(*) "
                "FROM runs GROUP BY 1"
            ).fetchall()
        )
        storage = db.execute("SELECT coalesce(sum(bytes),0) FROM owned_resources").fetchone()[0]
        oldest = db.execute(
            "SELECT min(json_extract(summary,'$.created_at')) FROM runs "
            "WHERE json_extract(summary,'$.status')='queued'"
        ).fetchone()[0]
        failures = db.execute(
            "SELECT count(*) FROM cleanup_receipts WHERE state='failed'"
        ).fetchone()[0]
        errors = dict(
            db.execute(
                "SELECT json_extract(summary,'$.error.code'),count(*) FROM runs "
                "WHERE json_extract(summary,'$.error.code') IS NOT NULL GROUP BY 1"
            ).fetchall()
        )
    with store.usage.connect() as db:
        counters = dict(
            db.execute("SELECT resource,sum(amount) FROM job_metrics GROUP BY resource").fetchall()
        )
    return {
        "outcomes": outcomes,
        "errors": errors,
        "counters": counters,
        "reserved_bytes": storage,
        "oldest_queued_at": oldest,
        "cleanup_failures": failures,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("metrics", "reconcile", "prune", "backup", "restore"))
    parser.add_argument("--data-root", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    try:
        with ExitStack() as stack:
            stack.enter_context(ProcessLock(args.data_root / "web/worker.lock"))
            stack.enter_context(ProcessLock(PROJECT_ROOT / "data/execution.lock"))
            if args.action == "restore":
                if not args.source or not args.destination:
                    raise ValueError("Restore needs --source and --destination.")
                current = RunStore(args.data_root / "web/runs.sqlite3", Settings())
                result = restore(args.source, args.destination, deletions_store=current)
            else:
                store = RunStore(args.data_root / "web/runs.sqlite3", Settings())
                retention = Retention(store, args.data_root)
                if args.action == "metrics":
                    result = metrics(store)
                elif args.action == "reconcile":
                    recovered = JobLeases(store).recover(abandoned_worker=True)
                    reconcile_containers(store)
                    retention.reconcile_pending()
                    retention.account_existing()
                    Resources(store).settle_artifacts(args.data_root / "artifacts")
                    result = {"state": "complete", "interrupted_jobs": len(recovered)}
                elif args.action == "prune":
                    result = {"state": "complete", "deleted_count": retention.prune()}
                else:
                    if not args.destination:
                        raise ValueError("Backup needs --destination.")
                    result = backup(store, args.data_root, args.destination)
            print(json.dumps(result, sort_keys=True))
    except Exception:
        # Operator output is deliberately metadata-only; configuration/key/path errors stay local.
        print(
            json.dumps(
                {
                    "state": "failed",
                    "message": "Operation failed. Check locks and integrity. No replay occurred.",
                }
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
