"""One process-owned worker. Interrupted inference is never replayed automatically."""

import asyncio
import threading
from pathlib import Path
from uuid import uuid4

from pydantic import TypeAdapter

from app.api.artifacts import digest, public_text, summary_from_report
from app.api.contracts import Problem, RunRequest
from app.api.storage import RunStore
from app.baseline import record_baseline
from app.catalog_store import CatalogStore
from app.catalog_workflow import record_catalog
from app.config import Settings
from app.errors import GraftError
from app.execution import ProcessLock
from app.fit_select import record_fit_selection
from app.models.meta_client import MetaClient
from app.pinned_baseline import record_pinned_baseline
from app.project_analysis import record_project_analysis
from app.projects import ProjectStore
from app.relevance_store import RelevanceStore
from app.relevance_workflow import record_relevance
from app.report_publication import publish_json_report
from app.selection_artifacts import ArtifactStore, prepare_directory
from app.smoke import run_smoke
from app.source_trace import record_source_trace
from app.tools.repositories import RepositoryManager, registered_target
from app.tools.runner import PROJECT_ROOT
from app.tools.runner_artifacts import read_regular_file
from app.workflow_inputs import SelectionInputs, TraceInputs, WorkflowContext


class LocalWorker:
    def __init__(self, store: RunStore, root: Path, settings: Settings, *, connection_check=None):
        self.store, self.root, self.settings = store, root, settings
        self.stop_requested = threading.Event()
        self.available = False
        self.thread = threading.Thread(target=self.loop, name="upgrade-local-worker", daemon=True)
        self.lock = None
        self.connection_check = connection_check

    def start(self) -> None:
        self.lock = ProcessLock(self.store.path.parent / "worker.lock")
        if not self.lock.acquire():
            self.lock = None
            return
        # Holding the OS lock establishes that no prior API worker is still executing.
        for run in self.store.summaries(status="running"):
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

    async def execute(
        self, request: RunRequest, progress, *, owner: str = "local-workspace", access_epoch=None
    ) -> tuple[dict, Path]:
        if request.kind == "relevance_assessment":
            return await record_relevance(
                request, RelevanceStore(self.store),
                ProjectStore(self.store, self.root.parent / "projects"),
                owner, self.root, self.settings, progress, access_epoch=access_epoch,
            )
        if request.kind == "catalog_discovery":
            return await record_catalog(
                request,
                CatalogStore(self.store),
                ProjectStore(self.store, self.root.parent / "projects"),
                owner,
                self.root,
                self.settings,
                progress,
                access_epoch=access_epoch,
            )
        if request.kind == "project_analysis":
            return await asyncio.to_thread(
                record_project_analysis,
                request,
                ProjectStore(self.store, self.root.parent / "projects"),
                owner,
                self.root,
                progress,
            )
        receipt = PROJECT_ROOT / "data/runner-image.json"
        context = WorkflowContext(self.root, self.settings)
        if request.kind == "fit_select":
            return await record_fit_selection(
                SelectionInputs(**request.model_dump(exclude={"kind"}), image_receipt=receipt),
                progress=progress,
                context=context,
            )
        if request.kind == "source_trace":
            progress("analysis", "Pinning repositories and tracing source behavior with Meta.")
            return await record_source_trace(
                TraceInputs(**request.model_dump(exclude={"kind"})), context=context
            )
        if request.kind == "local_baseline":
            progress("checks", "Testing current working files in the restricted Docker runner.")
            return await asyncio.to_thread(record_baseline, receipt, artifact_root=self.root)
        directory = self.root / f"{request.kind.replace('_', '-')}-{uuid4().hex}"
        prepare_directory(directory)
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
        publish_json_report(ArtifactStore(directory, {}), report)
        return report, path

    def loop(self) -> None:
        while not self.stop_requested.is_set():
            # Changing storage does not change the shared target/provider execution domain.
            execution = ProcessLock(PROJECT_ROOT / "data/execution.lock")
            if not execution.acquire():
                self.stop_requested.wait(0.5)
                continue
            try:
                row = self.store.claim(mode=self.settings.auth_mode)
                if row is None:
                    execution.close()
                    self.stop_requested.wait(0.5)
                    continue
                self.process(row)
            finally:
                execution.close()

    def process(self, row) -> None:
        run_id = row["id"]
        try:
            epoch = None
            if self.settings.auth_mode == "github":
                from app.auth.crypto import ReconnectRequired

                with self.store.connect() as db:
                    grant = db.execute(
                        "SELECT * FROM run_authorizations WHERE run_id=?", (run_id,)
                    ).fetchone()
                if not grant or not self.connection_check:
                    raise ReconnectRequired(
                        "GitHub authorization must be checked before execution."
                    )
                epoch = self.connection_check(row["owner_id"])
                if epoch != grant["access_epoch"]:
                    raise ReconnectRequired(
                        "GitHub access changed after submission. Start a new run."
                    )
            request = TypeAdapter(RunRequest).validate_json(row["request"])

            def progress(stage, message, owned_run=run_id):
                if epoch is not None:
                    from app.auth.access import RepositoryAccess

                    with self.store.connect() as db:
                        RepositoryAccess(self.store, None, None).check_in(
                            db, row["owner_id"], epoch
                        )
                self.store.transition(owned_run, stage=stage, message=message)

            if request.kind in {"project_analysis", "catalog_discovery", "relevance_assessment"}:
                report, path = asyncio.run(
                    self.execute(request, progress, owner=row["owner_id"], access_epoch=epoch)
                )
            else:
                report, path = asyncio.run(self.execute(request, progress))
            raw = read_regular_file(self.root, path.relative_to(self.root).as_posix())
            if epoch is not None and self.connection_check(row["owner_id"]) != epoch:
                from app.auth.crypto import ReconnectRequired

                raise ReconnectRequired("GitHub access changed before result publication.")
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
                expected_access_epoch=epoch,
                publication=(path.relative_to(self.root).as_posix(), digest(raw)),
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
