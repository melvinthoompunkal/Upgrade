"""F2 provider, private Git, revocation and publication boundaries, offline."""

import base64
import hashlib
import hmac
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pydantic import SecretStr, ValidationError
from test_auth import authenticated_app as authenticated_app
from test_auth import login, mutate

from app.auth.access import RepositoryAccess, RepositoryAccessDenied
from app.auth.crypto import ReconnectRequired
from app.auth.git_broker import git_credential_channel
from app.auth.github import GitHubClient, GitHubError, GitHubRateLimited, GitHubTokens, GitHubUser
from app.auth.webhooks import accept_delivery
from app.config import Settings
from app.errors import RepositoryError
from app.tools.git import Git

SHA = "a" * 40
SECRET = "fixture-webhook-secret"
INSTALL_TOKEN = "opaque-installation-token-of-any-length"
REPOSITORY = {
    "id": 41,
    "full_name": "river/project",
    "owner": {"login": "river"},
    "private": True,
    "archived": False,
    "default_branch": "main",
    "size": 1,
}
INSTALLATION = {
    "id": 7,
    "app_id": 123,
    "account": {"login": "river"},
    "permissions": {"contents": "read"},
    "repository_selection": "selected",
    "suspended_at": None,
}


@pytest.fixture
def connected(authenticated_app, tmp_path):
    client, state = authenticated_app
    session, _ = login(client)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "fixture-key.pem"
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    settings = client.app.state.settings.model_copy(
        update={
            "github_app_id": 123,
            "github_private_key_path": path,
            "github_webhook_secret": SecretStr(SECRET),
            "github_private_fetch_enabled": True,
        }
    )
    client.app.state.settings = settings
    state.update(
        repo=dict(REPOSITORY),
        installation=dict(INSTALLATION),
        denied=False,
        requests=[],
        token_changes={},
        revoke_status=204,
    )

    def handler(request):
        state["requests"].append(request)
        endpoint = request.url.path
        if endpoint == "/user":
            return httpx.Response(200, json={"id": state["id"], "login": state["login"]})
        if endpoint == "/user/installations":
            return httpx.Response(200, json={"installations": [state["installation"]]})
        if endpoint == "/user/installations/7/repositories":
            return httpx.Response(200, json={"repositories": [state["repo"]]})
        if endpoint in {"/repos/river/project", "/repos/new-owner/renamed"}:
            return (
                httpx.Response(404) if state["denied"] else httpx.Response(200, json=state["repo"])
            )
        if endpoint.startswith("/repos/") and endpoint.endswith("/installation"):
            return httpx.Response(200, json=state["installation"])
        if endpoint == "/app/installations/7/access_tokens":
            return httpx.Response(
                201,
                json={
                    "token": INSTALL_TOKEN,
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "permissions": {"contents": "read"},
                    "repositories": [state["repo"]],
                    **state["token_changes"],
                },
            )
        if endpoint == "/installation/token":
            return httpx.Response(204)
        if endpoint.endswith("/grant"):
            return httpx.Response(state["revoke_status"])
        return httpx.Response(500, json={"private": "provider secret must never escape"})

    client.app.state.github = GitHubClient(settings, transport=httpx.MockTransport(handler))
    assert mutate(client, "/api/repositories/sync", session).status_code == 200
    state["requests"].clear()
    return client, state, session, key


def post_snapshot(client, session, **changes):
    return mutate(
        client,
        "/api/repository-snapshots",
        session,
        json={
            "repository_id": 41,
            "commit_sha": SHA,
            "role": "source",
            **changes,
        },
    )


def webhook(client, event, body, *, delivery="delivery-1", signature=None):
    raw = json.dumps(body).encode()
    signature = signature or "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/api/webhooks/github",
        content=raw,
        headers={
            "x-hub-signature-256": signature,
            "x-github-delivery": delivery,
            "x-github-event": event,
        },
    )


def test_app_jwt_is_valid_short_lived_rs256_and_single_repo_token_is_restricted(connected):
    client, state, _, key = connected
    token = client.app.state.github.installation_token(7, 41)
    request = state["requests"][0]
    assert request.method == "POST"
    assert json.loads(request.content) == {
        "repository_ids": [41],
        "permissions": {"contents": "read"},
    }
    header, claims, signature = request.headers["authorization"].removeprefix("Bearer ").split(".")

    def decode(s):
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

    assert json.loads(decode(header))["alg"] == "RS256"
    payload = json.loads(decode(claims))
    assert payload["iss"] == "123" and payload["exp"] - payload["iat"] <= 600
    key.public_key().verify(
        decode(signature), f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    assert INSTALL_TOKEN not in repr(token)


@pytest.mark.parametrize(
    "changes",
    [
        {"repositories": [{"id": 42}]},
        {"permissions": {"contents": "write"}},
        {"expires_at": "2020-01-01T00:00:00Z"},
        {"expires_at": "2027-01-01"},
        {"token": ""},
        {"repositories": []},
    ],
)
def test_invalid_installation_token_is_rejected(connected, changes):
    client, state, _, _ = connected
    state["token_changes"] = changes
    with pytest.raises(GitHubError, match="unusable"):
        client.app.state.github.installation_token(7, 41)


def test_forged_or_foreign_repository_never_mints_or_fetches(connected):
    client, state, session, _ = connected
    response = post_snapshot(client, session, repository_id=999)
    assert response.status_code == 404 and not state["requests"]


@pytest.mark.parametrize(
    "change",
    [
        {"suspended_at": "2026-10-28"},
        {"id": 8},
        {"app_id": 124},
        {"permissions": {}},
    ],
)
def test_changed_installation_grants_cannot_mint(connected, change):
    client, state, session, _ = connected
    state["installation"].update(change)
    assert post_snapshot(client, session).status_code == 404
    assert all("access_tokens" not in str(r.url) for r in state["requests"])


def test_lost_user_access_cannot_be_substituted_by_app_access(connected):
    client, state, session, _ = connected
    state["denied"] = True
    assert post_snapshot(client, session).status_code == 404
    assert len(state["requests"]) == 1
    assert client.get("/api/repositories").json()["items"] == []


def test_rename_is_resolved_by_numeric_identity(connected):
    client, state, session, _ = connected
    state["repo"].update(full_name="new-owner/renamed", owner={"login": "new-owner"})
    access = RepositoryAccess(client.app.state.runs, client.app.state.auth, client.app.state.github)
    grant = access.authorize(session["user"]["id"], 41)
    assert grant.repository.full_name == "new-owner/renamed"
    assert client.get("/api/repositories").json()["items"][0]["full_name"] == "new-owner/renamed"


@pytest.fixture
def private_git(connected, tmp_path, monkeypatch):
    client, state, session, _ = connected
    git = Git(tmp_path / "fixture-control")
    origin = tmp_path / "fixture-origin"
    git.run("init", "--template=", str(origin))
    (origin / "package").mkdir()
    (origin / "package/library.py").write_text("VALUE = 41\n")
    (origin / ".env").write_text("DO_NOT_EXPORT=fixture\n")
    git.run("add", ".", cwd=origin)
    git.run("commit", "-m", "Fixture", cwd=origin)
    sha = git.run("rev-parse", "HEAD", cwd=origin).decode().strip()
    original = Git.run
    calls = []

    def fetch(instance, *args, **kwargs):
        if args[0] == "fetch" and "https://github.com/river/project.git" in args:
            calls.append((args, dict(kwargs["credential_channel"])))
            if state.get("during_fetch"):
                state["during_fetch"]()
            args = tuple(
                str(origin) if a == "https://github.com/river/project.git" else a for a in args
            )
        return original(instance, *args, **kwargs)

    monkeypatch.setattr(Git, "run", fetch)
    return client, state, session, sha, calls


def test_granted_private_snapshot_pins_blobs_and_rechecks_every_read(private_git):
    client, state, session, sha, calls = private_git
    response = post_snapshot(client, session, commit_sha=sha, project_root="package")
    assert response.status_code == 201, response.text
    view = response.json()
    assert view["commit_sha"] == sha and view["file_count"] == 1 and view["private"]
    assert view["capability"] == "browse_index" and view["project_root"] == "package"
    assert calls[0][0][-2:] == ("https://github.com/river/project.git", sha)
    assert INSTALL_TOKEN not in json.dumps(calls)
    root = client.app.state.private_repository_root / view["id"]
    assert not list(root.glob("credential-*"))
    assert INSTALL_TOKEN.encode() not in b"".join(
        p.read_bytes() for p in root.rglob("*") if p.is_file()
    )
    assert any(
        r.url.path == "/installation/token" and r.method == "DELETE" for r in state["requests"]
    )
    assert client.get(f"/api/repository-snapshots/{view['id']}").status_code == 200
    state["denied"] = True
    assert client.get(f"/api/repository-snapshots/{view['id']}").status_code == 404
    assert INSTALL_TOKEN not in response.text and "C:\\" not in response.text


@pytest.mark.parametrize("event", ["disconnect", "grant-removed"])
def test_revocation_during_fetch_prevents_publication_and_cleans_owned_code(private_git, event):
    client, state, session, sha, _ = private_git
    if event == "disconnect":
        state["during_fetch"] = lambda: client.app.state.auth.disconnect(session["user"]["id"])
    else:
        state["during_fetch"] = lambda: accept_delivery(
            client.app.state.auth,
            "during-fetch",
            "installation_repositories",
            json.dumps({"action": "removed", "installation": {"id": 7}}).encode(),
        )
    response = post_snapshot(client, session, commit_sha=sha)
    assert response.status_code == 404, response.text
    with client.app.state.runs.connect() as db:
        assert db.execute("SELECT count(*) FROM repository_snapshots").fetchone()[0] == 0
    assert not list(client.app.state.private_repository_root.iterdir())
    assert state["requests"][-1].url.path == "/installation/token"


def test_failed_git_fetch_revokes_token_and_does_not_publish(connected, monkeypatch):
    client, state, session, _ = connected
    original = Git.run

    def fail(instance, *args, **kwargs):
        if args[0] == "fetch":
            raise RepositoryError("Git operation failed.")
        return original(instance, *args, **kwargs)

    monkeypatch.setattr(Git, "run", fail)
    assert post_snapshot(client, session).status_code == 409
    assert state["requests"][-1].url.path == "/installation/token"
    assert not list(client.app.state.private_repository_root.iterdir())


def test_askpass_runs_without_token_on_disk_in_argv_or_environment(tmp_path):
    git = Git(tmp_path / "control")
    with git_credential_channel(tmp_path, INSTALL_TOKEN) as channel:
        env = {**git.env, **channel}
        assert INSTALL_TOKEN not in json.dumps(env)
        assert all(
            INSTALL_TOKEN.encode() not in p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
        )
        cmd = [channel["GIT_ASKPASS"], "Password for https://github.com:"]
        if os.name == "nt":
            cmd = [os.environ["COMSPEC"], "/c", *cmd]
        assert INSTALL_TOKEN not in json.dumps(cmd)
        result = subprocess.run(cmd, env=env, capture_output=True, check=True, timeout=10)
        assert result.stdout.decode().strip() == INSTALL_TOKEN
        # Exercise Git's own askpass dispatch, not just the native helper launcher.
        credential = git.run(
            "credential",
            "fill",
            input=b"protocol=https\nhost=github.com\n\n",
            credential_channel=channel,
        )
        assert b"username=x-access-token" in credential
        assert ("password=" + INSTALL_TOKEN).encode() in credential
    assert not Path(channel["GIT_ASKPASS"]).exists()
    assert "UPGRADE_BROKER_NONCE" not in os.environ


def test_webhook_signature_tamper_duplicate_and_delivery_conflict(connected):
    client, _, session, _ = connected
    body = {"action": "removed", "installation": {"id": 7}}
    assert (
        webhook(
            client, "installation_repositories", body, signature="sha256=" + "0" * 64
        ).status_code
        == 401
    )
    assert client.get("/api/repositories").json()["items"]
    result = webhook(client, "installation_repositories", body)
    assert result.status_code == 202 and result.json()["status"] == "accepted"
    assert client.get("/api/repositories").json()["items"] == []
    with client.app.state.runs.connect() as db:
        epoch = db.execute(
            "SELECT access_epoch FROM users WHERE id=?", (session["user"]["id"],)
        ).fetchone()[0]
    assert webhook(client, "installation_repositories", body).json()["status"] == "duplicate"
    assert (
        webhook(client, "installation_repositories", {**body, "action": "added"}).status_code == 409
    )
    with client.app.state.runs.connect() as db:
        assert (
            db.execute(
                "SELECT access_epoch FROM users WHERE id=?", (session["user"]["id"],)
            ).fetchone()[0]
            == epoch
        )


def test_authorization_revocation_disconnects_only_matching_numeric_user(connected):
    client, _, session, _ = connected
    run = mutate(client, "/api/runs", session, json={"kind": "meta_smoke"}).json()
    assert (
        webhook(
            client, "github_app_authorization", {"action": "revoked", "sender": {"id": 9999}}
        ).status_code
        == 202
    )
    assert client.get("/api/session").json()["connection"] == "connected"
    assert (
        webhook(
            client,
            "github_app_authorization",
            {"action": "revoked", "sender": {"id": 67}},
            delivery="revoke-67",
        ).status_code
        == 202
    )
    assert client.get("/api/session").json()["connection"] == "reconnect_required"
    assert client.get(f"/api/runs/{run['id']}/status").json()["outcome"] == "cancelled"


@pytest.mark.parametrize("status,expected", [(204, "revoked"), (500, "manual_revocation_required")])
def test_disconnect_revokes_user_grant_and_reports_provider_failure(connected, status, expected):
    client, state, session, _ = connected
    state["revoke_status"] = status
    response = mutate(client, "/api/auth/disconnect", session)
    assert response.json()["provider_revocation"] == expected
    assert client.get("/api/session").json()["connection"] == "reconnect_required"
    request = next(r for r in state["requests"] if r.url.path.endswith("/grant"))
    assert request.method == "DELETE" and request.headers["authorization"].startswith("Basic ")
    assert "private-access-fixture" not in str(request.url)


def test_forged_setup_callback_is_not_a_grant(connected):
    client, _, _, _ = connected
    assert (
        client.get("/api/github/setup?installation_id=999", follow_redirects=False).status_code
        == 404
    )
    response = client.get("/api/github/setup?installation_id=7", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/repositories"


def test_rate_limit_honors_headers_without_retry_storm_or_body_leak(connected):
    client, _, _, _ = connected
    calls = []

    def limited(request):
        calls.append(request)
        return httpx.Response(
            403,
            headers={"retry-after": "120", "x-ratelimit-remaining": "0"},
            json={"secret": INSTALL_TOKEN},
        )

    github = GitHubClient(client.app.state.settings, transport=httpx.MockTransport(limited))
    with pytest.raises(GitHubRateLimited) as error:
        github.request("GET", "/user", token="fixture")
    assert error.value.retry_after == 120 and len(calls) == 1
    assert INSTALL_TOKEN not in str(error.value)


def test_private_fetch_is_opt_in_and_incomplete_configuration_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, github_private_fetch_enabled=True)


def test_stale_generation_cannot_admit_or_attach_after_reconnect(connected):
    client, _, session, _ = connected
    owner = session["user"]["id"]
    auth, store = client.app.state.auth, client.app.state.runs
    epoch = auth.current_access(owner, client.app.state.github)
    run = mutate(client, "/api/runs", session, json={"kind": "meta_smoke"}).json()
    auth.disconnect(owner)
    with pytest.raises(ReconnectRequired):
        store.attach(run["id"], "never-published/report.json", "0" * 64, access_epoch=epoch)
    assert store.row(run["id"])["report"] is None
    with pytest.raises(RepositoryAccessDenied):
        RepositoryAccess(store, auth, client.app.state.github).authorize(owner, 41)


@pytest.mark.parametrize("boundary", ["start", "execute", "completion"])
def test_worker_never_publishes_after_known_revocation(connected, boundary, monkeypatch):
    client, _, session, _ = connected
    store, auth, worker = client.app.state.runs, client.app.state.auth, client.app.state.worker
    run = mutate(client, "/api/runs", session, json={"kind": "meta_smoke"}).json()
    row = store.claim(mode="github")
    calls = []

    async def execute(request, progress):
        calls.append(request.kind)
        path = client.app.state.artifact_root / "worker-report.json"
        path.write_text('{"status":"passed"}')
        if boundary == "execute":
            auth.disconnect(session["user"]["id"])
        return {"status": "passed"}, path

    worker.execute = execute
    if boundary == "start":
        auth.disconnect(session["user"]["id"])
    if boundary == "completion":
        transition = store.transition

        def revoke_before_publish(*args, **kwargs):
            if kwargs.get("publication"):
                auth.disconnect(session["user"]["id"])
            return transition(*args, **kwargs)

        monkeypatch.setattr(store, "transition", revoke_before_publish)
    worker.process(row)
    final = store.row(run["id"])
    assert final["report"] is None
    summary = json.loads(final["summary"])
    assert summary["status"] == "finished" and summary["outcome"] == "failed"
    assert calls == ([] if boundary == "start" else ["meta_smoke"])


def test_foreign_snapshot_descriptor_is_indistinguishable_from_missing(private_git):
    from test_auth import token_data

    client, state, session, sha, _ = private_git
    view = post_snapshot(client, session, commit_sha=sha).json()
    other = client.app.state.auth.sign_in(
        GitHubUser(id=9801, login="different-owner"), GitHubTokens(**token_data())
    )
    from app.auth.store import SESSION_COOKIE

    client.cookies.set(SESSION_COOKIE, other, domain="127.0.0.1", path="/")
    state["requests"].clear()
    foreign = client.get(f"/api/repository-snapshots/{view['id']}")
    missing = client.get("/api/repository-snapshots/unknown")
    assert foreign.status_code == missing.status_code == 404
    assert foreign.content == missing.content and not state["requests"]


def test_failed_old_access_check_cannot_invalidate_a_new_connection(connected):
    from test_auth import token_data

    client, state, session, _ = connected
    owner = session["user"]["id"]
    github = client.app.state.github
    original = github.request

    def reconnect(method, path, **kwargs):
        if path == "/repos/river/project":
            client.app.state.auth.sign_in(
                GitHubUser(id=67, login="river-builds"), GitHubTokens(**token_data())
            )
            # This new connection must survive the older request's denial.
            from app.auth.github import GitHubDenied

            raise GitHubDenied("Old authorization denied.")
        return original(method, path, **kwargs)

    github.request = reconnect
    access = RepositoryAccess(client.app.state.runs, client.app.state.auth, github)
    with pytest.raises(RepositoryAccessDenied):
        access.authorize(owner, 41)
    assert client.get("/api/session").json()["connection"] == "connected"
    assert client.app.state.auth.access_token(owner, github)


@pytest.mark.parametrize(
    "changes", [{"project_root": "../outside"}, {"commit_sha": "main"}, {"repository_id": True}]
)
def test_invalid_snapshot_inputs_never_contact_provider(connected, changes):
    client, state, session, _ = connected
    assert post_snapshot(client, session, **changes).status_code == 422
    assert not state["requests"]


def test_disabled_private_fetch_does_not_contact_provider(connected):
    client, state, session, _ = connected
    client.app.state.settings = client.app.state.settings.model_copy(
        update={"github_private_fetch_enabled": False}
    )
    assert post_snapshot(client, session).status_code == 409
    assert not state["requests"]


def test_empty_optional_settings_preserve_local_env_template():
    settings = Settings(_env_file=None, github_app_id="", github_private_key_path="")
    assert settings.auth_mode == "local" and not settings.github_private_fetch_enabled
    assert settings.github_app_id is None and settings.github_private_key_path is None


def test_webhook_invalidation_rolls_back_dedup_when_cancellation_fails(connected, monkeypatch):
    client, _, _, _ = connected
    original = client.app.state.runs.cancel_queued_in

    def fail(*_):
        raise RuntimeError("injected cancellation failure")

    monkeypatch.setattr(client.app.state.runs, "cancel_queued_in", fail)
    body = json.dumps({"action": "removed", "installation": {"id": 7}}).encode()
    with pytest.raises(RuntimeError):
        accept_delivery(
            client.app.state.auth, "rollback-delivery", "installation_repositories", body
        )
    assert client.get("/api/repositories").json()["items"]
    with client.app.state.runs.connect() as db:
        assert db.execute("SELECT count(*) FROM github_webhook_deliveries").fetchone()[0] == 0
    monkeypatch.setattr(client.app.state.runs, "cancel_queued_in", original)
    assert accept_delivery(
        client.app.state.auth, "rollback-delivery", "installation_repositories", body
    )


def test_read_provider_retry_is_bounded_and_mutations_are_not_replayed(connected):
    client, _, _, _ = connected
    calls = []

    def unavailable(request):
        calls.append(request)
        return httpx.Response(503)

    github = GitHubClient(client.app.state.settings, transport=httpx.MockTransport(unavailable))
    with pytest.raises(GitHubError):
        github.request("GET", "/user")
    assert len(calls) == 2
    with pytest.raises(GitHubError):
        github.revoke_installation_token("fixture")
    assert len(calls) == 3
