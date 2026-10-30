"""Contained, retryable deletion under the workspace lock and an admission transaction."""

import json
import os
import re
import shutil
import stat
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from app.jobs.control import ResourceLimit
from app.jobs.resources import Resources, tree_bytes
from app.tools.runner_artifacts import physical_root

DOMAINS = {"artifacts", "projects", "private-repositories"}


def contained_path(root: Path, kind: str, location: str):
    if kind not in DOMAINS or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,100}", location):
        raise ResourceLimit("Cleanup refused an invalid managed storage identity.")
    base = root.absolute() / kind
    path = base / location
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ResourceLimit(
                "Cleanup refused redirected storage. Operator recovery is required."
            )
    if path.exists() and physical_root(path).parent != physical_root(base):
        raise ResourceLimit("Cleanup escaped its managed storage root.")
    return path


def remove_owned(path):
    if not path.exists():
        return

    def clear_readonly(operation, filename, error):
        if not isinstance(error, PermissionError):
            raise error
        os.chmod(filename, stat.S_IWRITE | stat.S_IREAD)
        operation(filename)

    shutil.rmtree(path, onexc=clear_readonly)


class Retention:
    """Callers hold data/execution.lock. No removal while the owner's jobs are unfinished."""

    def __init__(self, store, root):
        self.store, self.root = store, root

    @staticmethod
    def idle_in(db, owner):
        if db.execute(
            "SELECT 1 FROM runs WHERE owner_id=? AND json_extract(summary,'$.status')!='finished'",
            (owner,),
        ).fetchone():
            raise HTTPException(409, "Finish or cancel this account's jobs before deleting data.")

    def locations_in(self, db, owner):
        locations = {
            (r["kind"], r["location"])
            for r in db.execute(
                "SELECT kind,location FROM owned_resources WHERE owner_id=?",
                (owner,),
            )
        }
        # Pre-F6 descriptors/reports remain eligible for owner deletion, without public import.
        for table, kind in (
            ("projects", "projects"),
            ("repository_snapshots", "private-repositories"),
        ):
            locations.update(
                (kind, r[0])
                for r in db.execute(
                    f"SELECT id FROM {table} WHERE owner_id=?",
                    (owner,),
                )
            )
        for row in db.execute(
            "SELECT report FROM runs WHERE owner_id=? AND report IS NOT NULL", (owner,)
        ):
            relative = Path(row[0])
            if len(relative.parts) != 2 or relative.name != "report.json":
                raise ResourceLimit("Cleanup refused an invalid report location.")
            locations.add(("artifacts", relative.parts[0]))
        return sorted(locations)

    def delete_owner(self, owner):
        identifier = uuid4().hex
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO cleanup_receipts VALUES (?,?,?,'pending',0)",
                (
                    identifier,
                    owner,
                    time.time(),
                ),
            )
        try:
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                self.idle_in(db, owner)
                paths = [
                    contained_path(self.root, *location)
                    for location in self.locations_in(db, owner)
                ]
                # Validate the entire deletion set before the first filesystem mutation.
                for path in paths:
                    remove_owned(path)
                self.purge_in(db, owner)
                db.execute(
                    "UPDATE cleanup_receipts SET state='complete',deleted_count=? WHERE id=?",
                    (len(paths), identifier),
                )
            with self.store.usage.connect() as db:
                db.execute("DELETE FROM job_metrics WHERE owner_id=?", (owner,))
            return {"id": identifier, "state": "complete", "deleted_count": len(paths)}
        except Exception:
            with self.store.connect() as db:
                db.execute("UPDATE cleanup_receipts SET state='failed' WHERE id=?", (identifier,))
            raise

    @staticmethod
    def purge_in(db, owner):
        db.execute(
            "DELETE FROM relevance_features WHERE assessment_id IN "
            "(SELECT id FROM relevance_assessments WHERE owner_id=?)",
            (owner,),
        )
        db.execute("DELETE FROM relevance_assessments WHERE owner_id=?", (owner,))
        db.execute("DELETE FROM relevance_operations WHERE owner_id=?", (owner,))
        for table in ("catalog_features", "catalog_revisions"):
            db.execute(
                f"DELETE FROM {table} WHERE catalog_id IN "
                "(SELECT id FROM catalogs WHERE owner_id=?)",
                (owner,),
            )
        for table in (
            "catalogs",
            "catalog_operations",
            "projects",
            "repository_snapshots",
            "repository_cache",
            "repository_sync",
            "model_consents",
            "owned_resources",
        ):
            db.execute(f"DELETE FROM {table} WHERE owner_id=?", (owner,))
        for table in (
            "events",
            "run_authorizations",
            "job_leases",
            "published_artifacts",
        ):
            db.execute(
                f"DELETE FROM {table} WHERE run_id IN (SELECT id FROM runs WHERE owner_id=?)",
                (owner,),
            )
        db.execute("DELETE FROM runs WHERE owner_id=?", (owner,))

    def reconcile_pending(self):
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT * FROM owned_resources WHERE run_id IS NULL AND state='reserved' "
                "AND kind IN ('projects','private-repositories')"
            ).fetchall()
        for row in rows:
            table = "projects" if row["kind"] == "projects" else "repository_snapshots"
            with self.store.connect() as db:
                exists = db.execute(
                    f"SELECT 1 FROM {table} WHERE id=?", (row["location"],)
                ).fetchone()
            if exists:
                Resources(self.store).complete(row["id"])
            else:
                remove_owned(contained_path(self.root, row["kind"], row["location"]))
                Resources(self.store).forget(row["id"])

    def account_existing(self):
        """Count pre-F6 code and reports without changing ownership or admitting new spend."""
        candidates = []
        with self.store.connect() as db:
            for table, kind in (
                ("projects", "projects"),
                ("repository_snapshots", "private-repositories"),
            ):
                for row in db.execute(f"SELECT id,owner_id,descriptor FROM {table}"):
                    created = json.loads(row["descriptor"])["created_at"]
                    stamp = (
                        datetime.fromisoformat(created).timestamp()
                        if isinstance(created, str)
                        else created
                    )
                    candidates.append((kind, row["id"], row["owner_id"], None, stamp))
            for row in db.execute(
                "SELECT id,owner_id,report,summary FROM runs WHERE report IS NOT NULL"
            ):
                path = Path(row["report"])
                if len(path.parts) == 2 and path.name == "report.json":
                    stamp = datetime.fromisoformat(
                        json.loads(row["summary"])["created_at"]
                    ).timestamp()
                    candidates.append(
                        ("artifacts", path.parts[0], row["owner_id"], row["id"], stamp)
                    )
        for kind, location, owner, run_id, created in candidates:
            path = contained_path(self.root, kind, location)
            size = tree_bytes(path) if path.exists() else 0
            with self.store.connect() as db:
                db.execute(
                    "INSERT OR IGNORE INTO owned_resources VALUES (?,?,?,?,?,?,?,'ready')",
                    (uuid4().hex, owner, run_id, kind, location, size, created),
                )

    def prune(self, *, at=None):
        cutoff = (time.time() if at is None else at) - self.store.settings.retention_days * 86400
        deleted = 0
        with self.store.connect() as db:
            owners = {
                r[0]
                for r in db.execute(
                    "SELECT owner_id FROM runs UNION SELECT owner_id FROM projects "
                    "UNION SELECT owner_id FROM repository_snapshots "
                    "UNION SELECT owner_id FROM owned_resources"
                )
            }
        for owner in owners:
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                try:
                    self.idle_in(db, owner)
                except HTTPException:
                    continue
                # Private snapshots cannot feed jobs in this release. Expire every code copy
                # by its own creation time, even when public workspace data is more recent.
                expired = db.execute(
                    "SELECT id FROM repository_snapshots WHERE owner_id=? AND created<?",
                    (owner, cutoff),
                ).fetchall()
                paths = [contained_path(self.root, "private-repositories", r[0]) for r in expired]
                for row, path in zip(expired, paths, strict=True):
                    remove_owned(path)
                    db.execute("DELETE FROM repository_snapshots WHERE id=?", (row[0],))
                    db.execute(
                        "DELETE FROM projects WHERE owner_id=? AND "
                        "json_extract(descriptor,'$.repository_snapshot')=?",
                        (owner, row[0]),
                    )
                    db.execute(
                        "DELETE FROM owned_resources "
                        "WHERE kind='private-repositories' AND location=?",
                        (row[0],),
                    )
                    deleted += 1
                if expired:
                    db.execute("DELETE FROM model_consents WHERE owner_id=?", (owner,))
                dates = [
                    r[0]
                    for r in db.execute(
                        "SELECT json_extract(summary,'$.updated_at') FROM runs WHERE owner_id=? "
                        "UNION ALL SELECT json_extract(descriptor,'$.created_at') "
                        "FROM projects WHERE owner_id=?",
                        (owner, owner),
                    )
                ]
                newest = max((datetime.fromisoformat(d).timestamp() for d in dates), default=0)
                newest = max(
                    newest,
                    db.execute(
                        "SELECT coalesce(max(created),0) FROM owned_resources WHERE owner_id=?",
                        (owner,),
                    ).fetchone()[0],
                )
            if newest and newest < cutoff:
                deleted += self.delete_owner(owner)["deleted_count"]
        with self.store.usage.connect() as db:
            db.execute(
                "DELETE FROM resource_usage WHERE day<?",
                (datetime.fromtimestamp(cutoff, UTC).date().isoformat(),),
            )
        with self.store.connect() as db:
            db.execute(
                "DELETE FROM cleanup_receipts WHERE created<? AND state='complete'", (cutoff,)
            )
        return deleted
