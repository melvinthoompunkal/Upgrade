"""Durable worker liveness shared by API processes; no code or credentials in status."""

import time


class WorkerStatus:
    def __init__(self, store):
        self.store = store

    def heartbeat(self, worker: str, available: bool) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT INTO worker_status VALUES (1,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "worker=excluded.worker, heartbeat=excluded.heartbeat, "
                "available=excluded.available",
                (worker, time.time(), int(available)),
            )

    def stop(self, worker: str) -> None:
        with self.store.connect() as db:
            db.execute("DELETE FROM worker_status WHERE worker=?", (worker,))

    def available(self) -> bool:
        with self.store.connect() as db:
            row = db.execute("SELECT heartbeat,available FROM worker_status WHERE id=1").fetchone()
        return bool(row and row["available"] and time.time() - 15 < row["heartbeat"] <= time.time())


class RemoteWorker:
    """API-side view; only the standalone worker can claim or execute jobs."""

    def __init__(self, store):
        self.status = WorkerStatus(store)

    @property
    def available(self) -> bool:
        return self.status.available()

    def stop(self) -> None:
        pass
