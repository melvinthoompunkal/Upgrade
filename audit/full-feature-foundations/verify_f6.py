"""Verify retained F6 source and test receipts without providers or execution."""

import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
RECEIPTS = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    receipt = json.loads((RECEIPTS / "f6-checks.json").read_text(encoding="utf-8"))
    for name, expected in receipt["source_files"].items():
        if digest(ROOT / name) != expected:
            raise ValueError(f"Source changed: {name}")
    for name, expected in receipt["artifact_sha256"].items():
        if digest(RECEIPTS / name) != expected:
            raise ValueError(f"Receipt changed: {name}")
        if not name.endswith(".xml"):
            continue
        root = ElementTree.parse(RECEIPTS / name).getroot()
        suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
        if any(
            int(s.get("errors", "0")) or int(s.get("failures", "0")) for s in suites
        ):
            raise ValueError(f"Receipt has failures: {name}")
    canonical = json.dumps(
        receipt["source_files"], sort_keys=True, separators=(",", ":")
    ).encode()
    if hashlib.sha256(canonical).hexdigest() != receipt["source_manifest_sha256"]:
        raise ValueError("Source manifest changed.")
    print(
        json.dumps(
            {
                "status": "passed",
                "source_files": len(receipt["source_files"]),
                "test_receipts": sum(name.endswith(".xml") for name in receipt["artifact_sha256"]),
            }
        )
    )


if __name__ == "__main__":
    main()
