"""Real offline Docker red-to-green/regression evidence for independent F11 project pins."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.adaptation_acceptance import (
    acceptance_state,
    render_acceptance,
    validate_acceptance,
)
from app.adaptation_contracts import PatchProposal, ProductionFile
from app.adaptation_policy import (
    MIT_BODY,
    sha256,
    source_notices,
    validate_patch,
    validate_plan,
)
from app.adaptation_runner import AdaptationRunner
from app.adaptation_workflow import CONTROLLERS
from app.errors import EvidenceError
from app.execution import cli_execution
from app.models.budget import ModelBudget
from app.project_support import inspect_project
from app.selection_artifacts import ArtifactStore, prepare_directory
from app.source_use import BSD_BODY
from app.tools.evidence import EvidenceSession
from app.tools.git import Git
from app.tools.repositories import Registration, RepositoryManager, verify_original
from app.tools.runner import PROJECT_ROOT, prepared_image
from app.tools.runner_artifacts import file_hashes, read_regular_file
from scripts.f11_fixtures import EXAMPLES, contracts


def repository(directory, code, tests, name, license_text):
    original = directory / "checkout"
    original.mkdir(parents=True)
    git = Git(directory / "control")
    git.run("init", "--template=", str(original))
    for path, text in {
        "pyproject.toml": (
            f'[project]\nname="{name}"\nrequires-python=">=3.12"\n'
            'dependencies=["httpx==0.28.1", "anyio==4.15.1"]\n'
            '[tool.pytest.ini_options]\ntestpaths=["tests"]\npythonpath=["src"]\n'
        ),
        "src/api.py": code,
        "tests/test_api.py": tests,
        "LICENSE": license_text,
    }.items():
        destination = original / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8", newline="\n")
    git.run("add", "--all", cwd=original)
    git.run("-c", "commit.gpgSign=false", "commit", "-m", name, cwd=original)
    manager = RepositoryManager(directory / "repositories")
    repo, state = manager.local_target(Registration(name, original, ""))
    if name.startswith("f11-source-"):
        repo = manager._inventory(
            repo.storage, "source", repo.snapshot.origin, repo.snapshot.commit_sha, ""
        )
    return repo, state, manager


def check(directory, name, example, image, image_receipt):
    directory.mkdir()
    before, after, tests, *_ = example
    spdx = "BSD-2-Clause" if name == "method_http" else "MIT"
    license_text = (
        "Copyright (c) 2026 F11 fixture contributors\n\n"
        + (BSD_BODY if spdx == "BSD-2-Clause" else MIT_BODY)
        + "\n"
    )
    source, source_state, source_manager = repository(
        directory / "source", after, tests, f"f11-source-{name}", license_text
    )
    target, target_state, target_manager = repository(
        directory / "target", before, tests, f"f11-target-{name}", license_text
    )
    support = inspect_project(target)
    if not support.execution_supported:
        raise AssertionError(support.reasons)
    plan, spec = contracts(source, target, example)
    sessions = [EvidenceSession(repo) for repo in (source, target)]
    for session, code in zip(sessions, (after, before), strict=True):
        session.read_range("src/api.py", 1, len(code.splitlines()))
    validate_plan(plan, *sessions)
    validate_acceptance(spec, plan, target)
    card = SimpleNamespace(
        license_spdx=spdx,
        license_refs=source.snapshot.license_refs,
        entry_points=plan.included[0].source_refs,
        implementation=[],
        source_tests=[],
        dependencies=[],
    )
    notice = source_notices(source, card)
    artifacts = ArtifactStore(directory, {})
    artifacts.json("contract.json", plan.model_dump(mode="json"))
    artifacts.json("acceptance-spec.json", spec.model_dump(mode="json"))
    artifacts.publish("acceptance/test_feature.py", render_acceptance(spec))
    artifacts.publish("source-NOTICE.txt", notice.encode())
    frozen = {
        "acceptance_hashes": {"test_feature.py": artifacts.hashes["acceptance/test_feature.py"]},
        "image_receipt_sha256": sha256(read_regular_file(image_receipt.parent, image_receipt.name)),
    }
    controllers = file_hashes(PROJECT_ROOT, CONTROLLERS)
    runner = AdaptationRunner(artifacts, image, ModelBudget(), frozen, controllers, image_receipt)
    original = {f.path: target.read(f.path).encode() for f in target.inventory.files}
    exported = target.export(tuple(original), directory / "original")
    runner.run("isolation", "before", directory / "original", exported)
    baseline = runner.run("project_baseline", "before", directory / "original", exported)
    if baseline.status != "passed":
        raise AssertionError(f"{name}: ordinary baseline failed")
    red = runner.run("project_acceptance", "before", directory / "original", exported)
    if acceptance_state(red, spec) != "red":
        raise AssertionError(f"{name}: no meaningful gap")
    proposal = PatchProposal(
        files=[
            ProductionFile(path="src/api.py", before_sha256=exported["src/api.py"], content=after)
        ],
        rationale="Apply independently specified source behavior within the frozen scope.",
    )
    patched, patch, mapping, _, _ = validate_patch(proposal, plan, original, original, notice)
    artifacts.publish("change.patch", patch)
    snapshot = directory / "patched"
    for path, raw in patched.items():
        destination = snapshot / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    hashes = {p: sha256(raw) for p, raw in patched.items()}
    green = runner.run("project_acceptance", "after", snapshot, hashes)
    regression = runner.run("project_baseline", "after", snapshot, hashes)
    rejections = []
    bad_code = {
        "async_http": (
            "async def fetch(url, *, client):\n"
            "    for _ in range(2):\n"
            "        try:\n            await client.get(url + '/unexpected')\n"
            "        except AssertionError:\n            pass\n"
            "    return {'value': 17}\n"
        ),
        "async_function": (
            "async def normalize(value):\n    raise RuntimeError('unrelated failure')\n"
        ),
    }.get(name)
    if bad_code:
        rejected = dict(patched)
        rejected["src/api.py"] = bad_code.encode()
        rejection_snapshot = directory / "rejection"
        for path, raw in rejected.items():
            destination = rejection_snapshot / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        rejection = runner.run(
            "project_acceptance",
            "rejection",
            rejection_snapshot,
            {p: sha256(raw) for p, raw in rejected.items()},
        )
        if name == "async_http":
            if acceptance_state(rejection, spec) != "red":
                raise AssertionError("Swallowed HTTP mismatches bypassed the adapter.")
            rejections.append("swallowed HTTP request mismatches remain assertion red")
        else:
            try:
                acceptance_state(rejection, spec)
            except EvidenceError:
                rejections.append("unrelated runtime errors cannot establish assertion red")
            else:
                raise AssertionError("Unrelated failure was mistaken for a behavior gap.")
    unchanged = verify_original(target_manager.git, target_state) and verify_original(
        source_manager.git, source_state
    )
    if acceptance_state(green, spec) != "green" or regression.status != "passed" or not unchanged:
        raise AssertionError(f"{name}: verification failed")
    return {
        "name": name,
        "fixture_kind": "independent generated source/target Git repositories",
        "source_sha": source.snapshot.commit_sha,
        "target_sha": target.snapshot.commit_sha,
        "support": support.model_dump(mode="json"),
        "license": spdx,
        "frozen": frozen,
        "input_hashes": exported,
        "controller_hashes": controllers,
        "baseline_green": True,
        "assertion_red": True,
        "unchanged_acceptance_green": True,
        "regression_green": True,
        "rejections": rejections,
        "originals_unchanged": unchanged,
        "mapping": mapping,
        "captures": runner.captures,
        "artifact_hashes": artifacts.verified_hashes(),
    }


def main():
    with cli_execution(PROJECT_ROOT):
        directory = PROJECT_ROOT / "data/artifacts" / f"f11-profiles-{uuid4().hex}"
        prepare_directory(directory)
        image_receipt = PROJECT_ROOT / "data/runner-image.json"
        image = prepared_image(image_receipt)
        checks = []
        for name, example in EXAMPLES.items():
            checks.append(check(directory / name, name, example, image, image_receipt))
            print(json.dumps({"profile": name, "status": "passed"}), flush=True)
        receipt = {
            "schema_version": "f11-profile-evidence-v1",
            "status": "passed",
            "created_at": datetime.now(UTC).isoformat(),
            "image_id": image,
            "checks": checks,
            "scope": (
                "Real Docker execution with controlled patches; no live model/private certification"
            ),
            "script_hashes": file_hashes(
                PROJECT_ROOT,
                (
                    "scripts/check_f11_profiles.py",
                    "scripts/f11_fixtures.py",
                ),
            ),
        }
        path = directory / "checks.json"
        path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print(json.dumps({"status": "passed", "receipt": str(path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
