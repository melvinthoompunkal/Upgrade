"""Atomic, run-local artifacts with a verified manifest and snapshot envelopes."""

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from app.baseline_contracts import ArtifactDigest
from app.contracts import validate_repo_path
from app.errors import EvidenceError, GraftError
from app.tools.evidence import EvidenceSession
from app.tools.runner_artifacts import physical_root, read_regular_file


def prepare_directory(directory: Path) -> Path:
    """Check existing ancestors before mkdir, then verify the newly created root.

    This rejects pre-existing local redirection. Concurrent hostile path replacement
    requires an OS handle-based boundary and is outside the local application's scope.
    """
    for part in (directory.absolute(), *directory.absolute().parents):
        if part.is_symlink() or part.is_junction():
            raise EvidenceError("Artifact directories must not contain links or junctions.")
    from app.jobs.resources import own_artifact_directory

    own_artifact_directory(directory)
    directory.mkdir(parents=True, exist_ok=True)
    return physical_root(directory)


class ArtifactStore:
    def __init__(self, directory: Path, binding: dict):
        self.directory = directory
        self.binding = binding
        self.hashes: dict[str, str] = {}

    def publish(self, artifact_id: str, content: bytes) -> ArtifactDigest:
        from app.jobs.control import checkpoint

        checkpoint()
        validate_repo_path(artifact_id)
        root = prepare_directory(self.directory)
        path = root / artifact_id
        from app.jobs.resources import check_artifact_size

        check_artifact_size(root, len(content), replacing=path)
        prepare_directory(path.parent)
        if path.is_symlink() or path.is_junction() or (path.exists() and not path.is_file()):
            raise EvidenceError("Artifact destination must be a regular file.")
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
        from app.jobs.control import CURRENT

        context = CURRENT.get()
        if context and context.run_id and context.artifact_root:
            relative = self.directory.absolute().relative_to(context.artifact_root.absolute())
            if len(relative.parts) == 1:
                with context.store.connect() as db:
                    from app.jobs.control import fence_in

                    db.execute("BEGIN IMMEDIATE")
                    fence_in(db)
                    db.execute(
                        "INSERT OR REPLACE INTO published_artifacts VALUES (?,?,?,?)",
                        (
                            context.run_id,
                            relative.as_posix(),
                            artifact_id,
                            digest,
                        ),
                    )
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
        **(
            {
                "schema_version": "text-reads-v2",
                "project_id": evidence.project_id,
                "project_root": evidence.repo.prefix,
                "searches": evidence.searches,
                "coverage": evidence.coverage().model_dump(mode="json"),
            }
            if hasattr(evidence, "project_id")
            else {}
        ),
        "qualification": "Read windows and static AST indexes do not imply full semantic coverage.",
    }


def feature_refs(card) -> tuple:
    return (
        *card.entry_points,
        *card.implementation,
        *card.source_tests,
        *card.license_refs,
        *(ref for dependency in card.dependencies for ref in dependency.refs),
        *(anchor.ref for anchor in getattr(card, "symbol_anchors", ())),
    )
