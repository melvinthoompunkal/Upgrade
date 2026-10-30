"""F6 private consent, sensitive-content exclusion, atomic quotas and tenant deletion."""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi import HTTPException
from test_auth import authenticated_app as authenticated_app
from test_auth import login, mutate, token_data
from test_github_lifecycle import connected as connected
from test_github_lifecycle import post_snapshot
from test_github_lifecycle import private_git as private_git

from app.api.storage import RunStore
from app.config import Settings
from app.errors import RepositoryError
from app.jobs.control import ExecutionContext, ResourceLimit, execution_context
from app.jobs.privacy import POLICY, PrivateConsentRequired, SensitiveContent, check_transmission
from app.jobs.quotas import charge
from app.jobs.resources import Resources
from app.jobs.retention import Retention
from app.models.meta_client import MetaClient


def test_secret_inside_ordinary_code_never_reaches_provider(tmp_path):
    requests = []
    config = Settings(_env_file=None, model_api_key="controlled-fixture")
    store = RunStore(tmp_path / "runs.sqlite3", config)

    async def check():
        async with MetaClient(
            config,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request: requests.append(request))
            ),
        ) as client:
            with pytest.raises(SensitiveContent):
                await client.request(
                    stage="test",
                    messages=[
                        {
                            "role": "user",
                            "content": json.dumps({"code": 'password = "sensitive-content-value"'}),
                        }
                    ],
                )
            assert client.budget.requests == 0

    with execution_context(ExecutionContext("owner", store)):
        asyncio.run(check())
    assert requests == []
    with store.usage.connect() as db:
        assert db.execute("SELECT count(*) FROM resource_usage").fetchone()[0] == 0


def test_revoking_private_consent_stops_retries_before_charging_or_transmitting(tmp_path):
    config = Settings(_env_file=None, model_api_key="controlled-fixture")
    store = RunStore(tmp_path / "runs.sqlite3", config)
    identity = "a" * 64
    with store.connect() as db:
        db.execute("INSERT INTO model_consents VALUES (?,?,?,?)", ("owner", identity, POLICY, 0))
    requests = []

    def provider(request):
        requests.append(request)
        with store.connect() as db:
            db.execute("DELETE FROM model_consents WHERE owner_id=?", ("owner",))
        return httpx.Response(500, json={"error": {"message": "Controlled retry."}})

    async def request():
        async with MetaClient(
            config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(provider))
        ) as client:
            with pytest.raises(PrivateConsentRequired):
                await client.request(
                    stage="test", messages=[{"role": "user", "content": "bounded private code"}]
                )
            assert client.budget.requests == 1 and len(client.attempts) == 1

    with execution_context(ExecutionContext("owner", store, private_identity=identity)):
        asyncio.run(request())
    assert len(requests) == 1
    with store.usage.connect() as db:
        assert (
            db.execute(
                "SELECT amount FROM resource_usage WHERE resource='model_attempts'"
            ).fetchone()[0]
            == 1
        )


def test_daily_attempt_quotas_are_atomic_and_shared_across_jobs(tmp_path):
    config = Settings(_env_file=None, daily_model_attempts=3, global_daily_model_attempts=4)
    store = RunStore(tmp_path / "runs.sqlite3", config)

    def attempt(index):
        with execution_context(ExecutionContext("a", store)):
            try:
                charge("model_attempts")
                return True
            except ResourceLimit:
                return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(12))) == 3
    with execution_context(ExecutionContext("b", store)):
        charge("model_attempts")
        with pytest.raises(ResourceLimit, match="shared"):
            charge("model_attempts")
    # Usage after a response is retained even when above the boundary; future calls stop.
    with execution_context(ExecutionContext("b", store)):
        charge("model_input_tokens", config.daily_model_input_tokens + 1, actual=True)
        with pytest.raises(ResourceLimit):
            charge("model_attempts")
        charge("github_requests")  # Model quota cannot block GitHub cleanup.


def test_denied_daily_attempt_is_not_reported_as_a_provider_call(tmp_path):
    config = Settings(_env_file=None, model_api_key="controlled-fixture", daily_model_attempts=1)
    store = RunStore(tmp_path / "runs.sqlite3", config)

    async def request():
        transport = httpx.MockTransport(lambda _: pytest.fail("Provider must not be called."))
        async with MetaClient(config, http_client=httpx.AsyncClient(transport=transport)) as client:
            with pytest.raises(ResourceLimit):
                await client.request(stage="test", messages=[{"role": "user", "content": "check"}])
            assert client.budget.requests == 0 and client.attempts == []

    with execution_context(ExecutionContext("a", store)):
        charge("model_attempts")
        asyncio.run(request())


def test_storage_reservations_prevent_concurrent_over_admission(tmp_path):
    store = RunStore(
        tmp_path / "runs.sqlite3",
        Settings(
            _env_file=None,
            storage_owner_bytes=1024,
            storage_global_bytes=2048,
        ),
    )

    def reserve(index):
        try:
            return Resources(store).reserve("a", "projects", str(index), 1024)
        except ResourceLimit:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(bool(value) for value in pool.map(reserve, range(8))) == 1
    Resources(store).reserve("b", "projects", "other", 1024)
    with pytest.raises(ResourceLimit):
        Resources(store).reserve("c", "projects", "third", 1024)


def test_consent_binds_owner_pins_roles_policy_and_rechecks_access(private_git):
    client, state, session, sha, _ = private_git
    snapshot = post_snapshot(client, session, commit_sha=sha).json()
    inputs = {"source_snapshot": snapshot["id"]}
    view = mutate(client, "/api/privacy/model-disclosure", session, json=inputs).json()
    assert not view["consented"] and not view["analysis_enabled"]
    assert view["snapshots"][0]["sha"] == sha
    assert view["provider_endpoint"] == "https://api.meta.ai/v1"
    context = ExecutionContext(
        session["user"]["id"], client.app.state.runs, private_identity=view["identity"]
    )
    with execution_context(context), pytest.raises(PrivateConsentRequired):
        check_transmission([{"role": "user", "content": "bounded code"}])
    decision = {**inputs, "identity": view["identity"], "policy": view["policy"], "accepted": True}
    assert (
        mutate(
            client,
            "/api/privacy/model-consent",
            session,
            json={
                **decision,
                "policy": "old-policy",
            },
        ).status_code
        == 409
    )
    assert mutate(client, "/api/privacy/model-consent", session, json=decision).json()["consented"]
    with execution_context(context):
        check_transmission([{"role": "user", "content": "bounded code"}])
    assert mutate(client, "/api/privacy/model-disclosure", session, json=inputs).json()["consented"]
    assert not mutate(
        client,
        "/api/privacy/model-consent",
        session,
        json={
            **decision,
            "accepted": False,
        },
    ).json()["consented"]
    state["denied"] = True
    assert mutate(client, "/api/privacy/model-consent", session, json=decision).status_code == 404


def test_deletion_removes_private_copies_and_owner_rows_but_keeps_other_account(private_git):
    client, state, session, sha, _ = private_git
    snapshot = post_snapshot(client, session, commit_sha=sha).json()
    root = client.app.state.private_repository_root / snapshot["id"]
    store = client.app.state.runs
    Resources(store).reserve("other", "projects", "a" * 32, 1024)
    other = client.app.state.project_root / ("a" * 32)
    other.mkdir(parents=True)
    (other / "keep.txt").write_text("another account")
    response = client.request(
        "DELETE",
        "/api/privacy/data",
        headers={
            "origin": "http://127.0.0.1:5173",
            "x-csrf-token": session["csrf_token"],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "complete" and not root.exists()
    assert (other / "keep.txt").read_text() == "another account"
    receipt = client.get(f"/api/privacy/deletions/{response.json()['id']}")
    assert receipt.json() == response.json()
    assert client.get("/api/usage").json()["reserved_bytes"] == 0
    assert client.get("/api/session").json()["authenticated"]
    state.update(id=312, login="other")
    previous = client.app.state.github.transport

    def provider(request):
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json=token_data())
        return previous.handle_request(request)

    client.app.state.github.transport = httpx.MockTransport(provider)
    login(client)
    assert client.get(f"/api/privacy/deletions/{response.json()['id']}").status_code == 404


def test_active_job_prevents_deletion_and_retention(private_git):
    client, _, session, sha, _ = private_git
    snapshot = post_snapshot(client, session, commit_sha=sha).json()
    run = mutate(client, "/api/runs", session, json={"kind": "meta_smoke"}).json()
    service = Retention(client.app.state.runs, client.app.state.data_root)
    with pytest.raises(HTTPException, match="jobs"):
        service.delete_owner(session["user"]["id"])
    assert service.prune(at=time.time() + 31 * 86400) == 0
    assert (client.app.state.private_repository_root / snapshot["id"]).exists()
    assert client.get(f"/api/runs/{run['id']}/status").json()["status"] == "queued"


def test_private_code_expires_even_with_recent_public_data(private_git):
    client, _, session, sha, _ = private_git
    snapshot = post_snapshot(client, session, commit_sha=sha).json()
    with client.app.state.runs.connect() as db:
        db.execute("UPDATE repository_snapshots SET created=0")
    service = Retention(client.app.state.runs, client.app.state.data_root)
    assert service.prune() == 1
    assert not (client.app.state.private_repository_root / snapshot["id"]).exists()
    with client.app.state.runs.connect() as db:
        assert db.execute("SELECT count(*) FROM repository_snapshots").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM owned_resources").fetchone()[0] == 0


def test_failed_private_fetch_cleans_code_even_if_token_cleanup_is_quota_blocked(
    private_git, monkeypatch
):
    client, state, session, sha, _ = private_git

    def fail_fetch():
        raise RepositoryError("Controlled retrieval failure.")

    def quota_blocked(token):
        raise ResourceLimit("Controlled cleanup quota failure.")

    state["during_fetch"] = fail_fetch
    monkeypatch.setattr(client.app.state.github, "revoke_installation_token", quota_blocked)
    response = post_snapshot(client, session, commit_sha=sha)
    assert response.status_code == 409
    assert list(client.app.state.private_repository_root.iterdir()) == []
    with client.app.state.runs.connect() as db:
        assert db.execute("SELECT count(*) FROM repository_snapshots").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM owned_resources").fetchone()[0] == 0
