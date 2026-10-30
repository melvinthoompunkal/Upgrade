"""Transactional, forward-only migrations. Old local runs never acquire a web user."""

import sqlite3

LOCAL_OWNER = "local-workspace"
SCHEMA_VERSION = 10


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
    db.execute("""CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, descriptor TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS owner_projects ON projects(owner_id,id)")
    db.execute("""CREATE TABLE IF NOT EXISTS catalogs (
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, project_id TEXT NOT NULL,
        identity TEXT NOT NULL, cache_key TEXT NOT NULL)""")
    db.execute("CREATE INDEX IF NOT EXISTS catalog_cache ON catalogs(owner_id,cache_key)")
    db.execute("""CREATE TABLE IF NOT EXISTS catalog_revisions (
        catalog_id TEXT NOT NULL, revision INTEGER NOT NULL, run_id TEXT NOT NULL UNIQUE,
        metadata TEXT NOT NULL, trace_ids TEXT NOT NULL,
        PRIMARY KEY(catalog_id,revision))""")
    db.execute("""CREATE TABLE IF NOT EXISTS catalog_features (
        catalog_id TEXT NOT NULL, revision INTEGER NOT NULL, feature_id TEXT NOT NULL,
        name TEXT NOT NULL, category TEXT NOT NULL, evidence_status TEXT NOT NULL,
        summary TEXT NOT NULL, detail TEXT NOT NULL, sha256 TEXT NOT NULL,
        search_text TEXT NOT NULL,
        PRIMARY KEY(catalog_id,revision,feature_id))""")
    db.execute("""CREATE INDEX IF NOT EXISTS catalog_page ON catalog_features(
        catalog_id,revision,name,feature_id)""")
    db.execute("""CREATE TABLE IF NOT EXISTS catalog_operations (
        owner_id TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT NOT NULL,
        catalog_id TEXT NOT NULL, revision INTEGER NOT NULL, cache_hit INTEGER NOT NULL,
        PRIMARY KEY(owner_id,key))""")
    db.execute("CREATE TABLE IF NOT EXISTS catalog_cursor_key (id INTEGER PRIMARY KEY, key BLOB)")
    db.execute("INSERT OR IGNORE INTO catalog_cursor_key VALUES (1,randomblob(32))")
    db.execute("""CREATE TABLE IF NOT EXISTS relevance_assessments (
        id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, metadata TEXT NOT NULL,
        run_id TEXT NOT NULL UNIQUE)""")
    db.execute("CREATE INDEX IF NOT EXISTS owner_assessments ON relevance_assessments(owner_id,id)")
    db.execute("""CREATE TABLE IF NOT EXISTS relevance_features (
        assessment_id TEXT NOT NULL, feature_id TEXT NOT NULL,
        relevance_status TEXT NOT NULL, relevance_score INTEGER NOT NULL,
        fit_status TEXT NOT NULL, summary TEXT NOT NULL, detail TEXT NOT NULL,
        sha256 TEXT NOT NULL, PRIMARY KEY(assessment_id,feature_id))""")
    db.execute("""CREATE INDEX IF NOT EXISTS relevance_page ON relevance_features(
        assessment_id,relevance_status,relevance_score DESC,feature_id)""")
    db.execute("""CREATE TABLE IF NOT EXISTS relevance_operations (
        owner_id TEXT NOT NULL, key TEXT NOT NULL, fingerprint TEXT NOT NULL,
        assessment_id TEXT NOT NULL, PRIMARY KEY(owner_id,key))""")
    db.execute("""CREATE INDEX IF NOT EXISTS catalog_category_page ON catalog_features(
        catalog_id,revision,category,name,feature_id)""")
    from app.jobs.schema import migrate_jobs

    migrate_jobs(db)
    db.execute("""CREATE TABLE IF NOT EXISTS publications (
        owner_id TEXT NOT NULL, run_id TEXT NOT NULL, key TEXT NOT NULL,
        fingerprint TEXT NOT NULL, receipt TEXT NOT NULL,
        PRIMARY KEY(owner_id,run_id), UNIQUE(owner_id,key))""")
    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
