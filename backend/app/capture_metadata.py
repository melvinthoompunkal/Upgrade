"""One strict decoding boundary for hash-bound runner capture metadata."""

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.errors import EvidenceError
from app.tools.runner_artifacts import TestCapture, TestCase


class CaseMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1)
    outcome: Literal["passed", "failed", "error", "skipped"]
    message: str
    detail: str


class CaptureMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    suite: str = Field(min_length=1)
    status: Literal["passed", "failed", "error", "timeout"]
    exit_code: int
    elapsed_ms: int = Field(ge=0)
    command: list[str]
    image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    cases: list[CaseMetadata]
    artifact_hashes: dict[str, str]
    diagnostic: str


def decode_capture(raw: bytes, expected_sha256: str) -> TestCapture:
    # Do not let damaged bytes reach JSON/constructor handling before authentication.
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise EvidenceError("Capture bytes differ from the current run's binding.")
    try:
        data = CaptureMetadata.model_validate_json(raw)
        if any(
            len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
            for value in data.artifact_hashes.values()
        ):
            raise ValueError("Invalid digest")
        return TestCapture(
            suite=data.suite,
            status=data.status,
            exit_code=data.exit_code,
            elapsed_ms=data.elapsed_ms,
            command=tuple(data.command),
            image_id=data.image_id,
            cases=tuple(TestCase(**case.model_dump()) for case in data.cases),
            artifact_hashes=data.artifact_hashes,
            diagnostic=data.diagnostic,
        )
    except (ValidationError, ValueError, TypeError):
        raise EvidenceError("Capture metadata does not match the registered schema.") from None
