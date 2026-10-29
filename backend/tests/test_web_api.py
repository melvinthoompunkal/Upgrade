"""Browser contracts, persistence, and artifact integrity without Docker or inference."""

import json
from pathlib import Path
from threading import Event
from time import monotonic

import pytest
from fastapi.testclient import TestClient

from app.api.artifacts import digest, import_history
from app.api.contracts import CheckRequest, SelectionRequest
from app.api.storage import RunStore
from app.api.worker import LocalWorker
from app.config import Settings
from app.main import create_app

SETTINGS = Settings(_env_file=None, model_api_key="offline-test-key")
KEY = "test-submission-key-1234"
REQUEST = {
    "kind": "fit_select",
    "source_url": "https://github.com/scrapy/scrapy",
    "source_sha": "a" * 40,
    "target": "crawler-demo",
    "goal": "Improve recovery from temporary errors.",
}


def wait_finished(store, run_id):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        if json.loads(store.row(run_id)["summary"])["status"] == "finished":
            return
        Event().wait(0.01)
    raise AssertionError("Worker did not finish within the test deadline.")


@pytest.fixture(autouse=True)
def worker_project_root(tmp_path, monkeypatch):
    # Test workers must never share the real workspace's execution lock.
    monkeypatch.setattr("app.api.worker.PROJECT_ROOT", tmp_path / "project")


@pytest.fixture
def client(tmp_path):
    app = create_app(SETTINGS, data_root=tmp_path, start_worker=False)
    with TestClient(app) as client:
        app.state.worker.available = True  # Queue-only tests; never start an actual workflow.
        yield client
        app.state.worker.available = False


def post(client, body=None, key=KEY, **kwargs):
    return client.post(
        "/api/runs", json=body or REQUEST, headers={"Idempotency-Key": key, **kwargs}
    )


def test_capabilities_are_configuration_not_connectivity(client):
    capabilities = client.get("/api/capabilities").json()
    assert capabilities["model_configured"] is True
    assert capabilities["patch_supported"] is False
    assert set(capabilities["kinds"]) == {
        "fit_select",
        "source_trace",
        "meta_smoke",
        "pinned_baseline",
        "local_baseline",
        "project_analysis",
        "catalog_discovery",
        "relevance_assessment",
    }
    assert "offline-test-key" not in json.dumps(capabilities)
    assert "verified" not in capabilities


def test_duplicate_submission_conflict_and_event_cursor(client):
    first = post(client)
    assert first.status_code == 202
    second = post(client)
    assert second.json()["id"] == first.json()["id"]
    assert len(client.get("/api/runs").json()["items"]) == 1
    assert post(client, {**REQUEST, "goal": "Different inputs"}).status_code == 409
    run_id = first.json()["id"]
    events = client.get(f"/api/runs/{run_id}/events").json()
    assert len(events["items"]) == 1
    assert (
        client.get(f"/api/runs/{run_id}/events?after={events['next_cursor']}").json()["items"] == []
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"source_url": "https://key@github.com/owner/repo"},
        {"source_url": "https://example.com/owner/repo"},
        {"source_sha": "main"},
        {"goal": " "},
        {"goal": "x" * 2001},
        {"target": "C:/private"},
        {"kind": "build_and_verify"},
        {"shell": "anything"},
    ],
)
def test_invalid_inputs_do_not_enqueue(client, changes):
    assert post(client, {**REQUEST, **changes}).status_code == 422
    assert client.get("/api/runs").json()["items"] == []


def test_queue_is_bounded_and_browser_origin_is_checked(client):
    assert post(client, Origin="https://unrelated.example").status_code == 403
    for index in range(4):
        assert post(client, key=f"unique-submission-key-{index}").status_code == 202
    assert post(client, key="unique-submission-key-overflow").status_code == 429
    assert post(client, key="unique-submission-key-0").status_code == 202
    assert client.get("/api/unknown").status_code == 404


def test_runs_survive_reopening_database(client):
    run_id = post(client).json()["id"]
    reopened = RunStore(client.app.state.runs.path)
    assert reopened.row(run_id)["request"]
    assert len(reopened.events(run_id, 0)) == 1


def publish_report(root: Path, files: dict, **changes) -> tuple[Path, dict]:
    directory = root / "artifacts" / ("fit-select-" + "b" * 32)
    directory.mkdir(parents=True)
    hashes = {}
    for name, data in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = data if isinstance(data, bytes) else json.dumps(data).encode()
        path.write_bytes(raw)
        hashes[name] = digest(raw)
    report = {
        "status": "failed",
        "created_at": "2026-10-28T15:00:00Z",
        "goal": "Integrity test",
        "analyzed_feature_count": 0,
        "artifact_hashes": hashes,
        **changes,
    }
    (directory / "report.json").write_text(json.dumps(report))
    return directory, report


def test_artifact_bytes_are_verified_and_host_paths_are_not_exported(tmp_path):
    directory, _ = publish_report(
        tmp_path,
        {
            "report.md": b"A completed report.",
            "test-captures/baseline/capture.json": {
                "root": "C:/Users/person/private",
                "command": ["docker", "private"],
                "detail": "C:\\Users\\person\\private",
                "status": "passed",
            },
        },
    )
    with TestClient(create_app(SETTINGS, data_root=tmp_path, start_worker=False)) as client:
        run = client.get("/api/runs").json()["items"][0]
        detail = client.get(f"/api/runs/{run['id']}").json()
        capture = next(item for item in detail["artifacts"] if item["kind"] == "test capture")
        route = f"/api/runs/{run['id']}/artifacts/{capture['id']}"
        response = client.get(route)
        assert response.status_code == 200
        assert "private" not in response.text
        assert "command" not in response.text
        assert digest(response.content) == capture["sha256"]
        (directory / capture["label"]).write_text("changed after publication")
        assert client.get(route).status_code == 409
        assert client.get(f"/api/runs/{run['id']}/artifacts/arbitrary-file").status_code == 404


def test_changed_receipt_keeps_a_visible_integrity_warning(tmp_path):
    directory, _ = publish_report(tmp_path, {})
    with TestClient(create_app(SETTINGS, data_root=tmp_path, start_worker=False)) as client:
        run_id = client.get("/api/runs").json()["items"][0]["id"]
        (directory / "report.json").write_text('{"status":"selected"}')
        detail = client.get(f"/api/runs/{run_id}").json()
        assert detail["features"] == []
        assert detail["artifacts"] == []
        assert "changed" in detail["warnings"][0]


def test_pinned_binding_registers_its_hash_bound_capture_json(tmp_path):
    content = b'{"suite":"baseline","status":"passed"}'
    binding = {
        "captures": [
            {
                "suite": "baseline",
                "artifact_id": "test-captures/baseline/capture.json",
                "sha256": digest(content),
            }
        ],
    }
    binding_raw = json.dumps(binding).encode()
    directory, report = publish_report(
        tmp_path,
        {
            "baseline-binding.json": binding_raw,
            "test-captures/baseline/capture.json": content,
        },
        baseline_binding={"artifact_id": "baseline-binding.json", "sha256": digest(binding_raw)},
    )
    # The capture itself is registered only by the W1 binding, not the top-level manifest.
    report["artifact_hashes"].pop("test-captures/baseline/capture.json")
    (directory / "report.json").write_text(json.dumps(report))
    with TestClient(create_app(SETTINGS, data_root=tmp_path, start_worker=False)) as client:
        run_id = client.get("/api/runs").json()["items"][0]["id"]
        artifacts = client.get(f"/api/runs/{run_id}/artifacts").json()
        capture = next(item for item in artifacts if item["label"].endswith("capture.json"))
        assert capture["available"] is True
        response = client.get(f"/api/runs/{run_id}/artifacts/{capture['id']}")
        assert response.json() == {"suite": "baseline", "status": "passed"}


def test_reference_index_only_serves_stored_hash_checked_excerpts(tmp_path):
    ref = {
        "repo_id": "source",
        "commit_sha": "a" * 40,
        "path": "retry.py",
        "start_line": 2,
        "end_line": 2,
        "symbol": None,
    }
    text = "def fetch():\n    return response\n"
    card = {
        "feature_id": "retry",
        "name": "Retry behavior",
        "behavior": "A bounded retry.",
        "entry_points": [ref],
        "implementation": [ref],
        "dependencies": [],
        "source_tests": [],
        "acceptance_behaviors": ["Retries temporary errors"],
        "transferable_slice": "Retry at fetch",
        "excluded_behaviors": [],
        "license_spdx": None,
        "license_refs": [],
        "evidence_status": "partial",
    }
    publish_report(
        tmp_path,
        {
            "source-trace.json": {"data": {"features": [card]}},
            "evidence-reads-source.json": {
                "data": {
                    "repo_id": "source",
                    "commit_sha": "a" * 40,
                    "windows": [
                        {
                            "path": "retry.py",
                            "start_line": 1,
                            "end_line": 2,
                            "text": text,
                            "sha256": digest(text.encode()),
                        }
                    ],
                }
            },
        },
    )
    with TestClient(create_app(SETTINGS, data_root=tmp_path, start_worker=False)) as client:
        run_id = client.get("/api/runs").json()["items"][0]["id"]
        detail = client.get(f"/api/runs/{run_id}").json()
        assert len(detail["references"]) == 1
        reference_id = detail["references"][0]["id"]
        result = client.get(f"/api/runs/{run_id}/references/{reference_id}").json()
        assert result["text"] == "    return response\n"
        assert "text" not in detail["references"][0]
        assert client.get(f"/api/runs/{run_id}/references/unread-file").status_code == 404


def test_import_does_not_duplicate_browser_owned_reports(tmp_path):
    directory, _ = publish_report(tmp_path, {})
    store = RunStore(tmp_path / "web/runs.sqlite3")
    run = store.enqueue(SelectionRequest(**REQUEST), KEY)
    raw = (directory / "report.json").read_bytes()
    store.attach(run.id, directory.name + "/report.json", digest(raw))
    import_history(store, tmp_path / "artifacts")
    assert len(store.summaries()) == 1


def test_worker_lock_and_restart_never_replay_running_inference(tmp_path):
    store = RunStore(tmp_path / "web/runs.sqlite3")
    run = store.enqueue(CheckRequest(kind="meta_smoke"), KEY)
    store.claim()  # Simulate a prior process stopping during execution.
    worker = LocalWorker(store, tmp_path / "artifacts", SETTINGS)
    worker.start()
    second = LocalWorker(store, tmp_path / "artifacts", SETTINGS)
    try:
        second.start()
        assert second.available is False
    finally:
        worker.stop()
    result = json.loads(store.row(run.id)["summary"])
    assert result["status"] == "finished"
    assert result["outcome"] == "interrupted"


def test_worker_completes_a_shared_service_result_and_records_progress(tmp_path):
    root = tmp_path / "custom-data/artifacts"
    root.mkdir(parents=True)
    store = RunStore(tmp_path / "web/runs.sqlite3")
    store.enqueue(CheckRequest(kind="meta_smoke"), KEY)
    started, release = Event(), Event()
    worker = LocalWorker(store, root, SETTINGS)

    async def fake_execute(request, progress):
        assert request.kind == "meta_smoke"
        progress("checks", "Test service started.")
        started.set()
        assert release.wait(5)
        path = root / "report.json"
        path.write_text('{"status":"passed"}')
        return {"status": "passed"}, path

    worker.execute = fake_execute
    worker.start()
    try:
        assert started.wait(5)
        release.set()
        wait_finished(store, store.summaries()[0].id)
    finally:
        release.set()
        worker.stop()
    result = store.summaries()[0]
    assert result.status == "finished"
    assert result.outcome == "passed"
    assert [event.stage for event in store.events(result.id, 0)] == [
        "inputs",
        "inputs",
        "checks",
        "complete",
    ]


def test_shared_cli_execution_lock_defers_worker_claim(tmp_path, monkeypatch):
    from app.api import worker as worker_module
    from app.execution import ProcessLock

    root = tmp_path / "custom-data/artifacts"
    root.mkdir(parents=True)
    store = RunStore(tmp_path / "web/runs.sqlite3")
    queued = store.enqueue(CheckRequest(kind="meta_smoke"), KEY)
    deferred, started = Event(), Event()
    acquire = ProcessLock.acquire

    def observe(lock):
        acquired = acquire(lock)
        if not acquired:
            deferred.set()
        return acquired

    monkeypatch.setattr(worker_module.ProcessLock, "acquire", observe)
    worker = LocalWorker(store, root, SETTINGS)

    async def execute(*_):
        started.set()
        path = root / "report.json"
        path.write_text('{"status":"passed"}')
        return {"status": "passed"}, path

    worker.execute = execute
    lock_path = worker_module.PROJECT_ROOT / "data/execution.lock"
    with ProcessLock(lock_path):
        worker.start()
        assert deferred.wait(5)
        assert json.loads(store.row(queued.id)["summary"])["status"] == "queued"
    try:
        assert started.wait(5)
        wait_finished(store, queued.id)
    finally:
        worker.stop()
    assert store.summaries()[0].outcome == "passed"
    # Acquiring/releasing repeatedly never appends to the lock file.
    for _ in range(10):
        with ProcessLock(lock_path):
            pass
    assert lock_path.stat().st_size == 1
