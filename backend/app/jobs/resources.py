"""Durable ownership before creation; conservative disk reservations until deletion."""

import time
from pathlib import Path
from uuid import uuid4

from app.jobs.control import CURRENT, ResourceLimit
from app.tools.runner_artifacts import physical_root


class Resources:
    def __init__(self, store):
        self.store = store

    def reserve(self, owner, kind, location, size, *, run_id=None):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self.reserve_in(db, owner, kind, location, size, run_id=run_id)

    def reserve_in(self, db, owner, kind, location, size, *, run_id=None):
        existing = db.execute(
            "SELECT * FROM owned_resources WHERE location=?", (location,)
        ).fetchone()
        if existing:
            if (
                existing["owner_id"] != owner
                or existing["run_id"] != run_id
                or existing["kind"] != kind
            ):
                raise ResourceLimit("Storage ownership does not match this operation.")
            return existing["id"]
        if run_id and kind == "artifacts":
            reserved = db.execute(
                "SELECT id FROM owned_resources WHERE run_id=? AND kind='job-reservation'",
                (run_id,),
            ).fetchone()
            if reserved:
                db.execute(
                    "UPDATE owned_resources SET kind=?,location=? WHERE id=?",
                    (kind, location, reserved[0]),
                )
                return reserved[0]
        total, owned = db.execute(
            "SELECT coalesce(sum(bytes),0),coalesce(sum(CASE WHEN owner_id=? THEN bytes "
            "ELSE 0 END),0) FROM owned_resources",
            (owner,),
        ).fetchone()
        settings = self.store.settings
        if (
            total + size > settings.storage_global_bytes
            or owned + size > settings.storage_owner_bytes
        ):
            raise ResourceLimit("Repository/artifact storage allowance is exhausted.")
        identifier = uuid4().hex
        db.execute(
            "INSERT INTO owned_resources VALUES (?,?,?,?,?,?,?,?)",
            (
                identifier,
                owner,
                run_id,
                kind,
                location,
                size,
                time.time(),
                "reserved",
            ),
        )
        return identifier

    def settle_run(self, run_id, root):
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT * FROM owned_resources WHERE run_id=? AND kind='artifacts'", (run_id,)
            ).fetchall()
        for row in rows:
            path = root / row["location"]
            size = tree_bytes(path) if path.exists() else 0
            with self.store.connect() as db:
                db.execute(
                    "UPDATE owned_resources SET bytes=?,state='ready' WHERE id=?", (size, row["id"])
                )

    def complete(self, identifier):
        with self.store.connect() as db:
            db.execute("UPDATE owned_resources SET state='ready' WHERE id=?", (identifier,))

    def forget(self, identifier):
        with self.store.connect() as db:
            db.execute("DELETE FROM owned_resources WHERE id=?", (identifier,))


def own_artifact_directory(directory):
    context = CURRENT.get()
    if not context or not context.artifact_root:
        return
    root = context.artifact_root.absolute()
    directory = directory.absolute()
    if directory.parent == root:
        context.check()
        Resources(context.store).reserve(
            context.owner,
            "artifacts",
            directory.name,
            context.store.settings.job_artifact_bytes,
            run_id=context.run_id,
        )
        context.storage_roots[directory] = context.store.settings.job_artifact_bytes


def check_artifact_size(directory: Path, added=0, replacing: Path | None = None):
    context = CURRENT.get()
    if not context or not context.artifact_root:
        return
    root = context.artifact_root.absolute()
    relative = directory.absolute().relative_to(root)
    if not relative.parts:
        return
    owned = root / relative.parts[0]
    size = tree_bytes(owned) if owned.exists() else 0
    if replacing and replacing.exists():
        size -= replacing.stat().st_size
    if size + added > context.store.settings.job_artifact_bytes:
        raise ResourceLimit("This job reached its artifact storage allowance.")


def tree_bytes(root):
    physical_root(root)
    size = 0
    for path in root.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise ResourceLimit("Managed storage contains an unexpected link.")
        if path.is_file():
            size += path.stat().st_size
    return size
