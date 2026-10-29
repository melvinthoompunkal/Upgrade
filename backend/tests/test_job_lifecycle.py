"""F6: real queues, leases, cancellation, provider interruption and owned processes."""

import asyncio
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest
from fastapi import HTTPException
from test_auth import authenticated_app as authenticated_app
from test_auth import login, mutate
from test_web_api import wait_finished

from app.api.artifacts import RunArtifacts
from app.api.contracts import CheckRequest
from app.api.storage import RunStore
from app.api.worker import LocalWorker
from app.config import Settings
from app.jobs.control import ExecutionContext, ExecutionStopped, execution_context
from app.jobs.leases import JobLeases
from app.jobs.processes import run_process
from app.models.meta_client import MetaClient
from app.selection_artifacts import ArtifactStore


def settings(**changes):
    return Settings(_env_file=None, model_api_key="controlled-fixture", **changes)


def enqueue(store, key="job", owner="local-workspace"):
    return store.enqueue(CheckRequest(kind="meta_smoke"), key, owner_id=owner)


def test_fair_claims_advance_another_owner_before_backlog(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite3")
    with store.connect() as db:
        db.executemany(
            "INSERT INTO users(id,github_id,login) VALUES (?,?,?)",
            [
                ("a", 1, "first"),
                ("b", 2, "second"),
            ],
        )
    for index in range(4):
        enqueue(store, f"a-{index}", "a")
    enqueue(store, "b-0", "b")
    first = store.claim(mode="github")
    assert first["owner_id"] == "a"
    store.transition(
        first["id"],
        stage="complete",
        message="Done.",
        status="finished",
        lease_token=first["lease_token"],
    )
    assert store.claim(mode="github")["owner_id"] == "b"


def test_concurrent_admission_and_claims_are_atomic(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite3")

    def admit(index):
        try:
            return enqueue(store, str(index)).id
        except HTTPException as error:
            assert error.status_code == 429
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted = [item for item in pool.map(admit, range(12)) if item]
        claims = list(pool.map(lambda _: store.claim(), range(4)))
    assert len(admitted) == 4
    assert {r["id"] for r in claims} == set(admitted)
    assert len({r["lease_token"] for r in claims}) == 4


def test_expired_lease_cannot_renew_or_publish_and_never_replays(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite3")
    run = enqueue(store)
    row = store.claim()
    with store.connect() as db:
        db.execute("UPDATE job_leases SET expires=0 WHERE run_id=?", (run.id,))
    with pytest.raises(ExecutionStopped, match="expired"):
        JobLeases(store).heartbeat(run.id, row["lease_token"], 30)
    with pytest.raises(ExecutionStopped):
        store.transition(
            run.id,
            stage="complete",
            message="Stale success.",
            status="finished",
            publication=("stale/report.json", "0" * 64),
            lease_token=row["lease_token"],
        )
    assert JobLeases(store).recover() == [run.id]
    assert store.row(run.id)["report"] is None
    assert json.loads(store.row(run.id)["summary"])["outcome"] == "interrupted"
    assert store.claim() is None


def test_running_cancellation_interrupts_provider_and_exports_intact_evidence(tmp_path):
    config = settings()
    store = RunStore(tmp_path / "web/runs.sqlite3", config)
    run = enqueue(store)
    worker = LocalWorker(store, tmp_path / "artifacts", config)
    started, closed = Event(), Event()

    async def provider(request):
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            closed.set()

    async def execute(request, progress):
        artifacts = ArtifactStore(worker.root / f"meta-smoke-{run.id}", {})
        artifacts.json("intact.json", {"evidence": "retained"})
        artifacts.json("changed.json", {"evidence": "old"})
        (artifacts.directory / "changed.json").write_text("changed after registration")
        async with MetaClient(
            config, http_client=httpx.AsyncClient(transport=httpx.MockTransport(provider))
        ) as client:
            await client.request(stage="test", messages=[{"role": "user", "content": "check"}])

    worker.execute = execute
    worker.start()
    try:
        assert started.wait(5)
        assert JobLeases(store).cancel(run.id, "local-workspace").cancel_requested
        wait_finished(store, run.id)
        assert closed.is_set()
    finally:
        worker.stop()
    row = store.row(run.id)
    assert json.loads(row["summary"])["outcome"] == "cancelled"
    bundle = RunArtifacts(worker.root, row)
    names = {item.label for item in bundle.descriptors()}
    assert "intact.json" in names and "changed.json" not in names
    assert bundle.report["usage"]["model_unknown_usage"] == 1
    assert store.claim() is None


@pytest.mark.parametrize(
    "action,expected", [("deadline", "runtime_limit"), ("shutdown", "interrupted")]
)
def test_runtime_and_shutdown_settle_active_calls(tmp_path, action, expected):
    config = settings(job_max_seconds=1 if action == "deadline" else 1200)
    store = RunStore(tmp_path / "web/runs.sqlite3", config)
    run = enqueue(store)
    worker = LocalWorker(store, tmp_path / "artifacts", config)
    started = Event()

    async def execute(request, progress):
        started.set()
        await asyncio.sleep(30)

    worker.execute = execute
    worker.start()
    try:
        assert started.wait(5)
        if action == "shutdown":
            worker.stop()
        wait_finished(store, run.id)
    finally:
        worker.stop()
    assert json.loads(store.row(run.id)["summary"])["outcome"] == expected


def test_cancellation_kills_owned_subprocess(tmp_path):
    store = RunStore(tmp_path / "web/runs.sqlite3")
    run = enqueue(store)
    row = store.claim()
    context = ExecutionContext("local-workspace", store, run.id, row["lease_token"])
    marker = tmp_path / "started"

    def execute():
        with execution_context(context), pytest.raises(ExecutionStopped):
            run_process(
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; import time; "
                    f"Path({str(marker)!r}).write_text('started'); time.sleep(30)",
                ],
                timeout=35,
                max_bytes=1024,
            )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execute)
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            Event().wait(0.01)
        assert marker.exists()
        JobLeases(store).cancel(run.id, "local-workspace")
        future.result(timeout=5)


def test_cancel_api_checks_owner_csrf_and_idempotency(authenticated_app):
    client, state = authenticated_app
    session, _ = login(client)
    run = mutate(client, "/api/runs", session, json={"kind": "meta_smoke"}).json()
    assert client.post(f"/api/runs/{run['id']}/cancel").status_code == 403
    canceled = mutate(client, f"/api/runs/{run['id']}/cancel", session)
    assert canceled.status_code == 200 and canceled.json()["outcome"] == "cancelled"
    assert mutate(client, f"/api/runs/{run['id']}/cancel", session).json() == canceled.json()
    state.update(id=312, login="other-owner")
    other, _ = login(client)
    assert mutate(client, f"/api/runs/{run['id']}/cancel", other).status_code == 404


def test_heartbeat_survives_blocking_owned_work_past_initial_lease(tmp_path):
    config = settings(job_lease_seconds=5)
    store = RunStore(tmp_path / "web/runs.sqlite3", config)
    run = enqueue(store)
    worker = LocalWorker(store, tmp_path / "artifacts", config)

    async def execute(request, progress):
        run_process([sys.executable, "-c", "import time; time.sleep(6)"], timeout=8, max_bytes=1024)
        artifacts = ArtifactStore(worker.root / f"meta-smoke-{run.id}", {})
        report = {"status": "passed"}
        artifacts.json("report.json", report)
        return report, artifacts.directory / "report.json"

    worker.execute = execute
    worker.process(store.claim())
    assert json.loads(store.row(run.id)["summary"])["outcome"] == "passed"


def test_paid_diagnostics_are_operator_only(authenticated_app):
    client, state = authenticated_app
    state.update(id=908, login="ordinary-user")
    session, _ = login(client)
    assert not client.get("/api/capabilities").json()["diagnostics_allowed"]
    assert mutate(client, "/api/runs", session, json={"kind": "meta_smoke"}).status_code == 403
    assert client.get("/api/runs").json()["items"] == []


def test_disconnect_interrupts_inflight_call_without_result_publication(authenticated_app):
    client, _ = authenticated_app
    session, _ = login(client)
    run = mutate(client, "/api/runs", session, json={"kind": "meta_smoke"}).json()
    store, worker = client.app.state.runs, client.app.state.worker
    row = store.claim(mode="github")
    started, closed = Event(), Event()

    async def execute(request, progress):
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            closed.set()

    worker.execute = execute
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker.process, row)
        assert started.wait(5)
        client.app.state.auth.disconnect(session["user"]["id"])
        future.result(timeout=5)
    assert closed.is_set()
    result = store.row(run["id"])
    assert result["report"] is None
    assert json.loads(result["summary"])["error"]["code"] == "access_revoked"
