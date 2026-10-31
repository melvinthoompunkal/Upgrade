"""Versioned read-only text analysis. Legacy Python evidence keeps its original meaning."""

from typing import Literal

from pydantic import Field

from app.contracts import CommitSHA, Contract, FrozenList, Ref, RepoPath
from app.evidence_contracts import Inventory, InventoryFile

TEXT_PROFILE = "repository-text-v1"
LEGACY_TEXT_POLICY = "repository-text-inventory-v1"
TEXT_POLICY = "repository-text-inventory-v2"
TEXT_POLICIES = {LEGACY_TEXT_POLICY, TEXT_POLICY}
LEGACY_POLICY = "python-inventory-v1"
TEXT_VERSIONS = {
    "analyzer": "text-catalog-v3",
    "prompt": "text-prompts-v3",
    "schema": "catalog-v2",
    "retrieval": "lexical-batch-windows-v3-input-preflight",
    "parsers": "disabled",
}
TEXT_RELEVANCE_VERSIONS = {
    "policy": "semantic-relevance-v2",
    "prompt": "text-expansion-v3-role-bound-schema",
    "fit": "unassessed",
}

FileRole = Literal["source", "test", "documentation", "manifest", "unknown"]


class TextInventoryFile(InventoryFile):
    role: FileRole
    classification_basis: str
    blob_oid: str
    lines: int = Field(ge=0)


class Exclusion(Contract):
    path: str
    reason: str


class TreeEntry(Contract):
    path: str
    object_type: str
    object_id: str
    bytes: int | None


class TextInventory(Inventory):
    files: FrozenList[TextInventoryFile]
    policy_version: Literal["repository-text-inventory-v1", "repository-text-inventory-v2"] = (
        TEXT_POLICY
    )
    tree_entries: FrozenList[TreeEntry]
    enumeration_complete: bool
    remaining_tree_entries: int
    exclusions: FrozenList[Exclusion]
    deferred_paths: FrozenList[str]
    qualification: str = "Incomplete enumeration requires a new pin with a smaller project root."


class TextRefV2(Ref):
    """Paths are original repository paths, including the selected project root."""

    schema_version: Literal["text-ref-v2"]
    project_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    project_root: str
    role: Literal["source", "target"]
    blob_sha256: str | None = Field(pattern=r"^[a-f0-9]{64}$")
    excerpt_sha256: str | None = Field(pattern=r"^[a-f0-9]{64}$")


class SymbolAnchorV2(Contract):
    ref: TextRefV2
    qualified_name: str
    parser_version: str | None
    grammar_version: str | None
    query_version: str | None
    validation: Literal["unvalidated", "parser_validated"]


class ReadRangeV2(Contract):
    path: RepoPath
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class ReadRangesV2(Contract):
    ranges: FrozenList[ReadRangeV2] = Field(min_length=1, max_length=8)


class TextSearch(Contract):
    query: str = Field(min_length=1, max_length=100)
    path_prefix: str | None
    offset: int = Field(ge=0, le=4000)
    start_line: int = Field(ge=1)


class ListPaths(Contract):
    path_prefix: str | None
    offset: int = Field(ge=0, le=4000)


class ReadWindowV2(Contract):
    path: RepoPath
    start_line: int
    end_line: int
    sha256: str


class LineInterval(Contract):
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class UnreadFile(Contract):
    path: RepoPath
    unread_ranges: FrozenList[LineInterval]


class CoverageV2(Contract):
    schema_version: Literal["coverage-v2"] = "coverage-v2"
    tree_enumeration_complete: bool
    admitted_paths: FrozenList[str]
    deferred_paths: FrozenList[str]
    excluded_paths: FrozenList[str]
    sampled_modules: FrozenList[str]
    examined_paths: FrozenList[str]
    read_ranges: FrozenList[ReadWindowV2]
    partially_read_files: FrozenList[UnreadFile]
    unexamined_areas: FrozenList[str]
    remaining_frontier: FrozenList[str]
    limits_reached: FrozenList[str]


class DependencyObservationV2(Contract):
    name: str = Field(min_length=1, max_length=160)
    kind: Literal["import_text", "manifest_declaration", "inferred_relationship"]
    resolution: Literal["observed", "unresolved"]
    refs: FrozenList[TextRefV2] = Field(min_length=1, max_length=4)


class FeatureCardV2(Contract):
    schema_version: Literal["feature-card-v2"]
    analysis_profile: Literal["repository-text-v1"]
    feature_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=120)
    behavior: str = Field(min_length=1, max_length=1200)
    entry_points: FrozenList[TextRefV2] = Field(max_length=8)
    implementation: FrozenList[TextRefV2] = Field(max_length=12)
    dependencies: FrozenList[DependencyObservationV2] = Field(max_length=8)
    source_tests: FrozenList[TextRefV2] = Field(max_length=8)
    acceptance_behaviors: FrozenList[str] = Field(min_length=1, max_length=8)
    transferable_slice: str = Field(min_length=1, max_length=600)
    excluded_behaviors: FrozenList[str] = Field(max_length=8)
    license_spdx: str | None
    license_refs: FrozenList[TextRefV2] = Field(max_length=4)
    evidence_status: Literal["supported", "partial", "unsupported"]
    structural_validation: Literal["text_only", "parser_validated", "mixed"]
    symbol_anchors: FrozenList[SymbolAnchorV2] = Field(max_length=8)
    execution_verification: Literal["not_run"]
    missing_evidence: FrozenList[str] = Field(max_length=12)


class SourceTraceV2(Contract):
    schema_version: Literal["source-trace-v2"]
    source_sha: CommitSHA
    features: FrozenList[FeatureCardV2] = Field(min_length=1, max_length=1)


class CandidateHintV2(Contract):
    behavior_key: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=600)
    category: str = Field(min_length=1, max_length=60)
    discovery_refs: FrozenList[TextRefV2] = Field(min_length=1, max_length=12)
    parent_key: str | None


class CandidateBatchV2(Contract):
    candidates: FrozenList[CandidateHintV2] = Field(max_length=8)
