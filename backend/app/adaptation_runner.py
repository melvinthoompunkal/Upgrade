"""Hash-bound isolated execution for exact patch bytes and immutable acceptance."""

from dataclasses import asdict

from app.adaptation_policy import sha256
from app.capture_metadata import decode_capture
from app.errors import EvidenceError
from app.pinned_baseline import GREEN_CASES, verify_export
from app.tools.runner import container_command, run_suite
from app.tools.runner_artifacts import classify_result, file_hashes, parse_junit, read_regular_file


class AdaptationRunner:
    def __init__(self, artifacts, image, budget, frozen, controllers, image_receipt):
        self.artifacts, self.image, self.budget = artifacts, image, budget
        self.frozen, self.controllers, self.image_receipt = frozen, controllers, image_receipt
        self.captures = []

    def check(self, snapshot, inputs):
        from app.tools.runner import PROJECT_ROOT

        self.budget.check()
        self.artifacts.verify()
        if file_hashes(PROJECT_ROOT, tuple(self.controllers)) != self.controllers:
            raise EvidenceError("Controller or runner definitions changed after freezing.")
        if (
            sha256(read_regular_file(self.image_receipt.parent, self.image_receipt.name))
            != (self.frozen["image_receipt_sha256"])
        ):
            raise EvidenceError("Runner image receipt changed after freezing.")
        if not verify_export(snapshot, inputs) or not verify_export(
            self.artifacts.directory / "acceptance", self.frozen["acceptance_hashes"]
        ):
            raise EvidenceError("Exact production inputs or frozen acceptance changed.")

    def run(self, suite, label, snapshot, inputs):
        self.check(snapshot, inputs)
        relative = f"test-captures/{label}/{suite}"
        directory = self.artifacts.directory / relative
        capture = run_suite(
            suite=suite,
            image_id=self.image,
            snapshot=snapshot,
            acceptance=self.artifacts.directory / "acceptance",
            directory=directory,
            timeout=min(60, max(1, self.budget.max_seconds - self.elapsed())),
        )
        # Retain complete available evidence even when a timeout/output limit prevents parsing.
        self.artifacts.adopt(f"{relative}/capture.json")
        for path in capture.artifact_hashes:
            self.artifacts.adopt(f"{relative}/{path}")
        self.captures.append({**asdict(capture), "phase": label})
        self.check(snapshot, inputs)
        recorded = decode_capture(
            read_regular_file(directory, "capture.json"),
            self.artifacts.hashes[f"{relative}/capture.json"],
        )
        if recorded != capture or capture.suite != suite or capture.image_id != self.image:
            raise EvidenceError("Runner capture does not match the requested suite and image.")
        command = capture.command
        if (
            len(command) < 4
            or command[:3] != ("docker", "create", "--name")
            or command
            != (
                container_command(
                    name=command[3],
                    suite=suite,
                    image_id=self.image,
                    snapshot=snapshot,
                    acceptance=self.artifacts.directory / "acceptance",
                    results=directory / "results",
                )
            )
        ):
            raise EvidenceError("Runner command differs from the registered isolation policy.")
        if capture.status in {"error", "timeout"}:
            raise EvidenceError("Isolated execution failed; capture evidence is retained.")
        paths = ("stdout.txt", "stderr.txt", "results/junit.xml")
        if file_hashes(directory, paths) != capture.artifact_hashes:
            raise EvidenceError("Captured log/JUnit hashes do not match the runner receipt.")
        cases = parse_junit(
            read_regular_file(directory, "results/junit.xml"),
            qualified_names=suite == "project_baseline",
        )
        if cases != capture.cases or classify_result(capture.exit_code, cases) != capture.status:
            raise EvidenceError("Capture cases and verdict disagree with the JUnit evidence.")
        if suite == "isolation" and (
            capture.status != "passed" or {c.name for c in cases} != GREEN_CASES["isolation"]
        ):
            raise EvidenceError("All reviewed isolation checks must pass.")
        return capture

    def elapsed(self):
        from time import monotonic

        return monotonic() - self.budget.started
