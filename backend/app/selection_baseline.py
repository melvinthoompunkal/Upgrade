"""Revalidate the current run's W1 evidence; historical receipt import is unsupported."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from app.baseline import ACCEPTANCE_FILES, EXPECTED_PASS, EXPECTED_RED, valid_feature_red
from app.baseline_contracts import BaselineBinding
from app.errors import EvidenceError
from app.pinned_baseline import (
    CONTROLLER_FILES,
    GREEN_CASES,
    RUNNER_ID,
    bind_capture,
    verify_export,
)
from app.tools.repositories import PinnedRepository
from app.tools.runner import PROJECT_ROOT, SANDBOX
from app.tools.runner_artifacts import TestCapture as Capture
from app.tools.runner_artifacts import TestCase as Case
from app.tools.runner_artifacts import file_hashes, read_regular_file
from app.tools.targets import CRAWLER_PREFIX


@dataclass(frozen=True)
class SelectionBaseline:
    directory: Path
    binding: BaselineBinding
    binding_sha256: str
    image_receipt: Path

    @classmethod
    def from_run(cls, directory: Path, target: PinnedRepository, image_receipt: Path):
        raw = read_regular_file(directory, "baseline-binding.json")
        evidence = cls(
            directory,
            BaselineBinding.from_w1(json.loads(raw)),
            hashlib.sha256(raw).hexdigest(),
            image_receipt,
        )
        evidence.verify(target)
        return evidence

    def verify(self, target: PinnedRepository) -> dict[str, Capture]:
        b = self.binding
        if (
            b.run_id != self.directory.name
            or b.target_repo_id != target.snapshot.repo_id
            or b.target_sha != target.snapshot.commit_sha
            or b.target_path_prefix != target.prefix
            or target.prefix != CRAWLER_PREFIX
            or b.runner_id != RUNNER_ID
            or hashlib.sha256(
                read_regular_file(self.directory, "baseline-binding.json")
            ).hexdigest()
            != self.binding_sha256
        ):
            raise EvidenceError("Baseline binding does not belong to this run's exact target.")
        expected = {entry.path: entry.sha256 for entry in target.inventory.files}
        exported = {entry.path: entry.sha256 for entry in b.exported_files}
        acceptance = {entry.path: entry.sha256 for entry in b.acceptance_files}
        definitions = {entry.path: entry.sha256 for entry in b.runner_definition_hashes}
        if (
            exported != expected
            or not verify_export(self.directory / "snapshot", exported)
            or not verify_export(self.directory / "acceptance", acceptance)
            or file_hashes(SANDBOX / "acceptance", ACCEPTANCE_FILES) != acceptance
            or file_hashes(PROJECT_ROOT, CONTROLLER_FILES) != definitions
            or file_hashes(self.image_receipt.parent, (self.image_receipt.name,))[
                self.image_receipt.name
            ]
            != b.image_receipt_sha256
        ):
            raise EvidenceError("Baseline bytes, acceptance, image receipt, or runner changed.")
        captures = {}
        for bound in b.captures:
            raw = json.loads(read_regular_file(self.directory, bound.artifact_id))
            capture = Capture(
                **{
                    **raw,
                    "command": tuple(raw["command"]),
                    "cases": tuple(Case(**case) for case in raw["cases"]),
                }
            )
            actual = bind_capture(
                capture, bound.suite, b.image_id, self.directory / f"test-captures/{bound.suite}"
            )
            if actual["sha256"] != bound.sha256 or actual["artifact_hashes"] != {
                entry.path: entry.sha256 for entry in bound.artifacts
            }:
                raise EvidenceError("Capture hashes differ from the current run's binding.")
            names = {case.name for case in capture.cases}
            if bound.suite != "acceptance":
                if capture.status != "passed" or names != GREEN_CASES[bound.suite]:
                    raise EvidenceError("Baseline does not pass all registered cases.")
            elif not (
                valid_feature_red(capture)
                or (capture.status == "passed" and names == set(EXPECTED_RED) | EXPECTED_PASS)
            ):
                raise EvidenceError("Diagnostic does not establish the registered retry behavior.")
            captures[bound.suite] = capture
        return captures
