"""Service inputs shared by terminal and browser adapters."""

from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.errors import UnsupportedSourceError
from app.tools.repositories import SCRAPY_URL


def require_crawler_source(source_url: str) -> None:
    """The legacy retry tracer is specific to Scrapy, including in source-only mode."""
    if source_url.removesuffix(".git").lower() != SCRAPY_URL:
        raise UnsupportedSourceError(
            "The crawler example supports only https://github.com/scrapy/scrapy. "
            "Use Feature discovery for other supported Python repositories. "
            "Node/TypeScript projects are not supported yet."
        )


@dataclass(frozen=True)
class WorkflowContext:
    artifact_root: Path
    settings: Settings


@dataclass(frozen=True)
class TraceInputs:
    source_url: str
    source_sha: str | None
    target: str
    goal: str


@dataclass(frozen=True)
class SelectionInputs(TraceInputs):
    image_receipt: Path
