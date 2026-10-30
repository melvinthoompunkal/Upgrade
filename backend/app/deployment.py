"""One-host deployment paths. Local CLI/API exclusion retains its historical domain."""

from pathlib import Path

from app.config import Settings


def data_directory(settings: Settings, root: Path | None = None) -> Path:
    from app.tools.runner import PROJECT_ROOT

    if settings.deployment_mode == "hosted" and root is not None and root != settings.data_root:
        raise ValueError("Hosted processes must use the configured DATA_ROOT.")
    return root or settings.data_root or PROJECT_ROOT / "data"


def execution_lock_path(settings: Settings) -> Path:
    from app.tools.runner import PROJECT_ROOT

    root = settings.data_root if settings.deployment_mode == "hosted" else PROJECT_ROOT / "data"
    return root / "execution.lock"
