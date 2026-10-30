"""One durable operation per owner/run, including uncertain provider outcomes."""

import hashlib
from uuid import uuid4

from fastapi import HTTPException

from app.api.storage import now
from app.publishing.contracts import Publication


class PublicationStore:
    def __init__(self, runs):
        self.runs = runs

    def get(self, owner, run_id):
        with self.runs.connect() as db:
            row = db.execute(
                "SELECT receipt FROM publications WHERE owner_id=? AND run_id=?", (owner, run_id)
            ).fetchone()
        return Publication.model_validate_json(row[0]) if row else None

    def prepare(self, owner, run_id, key, body, patch, repository):
        fingerprint = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        with self.runs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM publications WHERE owner_id=? AND (run_id=? OR key=?)",
                (owner, run_id, key),
            ).fetchall()
            if rows:
                if any(r["run_id"] != run_id or r["fingerprint"] != fingerprint for r in rows):
                    raise HTTPException(
                        409, "This publication or submission key has different inputs."
                    )
                return Publication.model_validate_json(rows[0]["receipt"])
            receipt = Publication(
                repository_id=body.repository_id,
                repository=repository,
                base_branch=body.base_branch,
                base_sha=patch.target.commit_sha,
                branch=f"upgrade/{uuid4().hex}",
                patch_sha256=patch.patch_sha256,
                notices_sha256=patch.notices_sha256,
                created_at=now(),
            )
            db.execute(
                "INSERT INTO publications VALUES (?,?,?,?,?)",
                (
                    owner,
                    run_id,
                    key,
                    fingerprint,
                    receipt.model_dump_json(),
                ),
            )
            return receipt

    def save(self, owner, run_id, receipt):
        with self.runs.connect() as db:
            db.execute(
                "UPDATE publications SET receipt=? WHERE owner_id=? AND run_id=?",
                (receipt.model_dump_json(), owner, run_id),
            )
