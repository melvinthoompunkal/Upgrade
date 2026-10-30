"""F1 failure, concurrency and populated-resource ownership boundaries."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from threading import Event
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from test_auth import ORIGIN, login, mutate, token_data
from test_auth import authenticated_app as authenticated_app

from app.api.artifacts import digest as artifact_digest
from app.api.migrations import SCHEMA_VERSION
from app.api.storage import RunStore
from app.auth.crypto import ReconnectRequired
from app.auth.github import GitHubClient, GitHubError, GitHubTokens, GitHubUser
from app.auth.store import SESSION_COOKIE, digest
from app.main import create_app
from app.selection_artifacts import ArtifactStore


@pytest.mark.parametrize("case", ["signed-out", "csrf", "non-ascii-csrf"])
def test_auth_rejections_are_sanitized_and_uncacheable(authenticated_app, case):
    client, _ = authenticated_app
    if case == "signed-out":
        response = client.get("/api/runs")
        assert response.status_code == 401
    else:
        login(client)
        headers = [
            (b"origin", ORIGIN.encode()),
            (b"x-csrf-token", b"\xff" if case == "non-ascii-csrf" else b"wrong"),
        ]
        response = client.post("/api/auth/logout", headers=headers)
        assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "payload", [None, {}, {"verifier": 3}, {"verifier": "x" * 43, "extra": True}]
)
def test_unusable_auth_transaction_is_consumed_without_provider_access(authenticated_app, payload):
    client, state = authenticated_app
    start = client.get("/api/auth/github/start", follow_redirects=False)
    transaction = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    auth = client.app.state.auth
    raw = b"invalid ciphertext" if payload is None else auth.cipher.encrypt("auth", payload)
    with auth.runs.connect() as db:
        db.execute("UPDATE auth_transactions SET verifier=?", (raw,))
    for _ in range(2):
        response = client.get(
            "/api/auth/github/callback",
            params={"state": transaction, "code": "fixture-code"},
            follow_redirects=False,
        )
        assert response.status_code == 400
        assert "ciphertext" not in response.text
    assert not state["requests"]
    with auth.runs.connect() as db:
        assert db.execute("SELECT count(*) FROM auth_transactions").fetchone()[0] == 0


@pytest.mark.parametrize(
    "change",
    ["missing", "string-expiry", "boolean-expiry", "infinite-expiry", "empty-token", "extra"],
)
def test_invalid_encrypted_credentials_require_reconnect_and_cancel_queue(
    authenticated_app, change
):
    client, _ = authenticated_app
    session, _ = login(client)
    run = mutate(client, "/api/runs", session, json={"kind": "meta_smoke"}).json()
    owner, auth = session["user"]["id"], client.app.state.auth
    with auth.runs.connect() as db:
        raw = db.execute("SELECT ciphertext FROM credentials WHERE user_id=?", (owner,)).fetchone()[
            0
        ]
        data = auth.cipher.decrypt(owner, raw)
        if change == "missing":
            del data["expires"]
        elif change == "string-expiry":
            data["expires"] = "private expiry detail"
        elif change == "boolean-expiry":
            data["expires"] = True
        elif change == "infinite-expiry":
            data["expires"] = float("inf")
        elif change == "empty-token":
            data["access_token"] = ""
        else:
            data["unexpected"] = "private credential detail"
        db.execute(
            "UPDATE credentials SET ciphertext=? WHERE user_id=?",
            (auth.cipher.encrypt(owner, data), owner),
        )

    class Provider:
        def refresh(self, token):
            pytest.fail("Malformed stored credentials must not reach GitHub.")

    with pytest.raises(ReconnectRequired) as error:
        auth.access_token(owner, Provider())
    assert "private" not in str(error.value)
    assert client.get("/api/session").json()["connection"] == "reconnect_required"
    assert client.get(f"/api/runs/{run['id']}").json()["outcome"] == "cancelled"


def test_failed_refresh_cannot_disconnect_a_new_sign_in(authenticated_app, monkeypatch):
    client, _ = authenticated_app
    session, _ = login(client)
    owner, auth = session["user"]["id"], client.app.state.auth
    with auth.runs.connect() as db:
        raw = db.execute("SELECT ciphertext FROM credentials WHERE user_id=?", (owner,)).fetchone()[
            0
        ]
        data = auth.cipher.decrypt(owner, raw)
        data["expires"] = 0
        db.execute(
            "UPDATE credentials SET ciphertext=? WHERE user_id=?",
            (auth.cipher.encrypt(owner, data), owner),
        )
    refreshing = Event()

    def reconnect():
        assert refreshing.wait(5)
        return auth.sign_in(
            GitHubUser(id=67, login="river-builds"),
            GitHubTokens(**token_data(access_token="fresh-authorization")),
        )

    class Provider:
        def refresh(self, token):
            refreshing.set()
            raise GitHubError("The old refresh failed.")

    disconnect = auth.disconnect
    with ThreadPoolExecutor(max_workers=1) as pool:
        fresh = pool.submit(reconnect)

        def delayed_cleanup(owner_id):
            # Reproduce a new authorization committing before stale failure cleanup.
            fresh.result(timeout=5)
            disconnect(owner_id)

        monkeypatch.setattr(auth, "disconnect", delayed_cleanup)
        with pytest.raises(ReconnectRequired):
            auth.access_token(owner, Provider())
        current = fresh.result(timeout=5)
    assert auth.principal(current).connected
    assert auth.access_token(owner, Provider()) == "fresh-authorization"


def test_disconnect_and_queued_cancellation_are_one_transaction(authenticated_app):
    client, _ = authenticated_app
    session, _ = login(client)
    run = mutate(client, "/api/runs", session, json={"kind": "meta_smoke"}).json()
    auth, owner = client.app.state.auth, session["user"]["id"]
    with auth.runs.connect() as db:
        db.execute(
            "CREATE TRIGGER fail_cancel BEFORE UPDATE OF summary ON runs "
            "BEGIN SELECT RAISE(ABORT, 'cancel fault'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        auth.disconnect(owner)
    assert client.get("/api/session").json()["connection"] == "connected"
    assert client.get(f"/api/runs/{run['id']}").json()["status"] == "queued"
    with auth.runs.connect() as db:
        assert (
            db.execute("SELECT count(*) FROM credentials WHERE user_id=?", (owner,)).fetchone()[0]
            == 1
        )


def test_disconnect_between_principal_and_enqueue_cannot_admit_a_job(
    authenticated_app, monkeypatch
):
    client, _ = authenticated_app
    session, _ = login(client)
    store = client.app.state.runs
    enqueue = store.enqueue

    def disconnect_first(*args, **kwargs):
        client.app.state.auth.disconnect(session["user"]["id"])
        return enqueue(*args, **kwargs)

    monkeypatch.setattr(store, "enqueue", disconnect_first)
    response = mutate(client, "/api/runs", session, json={"kind": "meta_smoke"})
    assert response.status_code == 409
    assert store.page(owner_id=session["user"]["id"])[0] == []


@pytest.mark.parametrize("case", ["denied", "outage", "bad-identity"])
def test_provider_login_failure_is_sanitized_and_not_replayed(authenticated_app, case):
    client, state = authenticated_app
    calls = []

    def provider(request):
        calls.append(request)
        if case == "bad-identity" and request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json=token_data())
        return httpx.Response(
            200 if case == "bad-identity" else 400 if case == "denied" else 503,
            json={"error": "private provider body"},
        )

    client.app.state.github = GitHubClient(
        client.app.state.settings, transport=httpx.MockTransport(provider)
    )
    start = client.get("/api/auth/github/start", follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    for _ in range(2):
        response = client.get(
            "/api/auth/github/callback",
            params={"state": state, "code": "private-code"},
            follow_redirects=False,
        )
        assert response.status_code == 400
        assert all(
            value not in response.text for value in ("private-code", "private provider body")
        )
    assert len(calls) == (2 if case == "bad-identity" else 1)
    assert not client.get("/api/session").json()["authenticated"]


@pytest.mark.parametrize("field", ["expires", "last_seen"])
def test_expired_sessions_are_removed_without_losing_credential_ownership(authenticated_app, field):
    client, _ = authenticated_app
    session, _ = login(client)
    token = client.cookies.get(SESSION_COOKIE)
    with client.app.state.runs.connect() as db:
        db.execute(f"UPDATE sessions SET {field}=0")
    assert client.get("/api/runs").status_code == 401
    with client.app.state.runs.connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM sessions WHERE token_hash=?", (digest(token),)
            ).fetchone()[0]
            == 0
        )
        assert (
            db.execute(
                "SELECT count(*) FROM credentials WHERE user_id=?", (session["user"]["id"],)
            ).fetchone()[0]
            == 1
        )


def test_failed_legacy_migration_rolls_back_and_can_be_retried(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""CREATE TABLE runs (id TEXT PRIMARY KEY, key TEXT UNIQUE,
            fingerprint TEXT, request TEXT, summary TEXT NOT NULL, report TEXT, digest TEXT);
            CREATE TABLE events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
            timestamp TEXT NOT NULL, stage TEXT NOT NULL, message TEXT NOT NULL);
            PRAGMA user_version=1;""")
        for index in range(2):
            db.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?)",
                (
                    str(index),
                    str(index),
                    "fingerprint",
                    "{}",
                    json.dumps(
                        {
                            "id": str(index),
                            "kind": "meta_smoke",
                            "title": f"Legacy {index}",
                            "status": "queued",
                            "stage": "inputs",
                            "created_at": "2026-10-28T12:00:00Z",
                            "updated_at": "2026-10-28T12:00:00Z",
                        }
                    ),
                    "duplicate/report.json",
                    "digest",
                ),
            )
        db.execute(
            "INSERT INTO events VALUES (?,?,?,?,?)",
            (17, "0", "timestamp", "inputs", "legacy event"),
        )
    with pytest.raises(sqlite3.IntegrityError):
        RunStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "owner_id" not in {row[1] for row in db.execute("PRAGMA table_info(runs)")}
        assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 2
        assert (
            db.execute("SELECT message FROM events WHERE sequence=17").fetchone()[0]
            == "legacy event"
        )
        db.execute("UPDATE runs SET report='other/report.json' WHERE id='1'")
    store = RunStore(path)
    assert store.row("0")["owner_id"] == "local-workspace"
    assert store.row("0")["digest"] == "digest"
    assert {row.title for row in store.page(owner_id="local-workspace")[0]} == {
        "Legacy 0",
        "Legacy 1",
    }
    assert store.page(owner_id="github-owner")[0] == []
    with store.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert (
            db.execute("SELECT message FROM events WHERE sequence=17").fetchone()[0]
            == "legacy event"
        )


@pytest.mark.parametrize("origin,secure", [(ORIGIN, False), ("https://127.0.0.1:5173", True)])
def test_auth_and_session_cookie_flags(authenticated_app, tmp_path, origin, secure):
    original, _ = authenticated_app
    settings = original.app.state.settings.model_copy(update={"app_base_url": origin})
    with TestClient(
        create_app(settings, data_root=tmp_path / "cookies", start_worker=False), base_url=origin
    ) as client:
        client.app.state.github = GitHubClient(
            settings, transport=original.app.state.github.transport
        )
        start = client.get("/api/auth/github/start", follow_redirects=False)
        transaction = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        callback = client.get(
            "/api/auth/github/callback",
            params={"state": transaction, "code": "cookie-fixture"},
            follow_redirects=False,
        )
        assert callback.status_code == 303
        for response, name, seconds in (
            (start, "upgrade_auth", 600),
            (callback, SESSION_COOKIE, 604800),
        ):
            cookies = SimpleCookie()
            for header in response.headers.get_list("set-cookie"):
                cookies.load(header)
            value = cookies[name]
            assert value["httponly"] and bool(value["secure"]) is secure
            assert value["samesite"] == "lax" and value["path"] == "/"
            assert value["domain"] == "" and value["max-age"] == str(seconds)


def test_populated_resources_are_scoped_to_their_owner(authenticated_app):
    client, state = authenticated_app
    first, _ = login(client)
    queued = mutate(client, "/api/runs", first, json={"kind": "meta_smoke"}).json()
    store = client.app.state.runs
    directory = client.app.state.artifact_root / queued["id"]
    outputs = ArtifactStore(directory, {})
    ref = {
        "repo_id": "source",
        "commit_sha": "a" * 40,
        "path": "private.py",
        "start_line": 1,
        "end_line": 1,
        "symbol": None,
    }
    outputs.json(
        "feature-card.json",
        {
            "feature_id": "private-feature",
            "name": "Private feature",
            "behavior": "Private owner behavior",
            "entry_points": [ref],
            "implementation": [ref],
            "dependencies": [],
            "source_tests": [],
            "acceptance_behaviors": ["Private behavior"],
            "transferable_slice": "Private slice",
            "excluded_behaviors": [],
            "license_spdx": None,
            "license_refs": [],
            "evidence_status": "partial",
        },
    )
    excerpt = "private_owner_code\n"
    outputs.json(
        "evidence-reads-source.json",
        {
            "repo_id": "source",
            "commit_sha": "a" * 40,
            "windows": [
                {
                    "path": "private.py",
                    "start_line": 1,
                    "end_line": 1,
                    "text": excerpt,
                    "sha256": artifact_digest(excerpt.encode()),
                }
            ],
        },
    )
    outputs.json(
        "report.json",
        {"status": "passed", "goal": "Private owner goal", "artifact_hashes": dict(outputs.hashes)},
    )
    store.attach(
        queued["id"],
        directory.name + "/report.json",
        artifact_digest((directory / "report.json").read_bytes()),
    )
    store.transition(
        queued["id"],
        stage="complete",
        message="Private owner event",
        status="finished",
        outcome="passed",
    )
    base = f"/api/runs/{queued['id']}"
    detail = client.get(base).json()
    assert detail["references"] and detail["artifacts"]
    paths = [base, base + "/status", base + "/events", base + "/artifacts"]
    paths.extend(base + "/references/" + item["id"] for item in detail["references"])
    for item in detail["artifacts"]:
        paths.extend(
            [base + "/artifacts/" + item["id"], base + "/artifacts/" + item["id"] + "/text"]
        )
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert (
        client.get(base + "/references/" + detail["references"][0]["id"]).json()["text"] == excerpt
    )
    state.update(id=312, login="separate-owner")
    other, _ = login(client)
    for path in paths:
        response = client.get(path)
        assert response.status_code == 404
        assert response.json() == client.get(path.replace(queued["id"], "unknown-run")).json()
        assert "private_owner_code" not in response.text
    assert client.get("/api/runs", params={"q": "Private"}).json()["items"] == []
    repeated = mutate(client, "/api/runs", other, json={"kind": "meta_smoke"})
    assert repeated.status_code == 202 and repeated.json()["id"] != queued["id"]


def test_future_database_schema_is_rejected_without_downgrade(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(RuntimeError, match="newer application"):
        RunStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 99
        assert (
            db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 0
        )
