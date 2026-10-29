"""Bounded file handling and honest test-result classification for the demo runner."""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

from app.contracts import validate_repo_path
from app.errors import GraftError

MAX_ARTIFACT_BYTES = 2 * 1024 * 1024


class RunnerError(GraftError):
    code = "runner_failed"


def physical_root(root: Path) -> Path:
    """Reject redirection in the supplied root and every ancestor before resolving it."""
    root = root.absolute()
    for part in (root, *root.parents):
        if part.is_symlink() or part.is_junction():
            raise RunnerError(
                "Inputs must not contain links or junctions, including root ancestors."
            )
    return root.resolve(strict=True)


def read_regular_file(root: Path, relative: str) -> bytes:
    """Reject traversal, links/junctions, special files, and unexpectedly large inputs."""
    validate_repo_path(relative)
    root = physical_root(root)
    path = root
    for part in Path(relative).parts:
        path = path / part
        if path.is_symlink() or path.is_junction():
            raise RunnerError("Runner inputs and artifacts must not contain links or junctions.")
    if not path.resolve(strict=True).is_relative_to(root) or not path.is_file():
        raise RunnerError("Expected a regular file within the registered directory.")
    with path.open("rb") as stream:
        content = stream.read(MAX_ARTIFACT_BYTES + 1)
    if len(content) > MAX_ARTIFACT_BYTES:
        raise RunnerError("Runner input or artifact exceeds the size limit.")
    return content


def file_hashes(root: Path, paths: tuple[str, ...]) -> dict[str, str]:
    return {p: hashlib.sha256(read_regular_file(root, p)).hexdigest() for p in paths}


def export_files(root: Path, paths: tuple[str, ...], destination: Path) -> dict[str, str]:
    """Only registered files enter the snapshot; .git, .env, and host files never enter it."""
    content = {path: read_regular_file(root, path) for path in paths}
    destination.mkdir(parents=True, exist_ok=False)
    for relative, data in content.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return {path: hashlib.sha256(data).hexdigest() for path, data in content.items()}


@dataclass(frozen=True)
class TestCase:
    name: str
    outcome: Literal["passed", "failed", "error", "skipped"]
    message: str
    detail: str


@dataclass(frozen=True)
class TestCapture:
    suite: str
    status: Literal["passed", "failed", "error", "timeout"]
    exit_code: int
    elapsed_ms: int
    command: tuple[str, ...]
    image_id: str
    cases: tuple[TestCase, ...]
    artifact_hashes: dict[str, str]
    diagnostic: str


def parse_junit(content: bytes, *, qualified_names: bool = False) -> tuple[TestCase, ...]:
    if len(content) > MAX_ARTIFACT_BYTES or b"<!DOCTYPE" in content or b"<!ENTITY" in content:
        raise RunnerError("JUnit report is oversized or contains unsupported XML declarations.")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        raise RunnerError("Runner did not produce valid JUnit XML.") from None
    if root.tag not in {"testsuite", "testsuites"}:
        raise RunnerError("Runner did not produce a JUnit test suite.")
    cases: list[TestCase] = []
    for node in root.iter("testcase"):
        outcome = "passed"
        message = detail = ""
        for tag, state in [("failure", "failed"), ("error", "error"), ("skipped", "skipped")]:
            child = node.find(tag)
            if child is not None:
                outcome = state
                message = child.get("message", "")
                detail = child.text or ""
                break
        name = node.get("name", "")
        if qualified_names and node.get("classname"):
            name = f"{node.get('classname')}.{name}"
        cases.append(TestCase(name, outcome, message, detail))
    if not cases or len({c.name for c in cases}) != len(cases):
        raise RunnerError("JUnit must contain uniquely named test cases.")
    return tuple(cases)


def classify_result(exit_code: int, cases: tuple[TestCase, ...]) -> str:
    outcomes = {case.outcome for case in cases}
    if any(case.message.startswith("Failed: Timeout (>") for case in cases):
        return "timeout"
    if exit_code == 0 and outcomes == {"passed"}:
        return "passed"
    if exit_code == 1 and "failed" in outcomes and outcomes <= {"passed", "failed"}:
        return "failed"
    return "error"
