"""Repository sync is explicit, bounded, owner scoped, and independent of inference."""

import httpx
import pytest
from test_auth import authenticated_app as authenticated_app
from test_auth import login, mutate, token_data

from app.auth.crypto import ReconnectRequired
from app.auth.github import GitHubClient, GitHubTokens, GitHubUser
from app.auth.repositories import RepositoryIndex
from app.auth.store import SESSION_COOKIE


def provider(client, *, count=350, installations=None, change=None):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/user/installations":
            return httpx.Response(
                200,
                json={
                    "installations": installations
                    if installations is not None
                    else [{"id": 7, "permissions": {"contents": "read"}, "suspended_at": None}]
                },
            )
        if request.url.path == "/user/installations/7/repositories":
            page = int(request.url.params["page"])
            repos = [
                {
                    "id": i,
                    "full_name": f"river/repository-{i:04}",
                    "owner": {"login": "river"},
                    "private": i % 2 == 0,
                    "default_branch": "main",
                    "archived": False,
                }
                for i in range(1 + (page - 1) * 100, min(page * 100, count) + 1)
            ]
            if change and repos:
                repos[0].update(change)
            return httpx.Response(200, json={"repositories": repos})
        return httpx.Response(500, json={"error": "private provider body"})

    client.app.state.github = GitHubClient(
        client.app.state.settings, transport=httpx.MockTransport(handler)
    )
    return requests


def test_sync_continues_and_search_finds_repositories_beyond_first_page(authenticated_app):
    client, state = authenticated_app
    session, _ = login(client)
    requests = provider(client)
    first = mutate(client, "/api/repositories/sync", session).json()
    assert first["sync_status"] == "partial" and first["discovered_count"] == 300
    assert len(requests) == 4
    second = mutate(client, "/api/repositories/sync", session).json()
    assert second["sync_status"] == "complete" and second["discovered_count"] == 350
    assert len(second["items"]) == 25 and second["next_cursor"] == 25
    assert all(repo["capability"] == "browse_index" for repo in second["items"])
    calls = len(requests)
    response = client.get(
        "/api/repositories",
        params={"q": "repository-0318", "visibility": "private", "owner": "river"},
    )
    assert response.json()["items"][0]["id"] == 318
    assert len(response.content) < 100 * 1024
    assert len(requests) == calls
    seen = []
    for cursor in range(0, 350, 25):
        page = client.get("/api/repositories", params={"cursor": cursor}).json()
        seen.extend(item["id"] for item in page["items"])
    assert seen == list(range(1, 351))
    # A second account sees no cache from the first account.
    token = client.app.state.auth.sign_in(
        GitHubUser(id=1111, login="different", name=None), GitHubTokens(**token_data())
    )
    client.cookies.set(SESSION_COOKIE, token, domain="127.0.0.1", path="/")
    assert client.get("/api/repositories").json()["items"] == []
    assert len(state["requests"]) == 2  # Sign-in only; no model client exists in this path.


@pytest.mark.parametrize(
    "grants",
    [
        [],
        [{"id": 7, "permissions": {"contents": "read"}, "suspended_at": "2026-10-28"}],
        [{"id": 7, "permissions": {}}],
    ],
)
def test_absent_suspended_or_insufficient_grants_are_empty(authenticated_app, grants):
    client, _ = authenticated_app
    session, _ = login(client)
    requests = provider(client, installations=grants)
    response = mutate(client, "/api/repositories/sync", session)
    assert response.status_code == 200
    assert response.json()["sync_status"] == "no_installations"
    assert not response.json()["items"] and len(requests) == 1


@pytest.mark.parametrize(
    "change", [{"id": True}, {"full_name": "https://token@evil.example"}, {"private": "false"}]
)
def test_invalid_provider_repository_metadata_is_sanitized(authenticated_app, change):
    client, _ = authenticated_app
    session, _ = login(client)
    provider(client, count=1, change=change)
    response = mutate(client, "/api/repositories/sync", session)
    assert response.status_code == 409
    assert "token@" not in response.text and "private provider" not in response.text


def test_expired_repository_cache_is_not_served(authenticated_app):
    client, _ = authenticated_app
    session, _ = login(client)
    provider(client, count=1)
    assert mutate(client, "/api/repositories/sync", session).status_code == 200
    with client.app.state.runs.connect() as db:
        db.execute("UPDATE repository_sync SET updated=0")
    page = client.get("/api/repositories").json()
    assert page["sync_status"] == "expired" and not page["items"]


def test_disconnect_during_provider_call_cannot_restore_cache(authenticated_app):
    client, _ = authenticated_app
    session, _ = login(client)
    owner = session["user"]["id"]
    provider(client, count=1)
    github = client.app.state.github
    original = github.request

    def disconnect(*args, **kwargs):
        result = original(*args, **kwargs)
        client.app.state.auth.disconnect(owner)
        return result

    github.request = disconnect
    index = RepositoryIndex(client.app.state.runs, client.app.state.auth, github)
    with pytest.raises(ReconnectRequired):
        index.advance(owner, restart=True)
    with client.app.state.runs.connect() as db:
        assert db.execute("SELECT count(*) FROM repository_cache").fetchone()[0] == 0


def test_failed_refresh_requires_reconnect_without_replaying_refresh(authenticated_app):
    client, _ = authenticated_app
    session, _ = login(client)
    auth = client.app.state.auth
    owner = session["user"]["id"]
    with client.app.state.runs.connect() as db:
        raw = db.execute("SELECT ciphertext FROM credentials WHERE user_id=?", (owner,)).fetchone()[
            0
        ]
        data = auth.cipher.decrypt(owner, raw)
        data["expires"] = 0
        db.execute(
            "UPDATE credentials SET ciphertext=? WHERE user_id=?",
            (auth.cipher.encrypt(owner, data), owner),
        )
    calls = []

    def fail(request):
        calls.append(request)
        return httpx.Response(400, json={"error": "bad_refresh", "secret": "provider secret"})

    github = GitHubClient(client.app.state.settings, transport=httpx.MockTransport(fail))
    for _ in range(2):
        with pytest.raises(ReconnectRequired):
            auth.access_token(owner, github)
    assert len(calls) == 1
    assert client.get("/api/session").json()["connection"] == "reconnect_required"
