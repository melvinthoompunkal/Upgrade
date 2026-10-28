"""Optional presentation must never prevent the required structured receipt."""

import hashlib
import json

import pytest

from app.report_publication import publish_report
from app.selection_artifacts import ArtifactStore


@pytest.mark.parametrize("error", [KeyError, TypeError, RuntimeError])
def test_markdown_formatter_failure_retains_original_json_and_artifacts(tmp_path, error):
    store = ArtifactStore(tmp_path, {})
    store.publish("evidence.json", b"{}")
    report = {
        "stage": "created",
        "status": "failed",
        "error_code": "invalid_evidence",
        "detail": "The evidence was rejected.",
        "analyzed_feature_count": 0,
        "eligible_feature_count": 0,
    }

    def render():
        raise error("private renderer details")

    publish_report(store, report, ("evidence.json", "report.md"), render)
    raw = (tmp_path / "report.json").read_bytes()
    assert json.loads(raw) == report
    assert report["error_code"] == "invalid_evidence"
    assert report["artifact_hashes"] == {"evidence.json": hashlib.sha256(b"{}").hexdigest()}
    assert report["unavailable_artifacts"] == ["report.md"]
    assert "private renderer" not in raw.decode()
