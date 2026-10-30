"""Controlled hosted control-plane checks; these do not certify a Linux deployment."""

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from threading import Event

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_auth import login, token_data
from test_projects import independent as independent
from test_projects import pin

from app.api.contracts import CheckRequest
from app.api.storage import RunStore
from app.api.worker import LocalWorker
from app.auth.crypto import CredentialCipher, ReconnectRequired
from app.auth.github import GitHubClient
from app.config import Settings
from app.deployment import execution_lock_path
from app.execution import ExecutionBusy, ProcessLock, cli_execution
from app.hosting import release
from app.hosting.release import GATES, PROFILES, build_digest, released_profiles
from app.hosting.runtime import deployment_settings, run_worker
from app.hosting.status import RemoteWorker, WorkerStatus
from app.jobs.leases import JobLeases
from app.jobs.operations import backup, restore, rollback_hosting_schema, rotate_credentials
from app.jobs.processes import terminate
from app.main import create_app
from app.project_contracts import ProjectAnalysisRequest
from app.tools.runner import PROJECT_ROOT

ORIGIN = "https://upgrade.example.com"


@pytest.fixture(autouse=True)
def isolated_build(tmp_path, monkeypatch):
    """Backend tests bind a controlled build without requiring or changing frontend/dist."""
    root = tmp_path / "build"
    for path, content in {
        "backend/pyproject.toml": "[project]\nname='fixture'\n",
        "backend/uv.lock": "version = 1\n",
        "backend/app/controller.py": "# controlled executable build fixture\n",
        "frontend/dist/index.html": "<main>Controlled build fixture</main>",
        "deploy/hosted/worker.service": "[Service]\nUser=fixture\n",
    }.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    monkeypatch.setattr(release, "build_digest", lambda _: build_digest(root))
    return root


def configuration(root, **changes):
    return Settings(
        _env_file=None,
        **{
            "deployment_mode": "hosted",
            "data_root": root / "data",
            "deployment_id": "fixture-host",
            "app_base_url": ORIGIN,
            "auth_mode": "github",
            "github_client_id": "fixture-client",
            "github_client_secret": "fixture-secret",
            "github_app_slug": "fixture-app",
            "token_encryption_keys": Fernet.generate_key().decode(),
            "hosted_release_path": root / "release/approved.json",
            "operator_github_ids": (67,),
            **changes,
        },
    )


def approve(settings):
    """Synthetic operator evidence; never install this fixture in a real deployment."""
    root = settings.hosted_release_path.parent
    root.mkdir(parents=True, exist_ok=True)
    data = b'{"scope":"controlled test only"}'
    (root / "evidence.json").write_bytes(data)
    manifest = {
        "schema_version": "hosted-release-v1",
        "deployment_id": settings.deployment_id,
        "origin": ORIGIN,
        "build_sha256": release.build_digest(PROJECT_ROOT),
        "created": time.time() - 1,
        "expires": time.time() + 60,
        "profiles": list(PROFILES),
        "approved_by": "controlled-test-fixture",
        "evidence": {
            gate: {
                "path": "evidence.json",
                "sha256": hashlib.sha256(data).hexdigest(),
                "outcome": "passed",
            }
            for gate in GATES
        },
    }
    settings.hosted_release_path.write_text(json.dumps(manifest))
    return manifest


@pytest.mark.parametrize(
    "changes",
    [
        {"auth_mode": "local"},
        {"app_base_url": "http://localhost:8000"},
        {"data_root": Path("relative")},
        {"deployment_id": ""},
        {"github_private_fetch_enabled": True},
    ],
)
def test_hosted_configuration_cannot_fall_back_to_local(tmp_path, changes):
    with pytest.raises(ValidationError):
        configuration(tmp_path, **changes)


def test_release_and_paths_cannot_live_in_writable_storage(tmp_path):
    with pytest.raises(ValidationError):
        configuration(tmp_path, hosted_release_path=tmp_path / "data/approved.json")
    config = configuration(tmp_path)
    assert execution_lock_path(config) == config.data_root / "execution.lock"
    with pytest.raises(ValueError, match="DATA_ROOT"):
        create_app(config, data_root=tmp_path / "other")


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "expired",
        "future",
        "build",
        "source",
        "deployment",
        "origin",
        "gate",
        "failed",
        "tampered",
        "traversal",
        "execution",
        "duplicate",
    ],
)
def test_stale_or_unreviewed_release_blocks_admission_without_reserving(tmp_path, mutation):
    config = configuration(tmp_path)
    manifest = approve(config)
    if mutation == "missing":
        config.hosted_release_path.unlink()
    elif mutation == "tampered":
        (config.hosted_release_path.parent / "evidence.json").write_text("changed")
    elif mutation == "source":
        (tmp_path / "build/backend/app/controller.py").write_text("# changed executable bytes\n")
    else:
        if mutation == "expired":
            manifest["expires"] = time.time() - 10
        elif mutation == "future":
            manifest["created"] = time.time() + 10
        elif mutation == "build":
            manifest["build_sha256"] = "0" * 64
        elif mutation == "deployment":
            manifest["deployment_id"] = "other-host"
        elif mutation == "origin":
            manifest["origin"] = "https://other.example.com"
        elif mutation == "gate":
            del manifest["evidence"][GATES[0]]
        elif mutation == "failed":
            manifest["evidence"][GATES[0]]["outcome"] = "failed"
        elif mutation == "traversal":
            manifest["evidence"][GATES[0]]["path"] = "../outside"
        elif mutation == "execution":
            manifest["profiles"].append("verified_adaptation")
        elif mutation == "duplicate":
            manifest["profiles"].append(PROFILES[0])
        config.hosted_release_path.write_text(json.dumps(manifest))
    store = RunStore(config.data_root / "web/runs.sqlite3", config)
    with pytest.raises(HTTPException) as error:
        store.enqueue(
            ProjectAnalysisRequest(kind="project_analysis", source_project="a" * 32), "job"
        )
    assert error.value.status_code == 503
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM owned_resources").fetchone()[0] == 0


@pytest.mark.parametrize(
    "job",
    [
        CheckRequest(kind="meta_smoke"),
        CheckRequest(kind="pinned_baseline"),
        ProjectAnalysisRequest(kind="project_analysis", source_project="a" * 32, run_baseline=True),
    ],
)
def test_approval_cannot_enable_unreviewed_execution(tmp_path, job):
    config = configuration(tmp_path)
    approve(config)
    store = RunStore(config.data_root / "web/runs.sqlite3", config)
    with pytest.raises(HTTPException) as error:
        store.enqueue(job, "job")
    assert error.value.status_code == 409


def test_hosted_api_shares_worker_health_and_keeps_transport_and_owner_guards(
    tmp_path, monkeypatch
):
    config = configuration(tmp_path)
    approve(config)
    monkeypatch.setattr(LocalWorker, "start", lambda _: pytest.fail("API started a worker"))
    state = {"id": 67}

    def provider(request):
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json=token_data())
        return httpx.Response(200, json={"id": state["id"], "login": "fixture", "name": None})

    with TestClient(create_app(config), base_url=ORIGIN) as client:
        client.app.state.github = GitHubClient(config, transport=httpx.MockTransport(provider))
        assert isinstance(client.app.state.worker, RemoteWorker)
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
        assert client.get("/api/operations/metrics").status_code == 401
        assert (
            client.get(
                "http://upgrade.example.com/health", headers={"X-Forwarded-Proto": "https"}
            ).status_code
            == 400
        )
        assert (
            client.get("/health", headers={"Host": "upgrade.example.com:8443"}).status_code == 400
        )
        session, _ = login(client)
        cookies = client.get("/api/auth/github/start", follow_redirects=False).headers["set-cookie"]
        assert "Secure" in cookies
        other_store = RunStore(config.data_root / "web/runs.sqlite3", config)
        WorkerStatus(other_store).heartbeat("worker", True)
        assert client.get("/ready").status_code == 200
        caps = client.get("/api/capabilities").json()
        assert set(caps["kinds"]) == set(PROFILES)
        assert not caps["target_execution_supported"] and not caps["patch_supported"]
        assert client.get("/api/targets").json() == []
        metrics = client.get("/api/operations/metrics")
        assert metrics.status_code == 200 and metrics.headers["cache-control"] == "no-store"
        assert metrics.headers["strict-transport-security"] == "max-age=31536000"
        assert "fixture-secret" not in metrics.text and "ciphertext" not in metrics.text
        assert client.post("/api/runs", json={"kind": "pinned_baseline"}).status_code == 403
        assert (
            client.post(
                "/api/runs",
                json={"kind": "pinned_baseline"},
                headers={
                    "Origin": ORIGIN,
                    "X-CSRF-Token": session["csrf_token"],
                    "Idempotency-Key": "hosted-fixture-request",
                },
            ).status_code
            == 409
        )
        with other_store.connect() as db:
            db.execute("UPDATE worker_status SET heartbeat=?", (time.time() - 30,))
        assert client.get("/ready").status_code == 503
        with ProcessLock(config.data_root / "maintenance.lock"):
            assert client.get("/api/session").status_code == 503
        state["id"] = 68
        login(client)
        assert client.get("/api/operations/metrics").status_code == 403
        assert client.get("/api/runs/guessed-owner-run").status_code == 404


def test_worker_does_not_claim_paid_work_without_release(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    store = RunStore(config.data_root / "web/runs.sqlite3", config)
    worker = LocalWorker(store, config.data_root / "artifacts", config)
    pruned = Event()
    monkeypatch.setattr("app.jobs.retention.Retention.prune", lambda _: pruned.set())
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while worker.recovery_pending and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not worker.recovery_pending and worker.thread.is_alive()
        assert pruned.wait(2)  # Pausing admission must not pause privacy retention.
        assert not WorkerStatus(store).available()
        duplicate = LocalWorker(store, config.data_root / "artifacts", config)
        duplicate.start()
        assert not duplicate.available
        duplicate.stop()
    finally:
        worker.stop()
    assert not WorkerStatus(store).available()


def test_runtime_and_local_cli_enforce_hosted_policy(tmp_path, monkeypatch):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    with pytest.raises(ValueError, match="credential"):
        deployment_settings()
    config = configuration(tmp_path)
    monkeypatch.setattr("app.config.Settings", lambda: config)
    with pytest.raises(ExecutionBusy, match="CLIs are disabled"), cli_execution(tmp_path):
        pytest.fail("Local owner bypassed hosted policy")
    stopped = Event()
    stopped.set()
    assert run_worker(config, stopped) == 0


def test_key_rotation_is_atomic_and_preserves_owner_binding(tmp_path):
    store = RunStore(tmp_path / "data/web/runs.sqlite3")
    old, new, wrong = (Fernet.generate_key().decode() for _ in range(3))
    previous = CredentialCipher(old)
    with store.connect() as db:
        db.executemany(
            "INSERT INTO credentials VALUES (?,?,?)",
            [
                (owner, previous.encrypt(owner, {"access_token": owner}), time.time())
                for owner in ("a", "b")
            ],
        )
    with pytest.raises(ReconnectRequired):
        rotate_credentials(store, CredentialCipher(wrong))
    with store.connect() as db:
        rows = db.execute("SELECT * FROM credentials").fetchall()
    assert previous.decrypt("a", rows[0]["ciphertext"])["access_token"] == "a"
    assert rotate_credentials(store, CredentialCipher(f"{new},{old}"))["rotated_count"] == 2
    with store.connect() as db:
        rows = db.execute("SELECT * FROM credentials").fetchall()
    for row in rows:
        assert CredentialCipher(new).decrypt(row["user_id"], row["ciphertext"])
        with pytest.raises(ReconnectRequired):
            CredentialCipher(new).decrypt("foreign", row["ciphertext"])
        with pytest.raises(ReconnectRequired):
            previous.decrypt(row["user_id"], row["ciphertext"])


def test_real_process_kill_releases_locks_and_fences_old_lease_without_replay(tmp_path):
    root = tmp_path / "data"
    store = RunStore(root / "web/runs.sqlite3")
    run = store.enqueue(CheckRequest(kind="meta_smoke"), "hard-stop")
    script = """
import sys, time
from pathlib import Path
from app.api.storage import RunStore
from app.execution import ProcessLock
root = Path(sys.argv[1])
with ProcessLock(root / 'web/worker.lock'), ProcessLock(root / 'execution.lock'):
    store = RunStore(root / 'web/runs.sqlite3')
    row = store.claim(worker='killed-fixture')
    (root / 'started').write_text(row['lease_token'])
    time.sleep(60)
"""
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", script, str(root)],
        cwd=PROJECT_ROOT / "backend",
        start_new_session=os.name != "nt",
    )
    try:
        deadline = time.monotonic() + 8
        while not (root / "started").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        token = (root / "started").read_text()
        assert token and not ProcessLock(root / "execution.lock").acquire()
    finally:
        terminate(child)
    with ProcessLock(root / "web/worker.lock"), ProcessLock(root / "execution.lock"):
        assert JobLeases(store).recover(abandoned_worker=True) == [run.id]
        assert store.claim() is None
        assert JobLeases(store).check(run.id, token) == "lease_lost"
    WorkerStatus(store).heartbeat("dead-worker", True)
    saved = tmp_path / "backup"
    backup(store, root, saved)
    restored = tmp_path / "restored"
    restore(saved, restored)
    assert not WorkerStatus(RunStore(restored / "web/runs.sqlite3")).available()


def test_restore_preserves_newer_provider_charges_and_rollback_preserves_ownership(tmp_path):
    import sqlite3

    from app.publishing.contracts import Publication
    from app.publishing.store import PublicationStore

    store = RunStore(tmp_path / "data/web/runs.sqlite3")
    run = store.enqueue(CheckRequest(kind="meta_smoke"), "running")
    store.claim()
    publication = Publication(
        repository_id=101,
        repository="fixture/target",
        base_branch="main",
        base_sha="a" * 40,
        branch="upgrade/retained-operation",
        patch_sha256="b" * 64,
        notices_sha256="c" * 64,
        created_at="2026-10-30T15:00:00+00:00",
        state="published",
        commit_sha="d" * 40,
        tree_sha="e" * 40,
        pr_number=23,
        pr_url="https://github.com/fixture/target/pull/23",
    )
    with store.connect() as db:
        db.execute(
            "INSERT INTO publications VALUES (?,?,?,?,?)",
            (
                "local-workspace",
                run.id,
                "retained-key",
                "f" * 64,
                publication.model_dump_json(),
            ),
        )
    saved = tmp_path / "backup"
    backup(store, tmp_path / "data", saved)
    with store.usage.connect() as db:
        db.execute("INSERT INTO resource_usage VALUES ('2026-10-30','owner','model_attempts',12)")
    destination = tmp_path / "restored"
    restore(saved, destination, deletions_store=store)
    restored = RunStore(destination / "web/runs.sqlite3")
    with restored.usage.connect() as db:
        assert db.execute("SELECT amount FROM resource_usage").fetchone()[0] == 12
    assert rollback_hosting_schema(restored)["schema_version"] == 8
    with sqlite3.connect(restored.path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 8
        row = db.execute("SELECT owner_id,summary FROM runs WHERE id=?", (run.id,)).fetchone()
        assert row[0] == "local-workspace" and json.loads(row[1])["outcome"] == "interrupted"
        assert db.execute("SELECT key FROM publications").fetchone()[0] == "retained-key"
    upgraded = RunStore(restored.path)
    assert upgraded.claim() is None  # Re-upgrade does not replay jobs or publishing.
    assert PublicationStore(upgraded).get("local-workspace", run.id) == publication


def test_template_does_not_create_passing_evidence_or_overwrite_approval(
    tmp_path, monkeypatch, capsys
):
    from scripts import validate_hosted

    config = configuration(tmp_path)
    output = config.hosted_release_path
    output.parent.mkdir()
    monkeypatch.setattr(validate_hosted, "Settings", lambda **_: config)
    monkeypatch.setattr(validate_hosted, "build_digest", release.build_digest)
    monkeypatch.setattr(
        sys, "argv", ["validate_hosted", "--env-file", "unused", "--template", str(output)]
    )
    assert validate_hosted.main() == 0
    assert not released_profiles(config)
    assert json.loads(output.read_text())["approved_by"] == ""
    assert validate_hosted.main() == 1
    assert "fixture-secret" not in capsys.readouterr().out


def test_hosted_api_and_separate_worker_complete_static_analysis(independent, tmp_path):
    projects, original, git, sha, _ = independent
    config = configuration(
        tmp_path,
        data_root=tmp_path,
        hosted_release_path=tmp_path.parent / f"release-{tmp_path.name}/approved.json",
    )
    approve(config)

    def provider(request):
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json=token_data())
        return httpx.Response(200, json={"id": 67, "login": "fixture", "name": None})

    before = git.run("status", "--porcelain", cwd=original)
    with TestClient(create_app(config), base_url=ORIGIN) as client:
        client.app.state.github = GitHubClient(config, transport=httpx.MockTransport(provider))
        session, _ = login(client)
        source = pin(projects, sha, owner=session["user"]["id"])
        code = """
import sys, time
from pathlib import Path
from cryptography.fernet import Fernet
from app.api.storage import RunStore
from app.api.worker import LocalWorker
from app.config import Settings
from app.hosting import release
root = Path(sys.argv[1])
# The parent tests the real build-digest function against an isolated controlled build.
# This subprocess shares that fixture's binding; no operator approval is installed.
release.build_digest = lambda _: sys.argv[3]
settings = Settings(_env_file=None, deployment_mode='hosted', data_root=root,
    deployment_id='fixture-host', hosted_release_path=Path(sys.argv[2]),
    app_base_url='https://upgrade.example.com', auth_mode='github',
    github_client_id='fixture', github_client_secret='fixture', github_app_slug='fixture',
    token_encryption_keys=Fernet.generate_key().decode())
store = RunStore(root / 'web/runs.sqlite3', settings)
worker = LocalWorker(store, root / 'artifacts', settings, connection_check=lambda _: 0)
worker.start()
try:
    while worker.thread.is_alive():
        time.sleep(0.1)
finally:
    worker.stop()
"""
        child = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-c",
                code,
                str(tmp_path),
                str(config.hosted_release_path),
                release.build_digest(PROJECT_ROOT),
            ],
            cwd=PROJECT_ROOT / "backend",
            start_new_session=os.name != "nt",
        )
        try:
            deadline = time.monotonic() + 10
            while client.get("/ready").status_code != 200 and time.monotonic() < deadline:
                time.sleep(0.05)
            assert client.get("/ready").status_code == 200
            response = client.post(
                "/api/runs",
                json={
                    "kind": "project_analysis",
                    "source_project": source.id,
                },
                headers={
                    "Origin": ORIGIN,
                    "X-CSRF-Token": session["csrf_token"],
                    "Idempotency-Key": "separate-process-static-job",
                },
            )
            assert response.status_code == 202, response.text
            run_id = response.json()["id"]
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                result = client.get(f"/api/runs/{run_id}").json()
                if result["status"] == "finished":
                    break
                time.sleep(0.05)
            assert result["status"] == "finished" and result["outcome"] == "passed", result
            assert result["source"]["commit_sha"] == sha
        finally:
            terminate(child)
    assert git.run("status", "--porcelain", cwd=original) == before
