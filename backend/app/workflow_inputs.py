"""Service inputs shared by terminal and browser adapters."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TraceInputs:
    source_url: str
    source_sha: str
    target: str
    goal: str


@dataclass(frozen=True)
class SelectionInputs(TraceInputs):
    image_receipt: Path
