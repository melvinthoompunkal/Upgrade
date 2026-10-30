"""Bounded read projections of the immutable F7 patch receipts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.adaptation_contracts import Digest
from app.api.contracts import Artifact
from app.contracts import RepoPath


class ChangedFile(BaseModel):
    id: str
    path: RepoPath
    status: Literal["added", "modified"]
    before_sha256: Digest | None
    after_sha256: Digest
    additions: int
    deletions: int
    diff_lines: int


class ChangeReview(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    verdict: Literal["verified", "unverified", "already_present"]
    outcome: str | None
    protected_tests_unchanged: bool
    original_target_unchanged: bool
    repair_count: int
    patch: Artifact | None = None
    mapping: Artifact | None = None
    files: list[ChangedFile] = Field(default_factory=list)
    evidence: list[Artifact] = Field(default_factory=list)
    publishing_enabled: Literal[False] = False


class DiffLine(BaseModel):
    kind: Literal["header", "hunk", "context", "addition", "deletion", "marker"]
    text: str
    old_line: int | None = None
    new_line: int | None = None
    truncated: bool = False


class DiffPage(BaseModel):
    file: ChangedFile
    patch_sha256: Digest
    offset: int
    next_offset: int | None
    total_lines: int
    lines: list[DiffLine]
