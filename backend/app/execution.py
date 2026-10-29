"""OS-owned exclusion shared by command-line and browser workflows."""

import os
from contextlib import contextmanager
from pathlib import Path

from app.errors import GraftError
from app.selection_artifacts import prepare_directory


class ExecutionBusy(GraftError):
    code = "execution_busy"


class ProcessLock:
    def __init__(self, path: Path):
        self.path, self.stream = path, None

    def acquire(self) -> bool:
        prepare_directory(self.path.parent)
        if self.path.is_symlink() or self.path.is_junction():
            raise ExecutionBusy("Execution lock is unavailable.")
        stream = os.fdopen(os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600), "r+b")
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            return False
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b"0")
            stream.flush()
            stream.seek(0)
        self.stream = stream
        return True

    def close(self):
        if self.stream:
            self.stream.close()
            self.stream = None

    def __enter__(self):
        if not self.acquire():
            raise ExecutionBusy("Another workflow is running. Retry after it finishes.")
        return self

    def __exit__(self, *_):
        self.close()


@contextmanager
def cli_execution(root: Path):
    """CLI and API spend use the same local account ledger and execution policy."""
    from app.api.storage import RunStore
    from app.config import Settings
    from app.jobs.control import ExecutionContext, execution_context

    with ProcessLock(root / "data/execution.lock"):
        settings = Settings()
        store = RunStore(root / "data/web/runs.sqlite3", settings)
        with execution_context(
            ExecutionContext(
                "local-workspace",
                store,
                artifact_root=root / "data/artifacts",
                max_seconds=settings.job_max_seconds,
            )
        ):
            yield
