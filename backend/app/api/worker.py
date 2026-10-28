"""One process-owned worker. Interrupted inference is never replayed automatically."""

import asyncio
import json
import os
import threading
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter

from app.api.artifacts import digest, public_text, summary_from_report
from app.api.contracts import Problem, RunRequest
from app.api.storage import RunStore
from app.baseline import record_baseline
from app.config import Settings
from app.errors import GraftError
from app.fit_select import record_fit_selection
from app.models.meta_client import MetaClient
from app.pinned_baseline import record_pinned_baseline
from app.smoke import run_smoke
from app.source_trace import record_source_trace
from app.tools.repositories import RepositoryManager, registered_target
from app.tools.runner import PROJECT_ROOT
from app.tools.runner_artifacts import read_regular_file
from app.workflow_inputs import SelectionInputs, TraceInputs


class LocalWorker:
    def __init__(self, store: RunStore, root: Path, settings: Settings):
        self.store, self.root, self.settings = store, root, settings
        self.stop_requested = threading.Event()
        self.available = False
        self.thread = threading.Thread(target=self.loop, name="upgrade-local-worker", daemon=True)
        self.lock = None

    def start(self) -> None:
        self.lock = (self.store.path.parent / "worker.lock").open("a+b")
        self.lock.seek(0)
        self.lock.write(b"0")
        self.lock.flush()
        self.lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            self.lock = None
            return
        # Holding the OS lock establishes that no prior API worker is still executing.
        for run in self.store.summaries():
            if run.status == "running":
                self.store.transition(
                    run.id,
                    stage="complete",
                    message="The prior worker stopped. No analysis was replayed.",
                    status="finished",
                    outcome="interrupted",
                    error=Problem(
                        code="interrupted",
                        message="The worker stopped before completion. Start a new run to retry.",
                    ),
                )
        self.available = True
        self.thread.start()

    def stop(self) -> None:
        self.stop_requested.set()
        if self.available:
            self.thread.join()  # Drain the owned workflow so its runner cleanup can finish.
        self.available = False
        if self.lock:
            self.lock.close()

    async def execute(self, request: RunRequest, progress) -> tuple[dict, Path]:
        receipt = PROJECT_ROOT / "data/runner-image.json"
        if request.kind == "fit_select":
            return await record_fit_selection(
                SelectionInputs(**request.model_dump(exclude={"kind"}), image_receipt=receipt),
                progress=progress,
            )
        if request.kind == "source_trace":
            progress("analysis", "Pinning repositories and tracing source behavior with Meta.")
            return await record_source_trace(TraceInputs(**request.model_dump(exclude={"kind"})))
        if request.kind == "local_baseline":
            progress("checks", "Testing current working files in the restricted Docker runner.")
            return await asyncio.to_thread(record_baseline, receipt)
        directory = self.root / f"{request.kind.replace('_', '-')}-{uuid4().hex}"
        directory.mkdir(parents=True)
        if request.kind == "pinned_baseline":
            progress(
                "checks", "Testing exact committed target bytes in the restricted Docker runner."
            )
            manager = RepositoryManager(directory / "repositories")
            target, original = manager.local_target(registered_target(PROJECT_ROOT, "crawler-demo"))
            return await asyncio.to_thread(
                record_pinned_baseline, target, original, directory, receipt
            )
        progress(
            "checks", "Checking Meta structured output and the harmless read-only tool round trip."
        )
        async with MetaClient(self.settings) as client:
            report = (await run_smoke(client)).model_dump(mode="json")
        path = directory / "report.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report, path

    def loop(self) -> None:
        while not self.stop_requested.is_set():
            row = self.store.claim()
            if row is None:
                self.stop_requested.wait(0.5)
                continue
            run_id = row["id"]
            try:
                request = TypeAdapter(RunRequest).validate_json(row["request"])

                def progress(stage, message, owned_run=run_id):
                    self.store.transition(owned_run, stage=stage, message=message)

                report, path = asyncio.run(self.execute(request, progress))
                raw = read_regular_file(self.root, path.relative_to(self.root).as_posix())
                self.store.attach(run_id, path.relative_to(self.root).as_posix(), digest(raw))
                result = summary_from_report(run_id, request.kind, report, historical=False)
                self.store.transition(
                    run_id,
                    stage="complete",
                    message=f"Workflow finished: {result.outcome}.",
                    status="finished",
                    outcome=result.outcome,
                    error=result.error,
                    analyzed_feature_count=result.analyzed_feature_count,
                    eligible_feature_count=result.eligible_feature_count,
                )
            except Exception as exc:
                # Never expose provider responses, exception strings, or local paths.
                code = exc.code if isinstance(exc, GraftError) else "workflow_failed"
                self.store.transition(
                    run_id,
                    stage="complete",
                    message="Workflow stopped; available results are retained.",
                    status="finished",
                    outcome="failed",
                    error=Problem(
                        code=code,
                        message=public_text(str(exc))
                        if isinstance(exc, GraftError)
                        else "Execution failed. Check configuration and Docker before retrying.",
                    ),
                )
