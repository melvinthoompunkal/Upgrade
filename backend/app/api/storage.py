"""A bounded SQLite queue and ordered events for one local worker."""

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from app.api.contracts import Event, RunRequest, RunSummary
from app.api.migrations import LOCAL_OWNER, migrate
from app.selection_artifacts import prepare_directory


def now() -> str:
    return datetime.now(UTC).isoformat()


class RunStore:
    def __init__(self, path: Path):
        self.path = path
        prepare_directory(path.parent)
        if path.is_symlink() or path.is_junction():
            raise ValueError("The run database must be a regular local file.")
        with self.connect() as db:
            migrate(db)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def event(db, run_id: str, stage: str, message: str) -> None:
        db.execute(
            "INSERT INTO events(run_id,timestamp,stage,message) VALUES (?,?,?,?)",
            (run_id, now(), stage, message),
        )

    def enqueue(
        self,
        request: RunRequest,
        key: str,
        *,
        owner_id: str = LOCAL_OWNER,
        require_connected: bool = False,
    ) -> RunSummary:
        body = request.model_dump_json()
        fingerprint = hashlib.sha256(body.encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if require_connected:
                connected = db.execute(
                    "SELECT connected FROM users WHERE id=?", (owner_id,)
                ).fetchone()
                if not connected or not connected[0]:
                    raise HTTPException(409, "Reconnect GitHub before starting another run.")
            previous = db.execute(
                "SELECT * FROM runs WHERE owner_id=? AND key=?", (owner_id, key)
            ).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise HTTPException(
                        409, "This submission key already belongs to different inputs."
                    )
                return RunSummary.model_validate_json(previous["summary"])
            active, owned = db.execute(
                """SELECT count(*),coalesce(sum(owner_id=?),0) FROM runs
                WHERE json_extract(summary,'$.status')!='finished'""",
                (owner_id,),
            ).fetchone()
            if active >= 16 or owned >= 4:
                raise HTTPException(429, "The local queue is full. Wait for a run to finish.")
            title = getattr(request, "goal", None) or {
                "meta_smoke": "Meta connectivity check",
                "local_baseline": "Working-file baseline",
                "pinned_baseline": "Committed target baseline",
            }.get(request.kind, "Source analysis")
            summary = RunSummary(
                id=uuid4().hex,
                kind=request.kind,
                title=title[:100],
                status="queued",
                stage="inputs",
                created_at=now(),
                updated_at=now(),
            )
            db.execute(
                "INSERT INTO runs(id,key,fingerprint,request,summary,owner_id) "
                "VALUES (?,?,?,?,?,?)",
                (summary.id, key, fingerprint, body, summary.model_dump_json(), owner_id),
            )
            self.event(db, summary.id, "inputs", "Queued for the local worker.")
            return summary

    def row(self, run_id: str, *, owner_id: str | None = None):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None or (owner_id is not None and row["owner_id"] != owner_id):
            raise HTTPException(404, "Run not found.")
        return row

    def page(
        self,
        *,
        owner_id: str,
        cursor: int = 0,
        query: str = "",
        kind: str | None = None,
        limit: int = 30,
    ) -> tuple[list[RunSummary], int | None]:
        conditions, values = ["owner_id=?"], [owner_id]
        if query:
            conditions.append(
                "instr(lower(json_extract(summary,'$.title')||' '||id||' '||"
                "coalesce(json_extract(summary,'$.outcome'),'')),lower(?))>0"
            )
            values.append(query)
        if kind:
            conditions.append("json_extract(summary,'$.kind')=?")
            values.append(kind)
        with self.connect() as db:
            rows = db.execute(
                "SELECT summary FROM runs WHERE "
                + " AND ".join(conditions)
                + " ORDER BY json_extract(summary,'$.created_at') DESC,id DESC LIMIT ? OFFSET ?",
                (*values, limit + 1, cursor),
            ).fetchall()
        return (
            [RunSummary.model_validate_json(row[0]) for row in rows[:limit]],
            cursor + limit if len(rows) > limit else None,
        )

    def summaries(self, *, status: str | None = None) -> list[RunSummary]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT summary FROM runs"
                + (" WHERE json_extract(summary,'$.status')=?" if status else ""),
                (status,) if status else (),
            ).fetchall()
        return sorted(
            (RunSummary.model_validate_json(row[0]) for row in rows),
            key=lambda item: item.created_at,
            reverse=True,
        )

    def transition(self, run_id: str, *, stage: str, message: str, **changes) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT summary FROM runs WHERE id=?", (run_id,)).fetchone()
            summary = RunSummary.model_validate_json(row[0])
            updated = summary.model_copy(
                update={
                    **changes,
                    "stage": stage,
                    "updated_at": now(),
                    "revision": summary.revision + 1,
                }
            )
            db.execute("UPDATE runs SET summary=? WHERE id=?", (updated.model_dump_json(), run_id))
            self.event(db, run_id, stage, message)

    def claim(self, *, mode: str = "local"):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            owner_filter = (
                "owner_id=?"
                if mode == "local"
                else (
                    "owner_id!=? AND EXISTS (SELECT 1 FROM users "
                    "WHERE users.id=runs.owner_id AND users.connected=1)"
                )
            )
            rows = db.execute(
                "SELECT * FROM runs WHERE request IS NOT NULL "
                "AND json_extract(summary,'$.status')='queued' AND "
                + owner_filter
                + " ORDER BY rowid LIMIT 1",
                (LOCAL_OWNER,),
            ).fetchall()
            for row in rows:
                summary = RunSummary.model_validate_json(row["summary"])
                if summary.status != "queued":
                    continue
                summary = summary.model_copy(
                    update={
                        "status": "running",
                        "updated_at": now(),
                        "revision": summary.revision + 1,
                    }
                )
                db.execute(
                    "UPDATE runs SET summary=? WHERE id=?", (summary.model_dump_json(), row["id"])
                )
                self.event(
                    db, row["id"], "inputs", "Worker started; execution checks are authoritative."
                )
                return row
        return None

    def cancel_queued(self, owner_id: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.cancel_queued_in(db, owner_id)

    def cancel_queued_in(self, db: sqlite3.Connection, owner_id: str) -> None:
        """Cancel under the caller's authorization transaction without releasing its lock."""
        rows = db.execute(
            "SELECT id,summary FROM runs WHERE owner_id=? "
            "AND json_extract(summary,'$.status')='queued'",
            (owner_id,),
        ).fetchall()
        for row in rows:
            summary = RunSummary.model_validate_json(row["summary"])
            updated = summary.model_copy(
                update={
                    "status": "finished",
                    "stage": "complete",
                    "outcome": "cancelled",
                    "updated_at": now(),
                    "revision": summary.revision + 1,
                }
            )
            db.execute(
                "UPDATE runs SET summary=? WHERE id=?", (updated.model_dump_json(), row["id"])
            )
            self.event(db, row["id"], "complete", "Cancelled after GitHub disconnect.")

    def attach(self, run_id: str, relative: str, digest: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE runs SET report=?,digest=? WHERE id=?", (relative, digest, run_id))

    def import_report(self, summary: RunSummary, relative: str, digest: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO runs(id,summary,report,digest) VALUES (?,?,?,?)",
                (summary.id, summary.model_dump_json(), relative, digest),
            )

    def events(self, run_id: str, after: int, *, owner_id: str | None = None) -> list[Event]:
        self.row(run_id, owner_id=owner_id)
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT 100",
                (run_id, after),
            ).fetchall()
        return [Event(**dict(row)) for row in rows]
