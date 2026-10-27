"""Reproduce log open/write/read failures for both streams through the real Docker gate."""

import json
from pathlib import Path
from uuid import uuid4

from app.baseline import record_baseline
from app.tools import runner


class FaultyIO:
    def __init__(self, stream, operation):
        self.stream = stream
        self.operation = operation

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def __enter__(self):
        self.stream.__enter__()
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def read(self, *args):
        if self.operation == "read":
            raise OSError("injected stream read failure")
        data = self.stream.read(*args)
        if self.operation == "supply_write" and not data:
            # The ordinary isolation suite emits no stderr. Supply one controller-side probe
            # chunk so the injected stderr write fault is actually exercised.
            self.operation = "done"
            return b"controller capture fault probe\n"
        return data

    def write(self, *args):
        if self.operation == "write":
            raise OSError("injected stream write failure")
        return self.stream.write(*args)


def check_fault(filename: str, operation: str) -> dict:
    original_open, original_popen = Path.open, runner.subprocess.Popen

    def open_file(path, mode="r", *args, **kwargs):
        selected = path.name == filename and path.parent.name == "isolation" and mode == "wb"
        if selected and operation == "open":
            raise OSError("injected log open failure")
        stream = original_open(path, mode, *args, **kwargs)
        return FaultyIO(stream, "write") if selected and operation == "write" else stream

    def popen(command, *args, **kwargs):
        process = original_popen(command, *args, **kwargs)
        if command[:3] == ["docker", "start", "--attach"] and operation in {"read", "write"}:
            attr = "stdout" if filename == "stdout.txt" else "stderr"
            selected = "read" if operation == "read" else "supply_write"
            setattr(process, attr, FaultyIO(getattr(process, attr), selected))
        return process

    try:
        Path.open, runner.subprocess.Popen = open_file, popen
        report, path = record_baseline(runner.PROJECT_ROOT / "data/runner-image.json")
    finally:
        Path.open, runner.subprocess.Popen = original_open, original_popen
    capture = report["captures"][0]
    passed = (
        report["status"] == "failed"
        and capture["status"] == "error"
        and filename in capture["diagnostic"]
        and "I/O error" in capture["diagnostic"]
        and len(report["captures"]) == 1
    )
    return {
        "filename": filename,
        "operation": operation,
        "passed": passed,
        "diagnostic": capture["diagnostic"],
        "report": str(path),
    }


def main() -> int:
    checks = [
        check_fault(filename, operation)
        for filename in ("stdout.txt", "stderr.txt")
        for operation in ("open", "write", "read")
    ]
    directory = runner.PROJECT_ROOT / "data/artifacts" / f"capture-faults-{uuid4().hex}"
    directory.mkdir()
    path = directory / "report.json"
    report = {
        "status": "passed" if all(c["passed"] for c in checks) else "failed",
        "checks": checks,
    }
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(path)}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
