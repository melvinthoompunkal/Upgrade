"""Service inputs shared by terminal and browser adapters."""

from dataclasses import dataclass
from pathlib import Path

from app.config import Settings


@dataclass(frozen=True)
class WorkflowContext:
    artifact_root: Path
    settings: Settings


@dataclass(frozen=True)
class TraceInputs:
    source_url: str
    source_sha: str
    target: str
    goal: str


@dataclass(frozen=True)
class SelectionInputs(TraceInputs):
    image_receipt: Path
