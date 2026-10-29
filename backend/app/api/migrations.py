"""Transactional, forward-only migrations. Old local runs never acquire a web user."""

import sqlite3

LOCAL_OWNER = "local-workspace"
SCHEMA_VERSION = 4


def migrate(db: sqlite3.Connection) -> None:
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError("The run database requires a newer application version.")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("BEGIN IMMEDIATE")
    db.execute("""CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY, key TEXT, fingerprint TEXT, request TEXT,
        summary TEXT NOT NULL, report TEXT, digest TEXT,
        owner_id TEXT NOT NULL DEFAULT 'local-workspace', UNIQUE(owner_id,key)
    )""")
    if "owner_id" not in {row[1] for row in db.execute("PRAGMA table_info(runs)")}:
        db.execute("ALTER TABLE runs RENAME TO legacy_runs")
        db.execute("""CREATE TABLE runs (
            id TEXT PRIMARY KEY, key TEXT, fingerprint TEXT, request TEXT,
            summary TEXT NOT NULL, report TEXT, digest TEXT,
            owner_id TEXT NOT NULL DEFAULT 'local-workspace', UNIQUE(owner_id,key)
        )""")
        db.execute("""INSERT INTO runs(id,key,fingerprint,request,summary,report,digest)
            SELECT id,key,fingerprint,request,summary,report,digest FROM legacy_runs""")
        db.execute("DROP TABLE legacy_runs")
    statements = (
        """CREATE TABLE IF NOT EXISTS events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
            timestamp TEXT NOT NULL, stage TEXT NOT NULL, message TEXT NOT NULL)""",
        """CREATE UNIQUE INDEX IF NOT EXISTS unique_report ON runs(report)
            WHERE report IS NOT NULL""",
        """CREATE INDEX IF NOT EXISTS owner_history ON runs(
            owner_id, json_extract(summary,'$.created_at') DESC, id DESC)""",
        """CREATE INDEX IF NOT EXISTS queue_status ON runs(
            json_extract(summary,'$.status'), owner_id)""",
        "CREATE INDEX IF NOT EXISTS run_events ON events(run_id,sequence)",
        """CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, github_id INTEGER NOT NULL UNIQUE,
            login TEXT NOT NULL, name TEXT, connected INTEGER NOT NULL DEFAULT 1)""",
        """CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf TEXT NOT NULL,
            created REAL NOT NULL, expires REAL NOT NULL, last_seen REAL NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS user_sessions ON sessions(user_id)",
        """CREATE TABLE IF NOT EXISTS auth_transactions (
            state_hash TEXT PRIMARY KEY, browser_hash TEXT NOT NULL,
            verifier BLOB NOT NULL, destination TEXT NOT NULL, expires REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS credentials (
            user_id TEXT PRIMARY KEY, ciphertext BLOB NOT NULL, updated REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS repository_sync (
            owner_id TEXT PRIMARY KEY, revision TEXT NOT NULL, state TEXT NOT NULL,
            updated REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS repository_cache (
            owner_id TEXT NOT NULL, github_id INTEGER NOT NULL, revision TEXT NOT NULL,
            installation_id INTEGER NOT NULL, full_name TEXT NOT NULL, data TEXT NOT NULL,
            PRIMARY KEY(owner_id,github_id))""",
        """CREATE INDEX IF NOT EXISTS repository_picker
            ON repository_cache(owner_id,revision,full_name,github_id)""",
    )
    for statement in statements:
        db.execute(statement)
    if "access_epoch" not in {row[1] for row in db.execute("PRAGMA table_info(users)")}:
        db.execute("ALTER TABLE users ADD COLUMN access_epoch INTEGER NOT NULL DEFAULT 0")
    db.execute("""CREATE TABLE IF NOT EXISTS github_webhook_deliveries (
        delivery_id TEXT PRIMARY KEY, body_digest TEXT NOT NULL, event TEXT NOT NULL,
        received REAL NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS repository_snapshots (
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, github_id INTEGER NOT NULL,
        installation_id INTEGER NOT NULL, descriptor TEXT NOT NULL, created REAL NOT NULL)""")
    db.execute("""CREATE INDEX IF NOT EXISTS owner_snapshots
        ON repository_snapshots(owner_id,github_id)""")
    db.execute("""CREATE TABLE IF NOT EXISTS run_authorizations (
        run_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, access_epoch INTEGER NOT NULL)""")
    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
