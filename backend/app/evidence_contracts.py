"""Immutable evidence artifacts; JSON collections remain ordinary arrays."""

from typing import Literal

from pydantic import Field

from app.contracts import CommitSHA, Contract, FrozenList, NonEmptyString, Ref, RepoPath


class InventoryFile(Contract):
    path: RepoPath
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)
    language: str


class Inventory(Contract):
    repo_id: str
    files: FrozenList[InventoryFile]
    excluded_paths: FrozenList[str]
    limits_reached: FrozenList[str]


class Coverage(Contract):
    examined_paths: FrozenList[RepoPath]
    excluded_paths: FrozenList[str]
    unexamined_areas: FrozenList[str]
    limits_reached: FrozenList[str]


class Dependency(Contract):
    name: NonEmptyString
    kind: Literal["stdlib", "runtime", "test"]
    refs: FrozenList[Ref] = Field(min_length=1)


class FeatureCard(Contract):
    feature_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    name: NonEmptyString
    behavior: NonEmptyString
    entry_points: FrozenList[Ref]
    implementation: FrozenList[Ref]
    dependencies: FrozenList[Dependency]
    source_tests: FrozenList[Ref]
    acceptance_behaviors: FrozenList[NonEmptyString] = Field(min_length=1)
    transferable_slice: NonEmptyString
    excluded_behaviors: FrozenList[str]
    license_spdx: str | None
    license_refs: FrozenList[Ref]
    evidence_status: Literal["supported", "partial", "unsupported"]


class SourceTrace(Contract):
    source_sha: CommitSHA
    features: FrozenList[FeatureCard] = Field(min_length=1, max_length=8)
    coverage: Coverage


class Symbol(Contract):
    name: str
    kind: Literal["class", "function", "async_function", "assignment", "import"]
    start_line: int
    end_line: int


class PythonEvidence(Contract):
    path: RepoPath
    symbols: FrozenList[Symbol]
    imports: FrozenList[str]
    parse_error: str | None


class ReadRange(Contract):
    path: RepoPath
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class SearchSymbols(Contract):
    query: str = Field(min_length=1, max_length=100)
