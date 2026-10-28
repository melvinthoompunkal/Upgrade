"""Audit reproductions at decoding and filesystem boundaries."""

import hashlib
import json
import os
import subprocess

import pytest

from app.capture_metadata import decode_capture
from app.errors import EvidenceError, GraftError
from app.selection_artifacts import ArtifactStore


@pytest.mark.parametrize(
    "change", ["missing", "extra", "string-integer", "boolean-integer", "nested-extra"]
)
def test_even_hash_matching_capture_requires_strict_metadata(baseline_context, change):
    _, baseline, _ = baseline_context
    raw = (baseline.directory / "test-captures/baseline/capture.json").read_bytes()
    data = json.loads(raw)
    if change == "missing":
        del data["diagnostic"]
    elif change == "extra":
        data["unexpected"] = True
    elif change == "string-integer":
        data["elapsed_ms"] = "1"
    elif change == "boolean-integer":
        data["exit_code"] = False
    else:
        data["cases"][0]["extra"] = "invalid"
    raw = json.dumps(data).encode()
    with pytest.raises(EvidenceError, match="schema"):
        decode_capture(raw, hashlib.sha256(raw).hexdigest())


def test_digest_check_precedes_json_decoding():
    with pytest.raises(EvidenceError, match="bytes differ"):
        decode_capture(b"not even JSON", "0" * 64)


@pytest.mark.parametrize("redirect_root", [False, True])
def test_artifact_junction_rejected_before_any_outside_write(tmp_path, redirect_root):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "artifacts"
    root.mkdir()
    link = root / "exports"
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    store = ArtifactStore(link if redirect_root else root, {})
    try:
        with pytest.raises(GraftError):
            store.publish("probe.txt" if redirect_root else "exports/probe.txt", b"must not write")
        assert not list(outside.iterdir())
    finally:
        if os.name == "nt":
            os.rmdir(link)
        else:
            link.unlink()
