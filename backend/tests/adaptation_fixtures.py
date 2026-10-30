"""Independent pinned projects and a controlled provider; target execution stays in Docker."""

import asyncio
import json
import shutil
from dataclasses import asdict
from xml.etree import ElementTree as ET

import httpx
from test_catalogs import Provider
from test_catalogs import execute as discover
from test_catalogs import submit as catalog_submit
from test_meta_client import completion, settings
from test_projects import pin
from test_relevance import execute as assess
from test_relevance import submit as assessment_submit

from app.adaptation_contracts import AdaptationRequest
from app.adaptation_policy import MIT_BODY
from app.adaptation_workflow import record_adaptation
from app.pinned_baseline import GREEN_CASES
from app.project_contracts import ProjectRequest
from app.tools.repositories import RepositoryManager
from app.tools.runner import container_command
from app.tools.runner_artifacts import TestCapture, classify_result, file_hashes, parse_junit

OWNER = "local-workspace"
IMAGE = "sha256:" + "a" * 64
SOURCE_CODE = "def chunks(items):\n    return [items] if items else []\n"
TARGET_CODE = "def chunks(items):\n    return [items]\n"


def context(independent, tmp_path, monkeypatch):
    projects, root, git, _, _ = independent
    (root / "library/LICENSE").write_text(
        "MIT License\nCopyright sequence-tools\n\n" + MIT_BODY + "\n"
    )
    (root / "library/src/sequence.py").write_text(SOURCE_CODE)
    git.run("add", "--all", cwd=root)
    git.run(
        "-c", "commit.gpgSign=false", "commit", "-m", "source empty-sequence behavior", cwd=root
    )
    source = pin(projects, git.run("rev-parse", "HEAD", cwd=root).decode().strip())
    library = projects, source, root, git
    catalogs, queued = catalog_submit(library)
    catalog, _, _, _ = discover(library, catalogs, queued, tmp_path, Provider(source))
    projects.runs.transition(catalog.run_id, stage="complete", message="Done", status="finished")
    target_root = tmp_path / "independent-target"
    target_root.mkdir()
    for source_file in (root / "library").rglob("*"):
        if source_file.is_file():
            destination = target_root / source_file.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_file, destination)
    (target_root / "library/src/sequence.py").write_text(TARGET_CODE)
    git.run("init", "--template=", str(target_root))
    git.run("add", "--all", cwd=target_root)
    git.run(
        "-c",
        "commit.gpgSign=false",
        "commit",
        "-m",
        "target with missing empty behavior",
        cwd=target_root,
    )
    target_sha = git.run("rev-parse", "HEAD", cwd=target_root).decode().strip()

    def fetch_target(manager, url, commit, *, role, prefix=""):
        assert role == "target" and url == "https://github.com/team/sequence-target"
        storage = manager.directory / "target.git"
        manager.git.run(
            "clone", "--bare", "--no-local", "--template=", str(target_root), str(storage)
        )
        return manager._inventory(storage, role, url, commit, prefix)

    monkeypatch.setattr(RepositoryManager, "public_snapshot", fetch_target)
    target = projects.create(
        OWNER,
        ProjectRequest(
            url="https://github.com/team/sequence-target",
            commit_sha=target_sha,
            role="target",
            project_root="library",
        ),
    )
    store, queued = assessment_submit((catalogs, catalog, target))
    view, _, _, _ = assess(library, store, queued, tmp_path)
    feature = next(
        f for f in catalogs.page(OWNER, catalog.id, 1).items if f.name == "Chunk sequences"
    )
    request = AdaptationRequest(
        kind="verified_adaptation", assessment_id=view.id, feature_id=feature.feature_id
    )
    return projects, request, source, target, root, git, target_root


class AdaptationProvider:
    def __init__(self, *, bad_rounds=0, content=None, corrupt_preimage=False):
        self.calls, self.implementations = [], 0
        self.bad_rounds, self.content = bad_rounds, content
        self.corrupt_preimage = corrupt_preimage

    def __call__(self, request):
        body = json.loads(request.content)
        self.calls.append(body)
        packet = json.loads(body["messages"][1]["content"])
        schema = body["response_format"]["json_schema"]["name"]
        if schema == "AdaptationPlan":
            if body["messages"][-1]["role"] != "tool":
                return httpx.Response(
                    200,
                    json=completion(
                        calls=[
                            {
                                "id": "target-read",
                                "type": "function",
                                "function": {
                                    "name": "read_range",
                                    "arguments": json.dumps(
                                        {
                                            "path": "src/sequence.py",
                                            "start_line": 1,
                                            "end_line": 2,
                                        }
                                    ),
                                },
                            }
                        ]
                    ),
                )
            source_ref = packet["feature"]["implementation"][0]
            target_ref = {
                **source_ref,
                "repo_id": "target",
                "commit_sha": packet["identity"]["target"]["sha"],
            }
            data = {
                "schema_version": "python-function-plan-v1",
                "included": [
                    {
                        "id": "empty_sequence",
                        "behavior": "Empty sequences return [].",
                        "failure_semantics": "Exceptions propagate unchanged.",
                        "source_refs": [source_ref],
                    }
                ],
                "excluded": ["No chunk-size parameter or streaming behavior."],
                "integration_refs": [target_ref],
                "production_paths": ["src/sequence.py"],
                "new_runtime_dependencies": [],
                "rationale": "Adapt the existing chunks API.",
            }
        elif schema == "AcceptanceSpec":
            data = {
                "schema_version": "python-function-acceptance-v1",
                "cases": [
                    {
                        "id": "empty",
                        "rule_id": "empty_sequence",
                        "module": "sequence",
                        "function": "chunks",
                        "args_json": "[[]]",
                        "kwargs_json": "{}",
                        "expected_json": "[]",
                        "expected_exception": None,
                        "exception_message": None,
                    }
                ],
            }
        else:
            self.implementations += 1
            code = (
                TARGET_CODE + "# pending\n"
                if self.implementations <= self.bad_rounds
                else SOURCE_CODE
            )
            current = packet["target_files"][0]
            # Repairs preserve the controller-inserted notice header.
            old = current["text"] or ""
            header = old[: old.index("def chunks")] if "def chunks" in old else ""
            data = {
                "files": [
                    {
                        "path": "src/sequence.py",
                        "content": self.content or header + code,
                        "before_sha256": "0" * 64 if self.corrupt_preimage else current["sha256"],
                    }
                ],
                "rationale": "Preserve ordinary behavior and handle an empty sequence.",
            }
        return httpx.Response(200, json=completion(data))


class Captures:
    def __init__(
        self,
        *,
        red="red",
        bad_rounds=0,
        baseline_failed=False,
        tamper=None,
        regression_failure=False,
    ):
        self.red, self.bad_rounds, self.baseline_failed = red, bad_rounds, baseline_failed
        self.tamper, self.regression_failure = tamper, regression_failure
        self.calls = []

    def __call__(self, *, suite, image_id, snapshot, acceptance, directory, timeout):
        self.calls.append((suite, snapshot.name))
        directory.mkdir(parents=True)
        results = directory / "results"
        results.mkdir()
        root = ET.Element("testsuite")
        names = (
            sorted(GREEN_CASES["isolation"])
            if suite == "isolation"
            else ["test_empty" if suite == "project_acceptance" else "test_chunks"]
        )
        round_index = int(snapshot.name[-1]) if snapshot.name.startswith("round-") else -1
        for name in names:
            case = ET.SubElement(root, "testcase", name=name)
            failed = (
                (suite == "project_baseline" and self.baseline_failed)
                or (suite == "project_baseline" and round_index >= 0 and self.regression_failure)
                or (
                    suite == "project_acceptance"
                    and (
                        (round_index < 0 and self.red != "green")
                        or 0 <= round_index < self.bad_rounds
                    )
                )
            )
            if failed:
                tag = "skipped" if self.red == "skip" else "failure"
                msg = (
                    "AssertionError: F7_EXPECTATION:empty"
                    if self.red != "exception"
                    else "ValueError: unrelated"
                )
                ET.SubElement(case, tag, message=msg).text = msg
        raw = ET.tostring(root)
        (results / "junit.xml").write_bytes(raw)
        (directory / "stdout.txt").write_text("Controlled runner evidence\n")
        (directory / "stderr.txt").write_text("")
        cases = parse_junit(raw, qualified_names=suite == "project_baseline")
        code = 1 if any(c.outcome != "passed" for c in cases) else 0
        capture = TestCapture(
            suite,
            classify_result(code, cases),
            code,
            1,
            container_command(
                name="graft-test-controlled",
                suite=suite,
                image_id=image_id,
                snapshot=snapshot,
                acceptance=acceptance,
                results=results,
            ),
            image_id,
            cases,
            file_hashes(directory, ("stdout.txt", "stderr.txt", "results/junit.xml")),
            "",
        )
        (directory / "capture.json").write_text(json.dumps(asdict(capture)))
        if self.tamper and round_index >= 0:
            self.tamper(snapshot, acceptance, directory)
        return capture


def execute(
    context, tmp_path, monkeypatch, *, provider=None, captures=None, request=None, docker=False
):
    from app import adaptation_runner, adaptation_workflow
    from app.models.meta_client import MetaClient

    projects, default_request, *_ = context
    provider = provider or AdaptationProvider()
    captures = captures or Captures()
    receipt = None
    if not docker:
        receipt = tmp_path / "image.json"
        receipt.write_text('{"controlled":true}')
        monkeypatch.setattr(adaptation_workflow, "prepared_image", lambda _: IMAGE)
        monkeypatch.setattr(adaptation_runner, "run_suite", captures)

    async def run():
        transport = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        async with MetaClient(settings(), http_client=transport) as client:
            return await record_adaptation(
                request or default_request,
                projects,
                OWNER,
                tmp_path / "adaptations",
                settings(),
                client=client,
                image_receipt=receipt,
            )

    report, path = asyncio.run(run())
    return report, path, provider, captures
