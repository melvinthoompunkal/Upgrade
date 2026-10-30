"""F6 tables, kept separate from the historical migrations."""


def migrate_jobs(db):
    statements = (
        """CREATE TABLE IF NOT EXISTS worker_status (
            id INTEGER PRIMARY KEY CHECK(id=1), worker TEXT NOT NULL,
            heartbeat REAL NOT NULL, available INTEGER NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS job_leases (
            run_id TEXT PRIMARY KEY, token TEXT NOT NULL, worker TEXT NOT NULL,
            expires REAL NOT NULL, heartbeat REAL NOT NULL, cancel_code TEXT)""",
        """CREATE TABLE IF NOT EXISTS owner_schedule (
            owner_id TEXT PRIMARY KEY, last_claim REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS owned_resources (
            id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, run_id TEXT,
            kind TEXT NOT NULL, location TEXT NOT NULL UNIQUE, bytes INTEGER NOT NULL,
            created REAL NOT NULL, state TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS owner_resources ON owned_resources(owner_id,created)",
        """CREATE TABLE IF NOT EXISTS model_consents (
            owner_id TEXT NOT NULL, identity TEXT NOT NULL, policy TEXT NOT NULL,
            created REAL NOT NULL, PRIMARY KEY(owner_id,identity,policy))""",
        """CREATE TABLE IF NOT EXISTS cleanup_receipts (
            id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, created REAL NOT NULL,
            state TEXT NOT NULL, deleted_count INTEGER NOT NULL DEFAULT 0)""",
        """CREATE TABLE IF NOT EXISTS published_artifacts (
            run_id TEXT NOT NULL, directory TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
            PRIMARY KEY(run_id,directory,path))""",
    )
    for statement in statements:
        db.execute(statement)
