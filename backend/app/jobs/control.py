"""One execution context, propagated to async tasks and owned blocking operations."""

import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING

from app.errors import GraftError

if TYPE_CHECKING:
    from app.api.storage import RunStore


class ExecutionStopped(GraftError):
    code = "cancelled"

    def __init__(self, code="cancelled"):
        self.code = code
        super().__init__(
            {
                "cancelled": "Cancellation requested. Intact evidence is retained.",
                "interrupted": "The worker stopped. Start a new run to retry.",
                "lease_lost": "Execution ownership expired. No result was published.",
                "runtime_limit": "The job reached its runtime limit.",
                "access_revoked": "Repository authorization changed. Start a new authorized run.",
            }.get(code, "Execution stopped by resource policy.")
        )


class ResourceLimit(GraftError):
    code = "resource_limit"


@dataclass
class ExecutionContext:
    owner: str
    store: "RunStore"
    run_id: str | None = None
    lease_token: str | None = None
    artifact_root: Path | None = None
    access_epoch: int | None = None
    max_seconds: float = 1200
    started: float = field(default_factory=monotonic)
    cancelled: threading.Event = field(default_factory=threading.Event)
    stop_code: str = "cancelled"
    storage_roots: dict[Path, int] = field(default_factory=dict)
    last_storage_check: float = field(default_factory=monotonic)
    private_identity: str | None = None

    def stop(self, code="cancelled"):
        self.stop_code = code
        self.cancelled.set()

    def check(self):
        if self.cancelled.is_set():
            raise ExecutionStopped(self.stop_code)
        if monotonic() - self.started >= self.max_seconds:
            self.stop("runtime_limit")
            raise ExecutionStopped(self.stop_code)
        if monotonic() - self.last_storage_check > 0.5:
            from app.jobs.resources import tree_bytes

            for root, limit in self.storage_roots.copy().items():
                if root.exists() and tree_bytes(root) > limit:
                    raise ResourceLimit("Managed code/artifact storage reached its allowance.")
            self.last_storage_check = monotonic()
        if self.run_id:
            from app.jobs.leases import JobLeases

            code = JobLeases(self.store).check(self.run_id, self.lease_token, self.access_epoch)
            if code:
                self.stop(code)
                raise ExecutionStopped(code)


CURRENT: ContextVar[ExecutionContext | None] = ContextVar("execution_context", default=None)


@contextmanager
def execution_context(context):
    token = CURRENT.set(context)
    try:
        yield context
    finally:
        CURRENT.reset(token)


def checkpoint():
    if context := CURRENT.get():
        context.check()


def fence_in(db):
    """Fence domain checkpoints in their own write transaction as well as final reports."""
    context = CURRENT.get()
    if context and context.run_id:
        from app.jobs.leases import JobLeases

        code = JobLeases(context.store).check_in(
            db, context.run_id, context.lease_token, context.access_epoch
        )
        if code:
            raise ExecutionStopped(code)
