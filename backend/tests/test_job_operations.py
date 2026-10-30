"""F6 offline recovery, backup integrity and containment without hosted claims."""

import json
import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

from app.api.contracts import CheckRequest
from app.api.storage import RunStore
from app.config import Settings
from app.execution import cli_execution
from app.jobs.containers import reconcile_containers
from app.jobs.control import ExecutionContext, ResourceLimit, execution_context
from app.jobs.operations import backup, metrics, restore
from app.jobs.resources import Resources
from app.jobs.retention import Retention
from app.selection_artifacts import ArtifactStore


@pytest.mark.parametrize("fails", [False, True])
def test_cli_settles_artifact_storage_on_success_and_failure(tmp_path, monkeypatch, fails):
    config = Settings(_env_file=None)
    monkeypatch.setattr("app.config.Settings", lambda: config)
    store = RunStore(tmp_path / "data/web/runs.sqlite3", config)
    directory = tmp_path / "data/artifacts/cli-fixture"

    def execute():
        with cli_execution(tmp_path):
            ArtifactStore(directory, {}).json("evidence.json", {"saved": True})
            if fails:
                raise RuntimeError("Controlled workflow failure.")

    if fails:
        with pytest.raises(RuntimeError, match="workflow failure"):
            execute()
    else:
        execute()
    with store.connect() as db:
        resource = db.execute("SELECT * FROM owned_resources").fetchone()
    assert resource["state"] == "ready"
    assert resource["bytes"] == (directory / "evidence.json").stat().st_size


@pytest.mark.parametrize("action", ["worker", "reconcile", "restore"])
def test_recovery_settles_artifacts_after_terminal_publication(tmp_path, monkeypatch, action):
    from app.api.worker import LocalWorker
    from app.jobs import operations

    config = Settings(_env_file=None)
    root = tmp_path / "data"
    store = RunStore(root / "web/runs.sqlite3", config)
    run = store.enqueue(CheckRequest(kind="meta_smoke"), "finished-before-cleanup")
    row = store.claim()
    directory = root / "artifacts" / ("meta-smoke-" + run.id)
    with execution_context(
        ExecutionContext("local-workspace", store, run.id, row["lease_token"], root / "artifacts")
    ):
        ArtifactStore(directory, {}).json("evidence.json", {"saved": True})
        store.transition(
            run.id,
            stage="complete",
            message="Finished before the process stopped.",
            status="finished",
            outcome="passed",
            lease_token=row["lease_token"],
        )
    if action == "worker":
        worker = LocalWorker(store, root / "artifacts", config)

        def idle(**kwargs):
            worker.stop_requested.set()
            return None

        monkeypatch.setattr(store, "claim", idle)
        worker.start()
        try:
            worker.thread.join(timeout=5)
            assert not worker.thread.is_alive() and not worker.recovery_pending
        finally:
            worker.stop()
    elif action == "reconcile":
        monkeypatch.setattr(operations, "Settings", lambda: config)
        monkeypatch.setattr(sys, "argv", ["operations", "reconcile", "--data-root", str(root)])
        assert operations.main() == 0
    else:
        saved = tmp_path / "backup"
        backup(store, root, saved)
        destination = tmp_path / "restored"
        restore(saved, destination)
        store = RunStore(destination / "web/runs.sqlite3", config)
    with store.connect() as db:
        resource = db.execute("SELECT * FROM owned_resources WHERE kind='artifacts'").fetchone()
    assert resource["state"] == "ready"
    assert resource["bytes"] == (directory / "evidence.json").stat().st_size
    assert json.loads(store.row(run.id)["summary"])["outcome"] == "passed"
    assert store.claim() is None


def test_cli_recovers_orphan_artifacts_and_containers_before_work(tmp_path, monkeypatch):
    from app.tools import runner

    config = Settings(_env_file=None)
    monkeypatch.setattr("app.config.Settings", lambda: config)
    store = RunStore(tmp_path / "data/web/runs.sqlite3", config)
    directory = tmp_path / "data/artifacts/abandoned-cli"
    with execution_context(
        ExecutionContext("local-workspace", store, artifact_root=directory.parent)
    ):
        ArtifactStore(directory, {}).json("evidence.json", {"saved": True})
    name = "graft-test-" + "a" * 32
    Resources(store).reserve("local-workspace", "container", name, 0)
    calls = []

    def docker(*args):
        calls.append(args)
        return name if args[0] == "ps" else ""

    monkeypatch.setattr(runner, "docker", docker)
    with cli_execution(tmp_path):
        assert calls[-1] == ("rm", "--force", name)
        with store.connect() as db:
            assert (
                db.execute(
                    "SELECT count(*) FROM owned_resources WHERE kind='container'"
                ).fetchone()[0]
                == 0
            )
            resource = db.execute("SELECT * FROM owned_resources WHERE kind='artifacts'").fetchone()
        assert resource["state"] == "ready"
        assert resource["bytes"] == (directory / "evidence.json").stat().st_size


def test_cli_cleanup_failure_blocks_work_and_keeps_container_record(tmp_path, monkeypatch):
    from app.tools import runner
    from app.tools.runner_artifacts import RunnerError

    config = Settings(_env_file=None)
    monkeypatch.setattr("app.config.Settings", lambda: config)
    store = RunStore(tmp_path / "data/web/runs.sqlite3", config)
    Resources(store).reserve("local-workspace", "container", "graft-test-" + "a" * 32, 0)

    def unavailable(*args):
        raise RunnerError("Controlled daemon outage.")

    monkeypatch.setattr(runner, "docker", unavailable)
    with pytest.raises(RunnerError), cli_execution(tmp_path):
        pytest.fail("Work must not start until orphan cleanup succeeds.")
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM owned_resources").fetchone()[0] == 1


def test_storage_recovery_preserves_active_and_queued_reservations(tmp_path):
    store = RunStore(tmp_path / "data/web/runs.sqlite3")
    running = store.enqueue(CheckRequest(kind="meta_smoke"), "running")
    queued = store.enqueue(CheckRequest(kind="meta_smoke"), "queued")
    row = store.claim()
    root = tmp_path / "data/artifacts"
    with execution_context(
        ExecutionContext("local-workspace", store, running.id, row["lease_token"], root)
    ):
        ArtifactStore(root / ("meta-smoke-" + running.id), {}).json("evidence.json", {})
    Resources(store).settle_artifacts(root)
    with store.connect() as db:
        resources = db.execute("SELECT * FROM owned_resources ORDER BY run_id").fetchall()
    assert {r["run_id"] for r in resources} == {running.id, queued.id}
    assert all(r["state"] == "reserved" for r in resources)
    assert all(r["bytes"] == store.settings.job_artifact_bytes for r in resources)


def test_backup_restore_preserves_artifacts_and_never_replays_work(tmp_path):
    root = tmp_path / "data"
    store = RunStore(root / "web/runs.sqlite3")
    run = store.enqueue(CheckRequest(kind="meta_smoke"), "pending")
    directory = root / "artifacts" / ("meta-smoke-" + run.id)
    directory.mkdir(parents=True)
    (directory / "evidence.json").write_text('{"saved":true}')
    destination = tmp_path / "backup"
    result = backup(store, root, destination)
    assert result["state"] == "complete"
    restored = tmp_path / "restored"
    assert restore(destination, restored)["automatic_replay"] is False
    reopened = RunStore(restored / "web/runs.sqlite3")
    assert reopened.claim() is None
    assert json.loads(reopened.row(run.id)["summary"])["outcome"] == "interrupted"
    assert (
        restored / directory.relative_to(root) / "evidence.json"
    ).read_bytes() == b'{"saved":true}'
    (destination / directory.relative_to(root) / "evidence.json").write_text("changed")
    with pytest.raises(ValueError, match="integrity"):
        restore(destination, tmp_path / "tampered")
    assert not (tmp_path / "tampered").exists()


def test_orphan_container_reconciliation_uses_only_registered_identity(tmp_path, monkeypatch):
    from app.tools import runner

    store = RunStore(tmp_path / "runs.sqlite3")
    name = "graft-test-" + "a" * 32
    Resources(store).reserve("owner", "container", name, 0, run_id="lost-job")
    calls = []

    def docker(*args):
        calls.append(args)
        return name if args[0] == "ps" else ""

    monkeypatch.setattr(runner, "docker", docker)
    reconcile_containers(store)
    assert calls[-1] == ("rm", "--force", name)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM owned_resources").fetchone()[0] == 0


def test_cleanup_failure_retains_recovery_record(tmp_path, monkeypatch):
    from app.tools import runner
    from app.tools.runner_artifacts import RunnerError

    store = RunStore(tmp_path / "runs.sqlite3")
    Resources(store).reserve("owner", "container", "graft-test-" + "a" * 32, 0)

    def fail(*args):
        raise RunnerError("Unavailable")

    monkeypatch.setattr(runner, "docker", fail)
    with pytest.raises(RunnerError):
        reconcile_containers(store)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM owned_resources").fetchone()[0] == 1


def test_deletion_rejects_junction_before_touching_any_data(tmp_path):
    root, outside = tmp_path / "data", tmp_path / "outside"
    store = RunStore(root / "web/runs.sqlite3")
    outside.mkdir()
    (outside / "keep").write_text("secret")
    linked = root / "private-repositories" / ("a" * 32)
    linked.parent.mkdir(parents=True)
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(linked), str(outside)],
            check=True,
            capture_output=True,
        )
    else:
        linked.symlink_to(outside, target_is_directory=True)
    Resources(store).reserve("owner", "private-repositories", linked.name, 1024)
    with pytest.raises(ResourceLimit, match="redirected"):
        Retention(store, root).delete_owner("owner")
    assert (outside / "keep").read_text() == "secret"
    with store.connect() as db:
        assert db.execute("SELECT state FROM cleanup_receipts").fetchone()[0] == "failed"
    if os.name == "nt":
        linked.rmdir()  # Remove the junction itself, never recurse into its target.
    else:
        linked.unlink()


def test_hosted_mode_is_fail_closed_and_metrics_are_metadata_only(tmp_path):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, deployment_mode="hosted")
    store = RunStore(tmp_path / "runs.sqlite3")
    store.enqueue(CheckRequest(kind="meta_smoke"), "key")
    view = metrics(store)
    assert view["outcomes"] == {"queued": 1} and view["oldest_queued_at"]
    assert "request" not in view and "owner" not in view


def test_restore_reapplies_newer_owner_deletion_receipts(tmp_path):
    root = tmp_path / "data"
    store = RunStore(root / "web/runs.sqlite3")
    run = store.enqueue(CheckRequest(kind="meta_smoke"), "saved")
    store.transition(run.id, stage="complete", message="Finished.", status="finished")
    name = "meta-smoke-" + run.id
    directory = root / "artifacts" / name
    directory.mkdir(parents=True)
    (directory / "report.json").write_text('{"status":"passed"}')
    store.attach(run.id, name + "/report.json", "0" * 64)
    saved = tmp_path / "backup"
    backup(store, root, saved)
    Retention(store, root).delete_owner("local-workspace")
    restored = tmp_path / "restored"
    restore(saved, restored, deletions_store=store)
    assert RunStore(restored / "web/runs.sqlite3").summaries() == []
    assert not (restored / "artifacts" / name).exists()


def test_pre_f6_reports_are_accounted_and_still_owner_deletable(tmp_path):
    root = tmp_path / "data"
    store = RunStore(root / "web/runs.sqlite3")
    run = store.enqueue(CheckRequest(kind="meta_smoke"), "legacy")
    store.transition(run.id, stage="complete", message="Finished.", status="finished")
    name = "meta-smoke-" + run.id
    directory = root / "artifacts" / name
    directory.mkdir(parents=True)
    report = directory / "report.json"
    report.write_text('{"status":"passed"}')
    store.attach(run.id, name + "/report.json", "0" * 64)
    retention = Retention(store, root)
    retention.account_existing()
    with store.connect() as db:
        row = db.execute("SELECT * FROM owned_resources").fetchone()
    assert row["owner_id"] == "local-workspace" and row["bytes"] == report.stat().st_size
    retention.delete_owner("local-workspace")
    assert not directory.exists() and store.summaries() == []


def test_cancellation_before_container_creation_confirms_absence_and_releases_record(
    tmp_path, monkeypatch
):
    from app.jobs.control import ExecutionContext, ExecutionStopped, execution_context
    from app.tools import runner
    from app.tools.runner_artifacts import RunnerError

    store = RunStore(tmp_path / "runs.sqlite3")
    run = store.enqueue(CheckRequest(kind="meta_smoke"), "cancel-create")
    row = store.claim()

    def docker(*args):
        if args[0] == "create":
            raise ExecutionStopped()
        if args[0] == "rm":
            raise RunnerError("No container was created.")
        assert args[0] == "ps" and args[3].startswith("name=^/graft-test-")
        return ""

    monkeypatch.setattr(runner, "docker", docker)
    with execution_context(ExecutionContext("local-workspace", store, run.id, row["lease_token"])):
        with pytest.raises(ExecutionStopped):
            runner.run_suite(
                suite="isolation",
                image_id="fixture",
                snapshot=tmp_path,
                acceptance=tmp_path,
                directory=tmp_path / "capture",
            )
    with store.connect() as db:
        assert (
            db.execute("SELECT count(*) FROM owned_resources WHERE kind='container'").fetchone()[0]
            == 0
        )


def test_unresolved_owned_container_blocks_next_admission(tmp_path, monkeypatch):
    from app.api.worker import LocalWorker
    from app.tools import runner
    from app.tools.runner_artifacts import RunnerError

    store = RunStore(tmp_path / "web/runs.sqlite3")
    run = store.enqueue(CheckRequest(kind="meta_smoke"), "waiting")
    Resources(store).reserve("local-workspace", "container", "graft-test-" + "a" * 32, 0)

    def unavailable(*args):
        raise RunnerError("Controlled daemon outage.")

    monkeypatch.setattr(runner, "docker", unavailable)
    worker = LocalWorker(store, tmp_path / "artifacts", Settings(_env_file=None))
    worker.start()
    try:
        worker.thread.join(timeout=5)
        assert not worker.thread.is_alive() and not worker.available
        assert json.loads(store.row(run.id)["summary"])["status"] == "queued"
    finally:
        worker.stop()
