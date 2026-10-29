"""Real session/database/API boundaries with controlled GitHub HTTP responses."""

import base64
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.api.contracts import CheckRequest
from app.api.storage import RunStore
from app.auth.crypto import CredentialCipher, ReconnectRequired
from app.auth.github import GitHubClient, GitHubTokens, GitHubUser
from app.auth.store import AUTH_COOKIE, SESSION_COOKIE, AuthStore
from app.config import Settings
from app.main import create_app

ORIGIN = "http://127.0.0.1:5173"
ACCESS, REFRESH = "private-access-fixture", "private-refresh-fixture"


def token_data(**changes):
    return {
        "access_token": ACCESS,
        "refresh_token": REFRESH,
        "expires_in": 3600,
        "refresh_token_expires_in": 86400,
        "token_type": "bearer",
        **changes,
    }


@pytest.fixture
def authenticated_app(tmp_path):
    settings = Settings(
        _env_file=None,
        model_api_key="fixture-model-key",
        auth_mode="github",
        github_client_id="fixture-client-id",
        github_client_secret="fixture-client-secret",
        github_app_slug="upgrade-fixture",
        operator_github_ids=(67, 312),
        token_encryption_keys=Fernet.generate_key().decode(),
    )
    state = {"id": 67, "login": "river-builds", "requests": []}

    def handler(request):
        state["requests"].append(request)
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json=token_data())
        if request.url.path == "/user":
            return httpx.Response(
                200, json={"id": state["id"], "login": state["login"], "name": None}
            )
        return httpx.Response(404)

    with TestClient(
        create_app(settings, data_root=tmp_path, start_worker=False), base_url=ORIGIN
    ) as client:
        client.app.state.github = GitHubClient(settings, transport=httpx.MockTransport(handler))
        client.app.state.worker.available = True
        yield client, state
        client.app.state.worker.available = False


def login(client):
    response = client.get("/api/auth/github/start", follow_redirects=False)
    assert response.status_code == 302
    params = parse_qs(urlsplit(response.headers["location"]).query)
    response = client.get(
        "/api/auth/github/callback",
        params={"state": params["state"][0], "code": "single-use-fixture-code"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client.get("/api/session").json(), params


def mutate(client, path, session, **kwargs):
    return client.post(
        path,
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": session["csrf_token"],
            "Idempotency-Key": "owner-submission-key-fixture",
        },
        **kwargs,
    )


def test_pkce_single_use_session_and_secret_free_dto(authenticated_app):
    client, state = authenticated_app
    session, params = login(client)
    body = parse_qs(state["requests"][0].content.decode())
    challenge = base64.urlsafe_b64encode(hashlib.sha256(body["code_verifier"][0].encode()).digest())
    assert params["code_challenge"] == [challenge.rstrip(b"=").decode()]
    assert params["code_challenge_method"] == ["S256"]
    assert params["scope"] == ["offline_access"]
    assert session["authenticated"] and session["connection"] == "connected"
    assert session["user"]["login"] == state["login"]
    response = client.get(
        "/api/auth/github/callback",
        params={"state": params["state"][0], "code": "single-use-fixture-code"},
    )
    assert response.status_code == 400 and len(state["requests"]) == 2
    raw = json.dumps(session)
    assert all(secret not in raw for secret in (ACCESS, REFRESH, "fixture-client-secret"))
    with client.app.state.runs.connect() as db:
        credential = db.execute("SELECT ciphertext FROM credentials").fetchone()[0]
        hashed = db.execute("SELECT token_hash FROM sessions").fetchone()[0]
    assert ACCESS.encode() not in credential and REFRESH.encode() not in credential
    assert hashed != client.cookies.get(SESSION_COOKIE)
    assert client.get("/api/session").headers["cache-control"] == "no-store"


@pytest.mark.parametrize("case", ["wrong-browser", "wrong-state", "expired", "denied", "no-code"])
def test_invalid_authorization_never_contacts_github(authenticated_app, case):
    client, state = authenticated_app
    response = client.get("/api/auth/github/start", follow_redirects=False)
    transaction = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    if case == "wrong-browser":
        client.cookies.set(AUTH_COOKIE, "different-browser", domain="127.0.0.1", path="/")
    if case == "wrong-state":
        transaction = "not-the-state"
    if case == "expired":
        with client.app.state.runs.connect() as db:
            db.execute("UPDATE auth_transactions SET expires=0")
    response = client.get(
        "/api/auth/github/callback",
        params={
            "state": transaction,
            "code": "" if case == "no-code" else "fixture-code",
            "error": "access_denied" if case == "denied" else "",
        },
    )
    assert response.status_code == 400
    assert not state["requests"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/runs",
        "/api/runs/guess",
        "/api/runs/guess/events",
        "/api/runs/guess/artifacts",
        "/api/runs/guess/artifacts/id",
        "/api/runs/guess/artifacts/id/text",
        "/api/runs/guess/references/id",
        "/api/targets",
    ],
)
def test_all_private_api_surfaces_require_session(authenticated_app, path):
    client, _ = authenticated_app
    assert client.get(path).status_code == 401


@pytest.mark.parametrize(
    "return_to",
    [
        "//evil.example",
        "https://evil.example",
        "/\\evil",
        "/runs/../outside",
        "/%2fevil.example",
        "/%0d%0aevil",
    ],
)
def test_open_redirects_are_rejected(authenticated_app, return_to):
    client, _ = authenticated_app
    assert client.get("/api/auth/github/start", params={"return_to": return_to}).status_code == 422


def test_csrf_logout_rotation_expiry_and_account_switch(authenticated_app):
    client, state = authenticated_app
    first, _ = login(client)
    old_cookie = client.cookies.get(SESSION_COOKIE)
    for headers in (
        {},
        {"Origin": "https://evil.example", "X-CSRF-Token": first["csrf_token"]},
        {"Origin": ORIGIN, "X-CSRF-Token": "incorrect"},
    ):
        assert client.post("/api/auth/logout", headers=headers).status_code == 403
    state.update(id=908, login="forest-maintainer")
    second, _ = login(client)
    assert second["user"]["id"] != first["user"]["id"]
    assert client.app.state.auth.principal(old_cookie) is None
    assert mutate(client, "/api/auth/logout", second).status_code == 200
    assert not client.get("/api/session").json()["authenticated"]
    login(client)
    with client.app.state.runs.connect() as db:
        db.execute("UPDATE sessions SET last_seen=0")
    assert client.get("/api/runs").status_code == 401


def test_numeric_identity_survives_renamed_login(authenticated_app):
    client, state = authenticated_app
    first, _ = login(client)
    state["login"] = "renamed-river"
    second, _ = login(client)
    assert first["user"]["id"] == second["user"]["id"]
    assert second["user"]["login"] == "renamed-river"


def test_two_owners_cannot_read_each_others_runs_or_artifacts(authenticated_app):
    client, state = authenticated_app
    first, _ = login(client)
    run = mutate(client, "/api/runs", first, json={"kind": "meta_smoke"}).json()
    assert client.get("/api/runs").json()["items"][0]["id"] == run["id"]
    state.update(id=312, login="separate-owner")
    second, _ = login(client)
    assert client.get("/api/runs").json()["items"] == []
    for suffix in (
        "",
        "/events",
        "/artifacts",
        "/artifacts/id",
        "/artifacts/id/text",
        "/references/id",
    ):
        assert client.get(f"/api/runs/{run['id']}{suffix}").status_code == 404
    # Identical keys have separate meaning for each owner.
    other = mutate(client, "/api/runs", second, json={"kind": "meta_smoke"})
    assert other.status_code == 202 and other.json()["id"] != run["id"]
    # Local mode can never claim either signed-in owner's jobs.
    assert client.app.state.runs.claim() is None


def test_disconnect_removes_credentials_and_cancels_queued_work(authenticated_app):
    client, _ = authenticated_app
    session, _ = login(client)
    run = mutate(client, "/api/runs", session, json={"kind": "meta_smoke"}).json()
    assert mutate(client, "/api/auth/disconnect", session).status_code == 200
    assert client.get(f"/api/runs/{run['id']}").json()["outcome"] == "cancelled"
    assert client.get("/api/session").json()["connection"] == "reconnect_required"
    assert mutate(client, "/api/runs", session, json={"kind": "meta_smoke"}).status_code == 409
    with pytest.raises(ReconnectRequired):
        client.app.state.auth.access_token(session["user"]["id"], client.app.state.github)


def test_legacy_migration_preserves_rows_and_local_ownership(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""CREATE TABLE runs (id TEXT PRIMARY KEY, key TEXT UNIQUE,
            fingerprint TEXT, request TEXT, summary TEXT NOT NULL, report TEXT, digest TEXT);
            PRAGMA user_version=1;""")
        db.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?)",
            ("old", "submission", "digest", "{}", '{"status":"queued"}', "old/report.json", "hash"),
        )
    store = RunStore(path)
    row = store.row("old")
    assert row["owner_id"] == "local-workspace"
    assert row["report"] == "old/report.json" and row["digest"] == "hash"
    assert store.page(owner_id="new-user")[0] == []
    with pytest.raises(Exception) as error:
        store.row("old", owner_id="new-user")
    assert error.value.status_code == 404


def test_refresh_is_atomic_and_encryption_keys_can_rotate(tmp_path):
    old_key, new_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    store = RunStore(tmp_path / "runs.sqlite3")
    auth = AuthStore(store, CredentialCipher(old_key))
    session = auth.sign_in(
        GitHubUser(id=12, login="tide", name=None), GitHubTokens(**token_data(expires_in=1))
    )
    owner = auth.principal(session).owner_id
    auth.cipher = CredentialCipher(new_key + "," + old_key)
    calls = []

    class Provider:
        def refresh(self, token):
            assert token == REFRESH
            calls.append(token)
            return GitHubTokens(
                **token_data(access_token="rotated-fixture", refresh_token="rotated-refresh")
            )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: auth.access_token(owner, Provider()), range(4)))
    assert results == ["rotated-fixture"] * 4 and len(calls) == 1
    with store.connect() as db:
        raw = db.execute("SELECT ciphertext FROM credentials").fetchone()[0]
    assert CredentialCipher(new_key).decrypt(owner, raw)["refresh_token"] == "rotated-refresh"
    with pytest.raises(ReconnectRequired):
        auth.cipher.decrypt("foreign-user", raw)


def test_history_search_is_global_and_literal(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite3")
    request = CheckRequest(kind="meta_smoke")
    for index in range(72):
        run = store.enqueue(request, f"key-{index}", owner_id=f"owner-{index}")
        store.transition(
            run.id,
            stage="complete",
            message="Finished",
            status="finished",
            title="needle_%" if index == 0 else f"run {index}",
        )
    # Same-owner history with enough rows to exceed one page.
    with store.connect() as db:
        db.execute("UPDATE runs SET owner_id='test-owner'")
    assert len(store.page(owner_id="test-owner")[0]) == 30
    found, next_cursor = store.page(owner_id="test-owner", query="needle_%")
    assert len(found) == 1 and next_cursor is None
    assert found[0].title == "needle_%"
    assert store.page(owner_id="test-owner", kind="source_trace")[0] == []
