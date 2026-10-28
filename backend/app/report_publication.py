"""Publish the structured receipt independently of optional human-readable output."""

from collections.abc import Callable

from app.errors import GraftError
from app.selection_artifacts import ArtifactStore


class PublicationError(GraftError):
    code = "publication_failed"


def publish_json_report(store: ArtifactStore, report: dict, required: tuple[str, ...] = ()) -> None:
    """Keep intact outputs in the final manifest even when another publication failed."""
    report["artifact_hashes"] = store.verified_hashes()
    report["unavailable_artifacts"] = [p for p in required if p not in report["artifact_hashes"]]
    try:
        store.json("report.json", report)
    except (GraftError, OSError, ValueError, TypeError):
        raise PublicationError(
            "The structured receipt could not be saved. Check artifact directory access."
        ) from None


def publish_report(
    store: ArtifactStore, report: dict, required: tuple[str, ...], markdown: Callable[[], str]
) -> None:
    report["artifact_hashes"] = store.verified_hashes()
    try:
        store.publish("report.md", markdown().encode())
        report["artifact_hashes"]["report.md"] = store.hashes["report.md"]
    except Exception:
        # Rendering is optional, including formatter bugs; never lose the JSON receipt.
        report.setdefault("warnings", []).append("The optional human report could not be saved.")
    report["stage_completed"] = report["stage"]
    publish_json_report(store, report, required)
