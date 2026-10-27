"""Exercise runner failure paths with disposable, self-authored probes inside Docker only."""

import json
from dataclasses import asdict
from uuid import uuid4

from app.tools.runner import PROJECT_ROOT, prepared_image, run_suite

PROBES = {
    "collection-error": ("import graft_intentionally_missing_package\n", "error", 60),
    "skipped-test": (
        "import pytest\n@pytest.mark.skip(reason='probe')\ndef test_skip(): pass\n",
        "error",
        60,
    ),
    "test-timeout": ("import time\ndef test_hang(): time.sleep(30)\n", "timeout", 60),
    "container-timeout": ("import time\ndef test_hang(): time.sleep(30)\n", "timeout", 1),
    "output-limit": (
        "import os\ndef test_loud():\n"
        "    os.write(1, b'x' * 3000000)\n    assert False, 'output probe'\n",
        "error",
        60,
    ),
}


def main() -> int:
    image_id = prepared_image(PROJECT_ROOT / "data" / "runner-image.json")
    directory = PROJECT_ROOT / "data" / "artifacts" / f"runner-probes-{uuid4().hex}"
    directory.mkdir(parents=True)
    captures = {}
    for name, (code, expected, timeout) in PROBES.items():
        root = directory / name
        snapshot = root / "snapshot"
        (snapshot / "src").mkdir(parents=True)
        (snapshot / "tests").mkdir()
        acceptance = root / "acceptance"
        acceptance.mkdir()
        (snapshot / "tests" / "test_crawler.py").write_text(code, encoding="utf-8")
        capture = run_suite(
            suite="baseline",
            image_id=image_id,
            snapshot=snapshot,
            acceptance=acceptance,
            directory=root / "capture",
            timeout=timeout,
        )
        captures[name] = {"expected": expected, "capture": asdict(capture)}
        if name == "output-limit":
            captures[name]["diagnostic_verified"] = (
                capture.diagnostic == "Container output exceeded the capture limit."
                and "stdout.txt" in capture.artifact_hashes
            )
        print(f"{name}: {capture.status} (expected {expected})", flush=True)
    passed = all(
        value["expected"] == value["capture"]["status"] and value.get("diagnostic_verified", True)
        for value in captures.values()
    )
    report = directory / "report.json"
    report.write_text(
        json.dumps({"status": "passed" if passed else "failed", "probes": captures}, indent=2),
        encoding="utf-8",
    )
    print(f"Report: {report}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
