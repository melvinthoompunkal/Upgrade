"""Separate accounting keeps charges durable across auth transaction rollback."""

import sqlite3
from contextlib import contextmanager


class UsageStore:
    def __init__(self, path):
        self.path = path
        if path.is_symlink() or path.is_junction():
            raise ValueError("Usage storage must be a regular local file.")
        with self.connect() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] > 1:
                raise RuntimeError("Usage storage requires a newer application version.")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS resource_usage (
                day TEXT NOT NULL, owner_id TEXT NOT NULL, resource TEXT NOT NULL,
                amount INTEGER NOT NULL, PRIMARY KEY(day,owner_id,resource))""")
            db.execute("""CREATE TABLE IF NOT EXISTS job_metrics (
                run_id TEXT NOT NULL, resource TEXT NOT NULL, amount INTEGER NOT NULL,
                owner_id TEXT NOT NULL, PRIMARY KEY(run_id,resource))""")
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()
