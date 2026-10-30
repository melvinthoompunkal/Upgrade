"""Separate API/worker entry points with systemd's read-only credential delivery."""

import argparse
import os
import signal
import threading
from pathlib import Path

from app.api.storage import RunStore
from app.api.worker import LocalWorker
from app.auth.crypto import CredentialCipher
from app.auth.github import GitHubClient
from app.auth.store import AuthStore
from app.config import Settings
from app.deployment import data_directory
from app.selection_artifacts import prepare_directory


def deployment_settings() -> Settings:
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if not directory:
        raise ValueError("Hosted services require systemd credential delivery.")
    settings = Settings(_env_file=Path(directory) / "backend.env")
    if settings.deployment_mode != "hosted":
        raise ValueError("This entry point requires hosted mode.")
    return settings


def api():
    from app.main import create_app

    return create_app(deployment_settings(), start_worker=False)


def run_worker(settings: Settings, stopped: threading.Event) -> int:
    root = data_directory(settings)
    prepare_directory(root / "artifacts")
    store = RunStore(root / "web/runs.sqlite3", settings)
    auth = AuthStore(store, CredentialCipher(settings.token_encryption_keys.get_secret_value()))
    github = GitHubClient(settings)
    worker = LocalWorker(
        store,
        root / "artifacts",
        settings,
        connection_check=lambda owner: auth.current_access(owner, github),
    )
    worker.start()
    try:
        if not worker.available:
            return 1
        while not stopped.wait(0.5):
            if not worker.thread.is_alive():
                return 1
        return 0
    finally:
        worker.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("worker",))
    parser.parse_args()
    stopped = threading.Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: stopped.set())
    return run_worker(deployment_settings(), stopped)


if __name__ == "__main__":
    raise SystemExit(main())
