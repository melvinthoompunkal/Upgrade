"""Atomic, run-local artifacts with a verified manifest and snapshot envelopes."""

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from app.baseline_contracts import ArtifactDigest
from app.contracts import validate_repo_path
from app.errors import EvidenceError, GraftError
from app.tools.evidence import EvidenceSession
from app.tools.runner_artifacts import read_regular_file


class ArtifactStore:
    def __init__(self, directory: Path, binding: dict):
        self.directory = directory
        self.binding = binding
        self.hashes: dict[str, str] = {}

    def publish(self, artifact_id: str, content: bytes) -> ArtifactDigest:
        validate_repo_path(artifact_id)
        path = self.directory / artifact_id
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(content)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return self.adopt(artifact_id)

    def json(self, artifact_id: str, data: object) -> ArtifactDigest:
        return self.publish(artifact_id, json.dumps(data, indent=2, ensure_ascii=True).encode())

    def derived(self, artifact_id: str, data: object, *, call=None) -> ArtifactDigest:
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        return self.json(
            artifact_id,
            {
                "schema_version": "1",
                **self.binding,
                "artifact_id": artifact_id,
                "content_sha256": hashlib.sha256(canonical).hexdigest(),
                "model_id": call.model_id if call else None,
                "response_id": call.response_id if call else None,
                "data": data,
            },
        )

    def adopt(self, artifact_id: str) -> ArtifactDigest:
        digest = hashlib.sha256(read_regular_file(self.directory, artifact_id)).hexdigest()
        self.hashes[artifact_id] = digest
        return ArtifactDigest(artifact_id=artifact_id, sha256=digest)

    def verify(self) -> None:
        for artifact_id, digest in self.hashes.items():
            if hashlib.sha256(read_regular_file(self.directory, artifact_id)).hexdigest() != digest:
                raise EvidenceError("Published selection artifact changed after validation.")

    def verified_hashes(self) -> dict[str, str]:
        """Retain intact partial artifacts even if another output became unavailable."""
        verified = {}
        for artifact_id, expected in self.hashes.items():
            try:
                actual = hashlib.sha256(read_regular_file(self.directory, artifact_id)).hexdigest()
            except (GraftError, OSError, ValueError):
                continue
            if actual == expected:
                verified[artifact_id] = expected
        return verified


def read_log(evidence: EvidenceSession, events: list[dict]) -> dict:
    return {
        "repo_id": evidence.repo.snapshot.repo_id,
        "commit_sha": evidence.repo.snapshot.commit_sha,
        "read_bytes": evidence.read_bytes,
        "windows": evidence.windows,
        "tool_events": events,
        "qualification": "Read windows and static AST indexes do not imply full semantic coverage.",
    }


def feature_refs(card) -> tuple:
    return (
        *card.entry_points,
        *card.implementation,
        *card.source_tests,
        *card.license_refs,
        *(ref for dependency in card.dependencies for ref in dependency.refs),
    )
