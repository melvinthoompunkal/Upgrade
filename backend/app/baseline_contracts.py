"""Selection-facing view of a completed, controller-owned W1 baseline run.

These contracts validate shape and consistency, not artifact contents. W1 verifies
the files and captures before returning them; later consumers must retain that
run ownership and verify hashes when reading artifacts again.
"""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.contracts import CommitSHA, Contract, FrozenList, NonEmptyString, RepoPath

SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ImageID = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
SuiteName = Literal["isolation", "baseline", "acceptance"]


class FileDigest(Contract):
    path: RepoPath
    sha256: SHA256


class ArtifactDigest(Contract):
    artifact_id: RepoPath
    sha256: SHA256


class CaptureBinding(ArtifactDigest):
    suite: SuiteName
    artifacts: FrozenList[FileDigest] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def registered_artifacts(self) -> "CaptureBinding":
        if self.artifact_id != f"test-captures/{self.suite}/capture.json":
            raise ValueError("Capture artifact ID must match its registered suite")
        if {item.path for item in self.artifacts} != {
            "stdout.txt",
            "stderr.txt",
            "results/junit.xml",
        }:
            raise ValueError("Capture requires stdout, stderr, and JUnit hashes exactly once")
        return self


class BaselineBinding(Contract):
    schema_version: Literal["1"]
    run_id: NonEmptyString
    target_repo_id: NonEmptyString
    target_sha: CommitSHA
    target_path_prefix: RepoPath
    exported_files: FrozenList[FileDigest] = Field(min_length=1)
    acceptance_files: FrozenList[FileDigest] = Field(min_length=1)
    runner_id: NonEmptyString
    image_id: ImageID
    image_receipt_sha256: SHA256
    runner_definition_hashes: FrozenList[FileDigest] = Field(min_length=1)
    captures: FrozenList[CaptureBinding] = Field(min_length=3, max_length=3)
    inputs_unchanged: bool

    @model_validator(mode="after")
    def complete_binding(self) -> "BaselineBinding":
        if not self.inputs_unchanged:
            raise ValueError("Selection requires unchanged baseline inputs")
        for entries in (self.exported_files, self.acceptance_files, self.runner_definition_hashes):
            if len({item.path for item in entries}) != len(entries):
                raise ValueError("Bound file paths must be unique within each manifest")
        if {capture.suite for capture in self.captures} != {"isolation", "baseline", "acceptance"}:
            raise ValueError("Selection requires all three registered capture bindings")
        return self

    @classmethod
    def from_w1(cls, record: dict) -> "BaselineBinding":
        """Normalize W1's hash maps without changing its existing receipt format.

        Call only for a successful record_pinned_baseline result. This is a typed
        conversion, not authorization to import or trust historical receipts.
        """
        if record.keys() != cls.model_fields.keys():
            raise ValueError("Expected exactly the W1 baseline binding fields")

        def manifest(value: object) -> list[FileDigest]:
            if not isinstance(value, dict):
                raise ValueError("Expected a W1 file-hash object")
            return [FileDigest(path=path, sha256=digest) for path, digest in value.items()]

        raw_captures = record["captures"]
        if not isinstance(raw_captures, (list, tuple)):
            raise ValueError("Expected W1 capture bindings")
        captures = []
        for capture in raw_captures:
            if not isinstance(capture, dict) or capture.keys() != {
                "suite",
                "artifact_id",
                "sha256",
                "artifact_hashes",
            }:
                raise ValueError("Expected exactly the W1 capture binding fields")
            captures.append(
                CaptureBinding(
                    suite=capture["suite"],
                    artifact_id=capture["artifact_id"],
                    sha256=capture["sha256"],
                    artifacts=manifest(capture["artifact_hashes"]),
                )
            )
        return cls.model_validate(
            {
                **record,
                "exported_files": manifest(record["exported_files"]),
                "acceptance_files": manifest(record["acceptance_files"]),
                "runner_definition_hashes": manifest(record["runner_definition_hashes"]),
                "captures": captures,
            }
        )
