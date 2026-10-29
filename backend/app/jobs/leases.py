"""Transactional leases and cancellation. Expired jobs are settled, never reclaimed."""

import time

from fastapi import HTTPException

from app.api.contracts import RunSummary
from app.jobs.control import ExecutionStopped


class JobLeases:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def check_in(db, run_id, token, epoch=None):
        row = db.execute("SELECT * FROM job_leases WHERE run_id=?", (run_id,)).fetchone()
        if not row or row["token"] != token or row["expires"] <= time.time():
            return "lease_lost"
        if row["cancel_code"]:
            return row["cancel_code"]
        if epoch is not None:
            user = db.execute(
                "SELECT connected,access_epoch FROM users WHERE id=(SELECT owner_id FROM runs "
                "WHERE id=?)",
                (run_id,),
            ).fetchone()
            if not user or not user["connected"] or user["access_epoch"] != epoch:
                return "access_revoked"
        return None

    def check(self, run_id, token, epoch=None):
        with self.store.connect() as db:
            return self.check_in(db, run_id, token, epoch)

    def heartbeat(self, run_id, token, seconds):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            code = self.check_in(db, run_id, token)
            if code == "lease_lost":
                raise ExecutionStopped(code)
            db.execute(
                "UPDATE job_leases SET heartbeat=?,expires=? WHERE run_id=? AND token=?",
                (time.time(), time.time() + seconds, run_id, token),
            )

    def cancel(self, run_id, owner):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT summary FROM runs WHERE id=? AND owner_id=?", (run_id, owner)
            ).fetchone()
            if not row:
                raise HTTPException(404, "Run not found.")
            summary = RunSummary.model_validate_json(row[0])
            if summary.status == "finished":
                return summary
            if summary.status == "queued":
                return self.settle_in(db, summary, "cancelled")
            lease = db.execute(
                "SELECT expires FROM job_leases WHERE run_id=?", (run_id,)
            ).fetchone()
            if not lease or lease[0] <= time.time():
                return self.settle_in(db, summary, "cancelled")
            if summary.cancel_requested:
                return summary
            from app.api.storage import now

            summary = summary.model_copy(
                update={
                    "cancel_requested": True,
                    "revision": summary.revision + 1,
                    "updated_at": now(),
                }
            )
            db.execute("UPDATE runs SET summary=? WHERE id=?", (summary.model_dump_json(), run_id))
            db.execute(
                "UPDATE job_leases SET cancel_code=coalesce(cancel_code,'cancelled') "
                "WHERE run_id=?",
                (run_id,),
            )
            self.store.event(
                db, run_id, summary.stage, "Cancellation requested; stopping owned work."
            )
            return summary

    def settle_in(self, db, summary, code):
        from app.api.storage import now

        summary = summary.model_copy(
            update={
                "status": "finished",
                "stage": "complete",
                "outcome": code,
                "updated_at": now(),
                "revision": summary.revision + 1,
            }
        )
        db.execute("UPDATE runs SET summary=? WHERE id=?", (summary.model_dump_json(), summary.id))
        db.execute("DELETE FROM job_leases WHERE run_id=?", (summary.id,))
        db.execute(
            "DELETE FROM owned_resources WHERE run_id=? AND kind='job-reservation'", (summary.id,)
        )
        self.store.event(db, summary.id, "complete", "Execution stopped; no work was replayed.")
        return summary

    def recover(self, *, abandoned_worker=False):
        """Caller must own worker + workspace locks before recovering abandoned work."""
        recovered = []
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT r.summary,l.expires FROM runs r LEFT JOIN job_leases l ON r.id=l.run_id "
                "WHERE json_extract(r.summary,'$.status')='running'"
            ).fetchall()
            for row in rows:
                if abandoned_worker or row["expires"] is None or row["expires"] <= time.time():
                    summary = RunSummary.model_validate_json(row["summary"])
                    self.settle_in(db, summary, "interrupted")
                    recovered.append(summary.id)
        return recovered
