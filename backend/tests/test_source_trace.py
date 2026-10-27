import argparse
import asyncio
import json

from app import source_trace
from app.config import Settings


def test_missing_key_saves_sanitized_failure_receipt_before_git_or_inference(tmp_path, monkeypatch):
    monkeypatch.setattr(source_trace, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        source_trace, "Settings", lambda: Settings(_env_file=None, model_api_key=None)
    )
    args = argparse.Namespace(
        source_url="https://github.com/scrapy/scrapy",
        source_sha="a" * 40,
        target="crawler-demo",
        goal="bounded retries",
    )
    report, path = asyncio.run(source_trace.record_source_trace(args))
    assert report["status"] == "failed"
    assert report["error_code"] == "missing_configuration"
    assert json.loads(path.read_text()) == report
    assert not (path.parent / "repositories").exists()
