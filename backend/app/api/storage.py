"""A bounded SQLite queue and ordered events for one local worker."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from app.api.contracts import Event, RunRequest, RunSummary


def now() -> str:
    return datetime.now(UTC).isoformat()


class RunStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, key TEXT UNIQUE, fingerprint TEXT,
                    request TEXT, summary TEXT NOT NULL, report TEXT, digest TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL, stage TEXT NOT NULL, message TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS unique_report ON runs(report)
                    WHERE report IS NOT NULL;
                PRAGMA user_version=1;
            """)

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

    def enqueue(self, request: RunRequest, key: str) -> RunSummary:
        body = request.model_dump_json()
        fingerprint = hashlib.sha256(body.encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM runs WHERE key=?", (key,)).fetchone()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise HTTPException(
                        409, "This submission key already belongs to different inputs."
                    )
                return RunSummary.model_validate_json(previous["summary"])
            active = sum(
                json.loads(row[0])["status"] != "finished"
                for row in db.execute("SELECT summary FROM runs")
            )
            if active >= 4:
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
                "INSERT INTO runs(id,key,fingerprint,request,summary) VALUES (?,?,?,?,?)",
                (summary.id, key, fingerprint, body, summary.model_dump_json()),
            )
            self.event(db, summary.id, "inputs", "Queued for the local worker.")
            return summary

    def row(self, run_id: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Run not found.")
        return row

    def summaries(self) -> list[RunSummary]:
        with self.connect() as db:
            rows = db.execute("SELECT summary FROM runs").fetchall()
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

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM runs WHERE request IS NOT NULL ORDER BY rowid"
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

    def attach(self, run_id: str, relative: str, digest: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE runs SET report=?,digest=? WHERE id=?", (relative, digest, run_id))

    def import_report(self, summary: RunSummary, relative: str, digest: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO runs(id,summary,report,digest) VALUES (?,?,?,?)",
                (summary.id, summary.model_dump_json(), relative, digest),
            )

    def events(self, run_id: str, after: int) -> list[Event]:
        self.row(run_id)
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT 100",
                (run_id, after),
            ).fetchall()
        return [Event(**dict(row)) for row in rows]
