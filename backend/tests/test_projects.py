"""Independent Python project admission, immutable pins, citations and native suite boundaries."""

import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.artifacts import RunArtifacts, digest, summary_from_report
from app.api.storage import RunStore
from app.config import Settings
from app.errors import EvidenceError
from app.main import create_app
from app.project_adapters import test_dependency_reasons as dependency_reasons
from app.project_analysis import record_project_analysis
from app.project_contracts import ProjectAnalysisRequest, ProjectRequest
from app.project_support import inspect_project
from app.projects import ProjectStore
from app.tools import runner
from app.tools.git import Git
from app.tools.repositories import RepositoryManager
from app.tools.runner_artifacts import parse_junit


@pytest.fixture
def independent(tmp_path, monkeypatch):
    root = tmp_path / "independent"
    root.mkdir()
    git = Git(tmp_path / "control")
    git.run("init", "--template=", str(root))
    files = {
        "library/pyproject.toml": (
            '[project]\nname="sequence-tools"\nrequires-python=">=3.11"\ndependencies=[]\n'
            '[tool.pytest.ini_options]\ntestpaths=["tests"]\npythonpath=["src"]\n'
        ),
        "library/src/sequence.py": "def chunks(items):\n    return [items]\n",
        "library/tests/conftest.py": (
            "import pytest\n@pytest.fixture\ndef items():\n    return [1, 2]\n"
        ),
        "library/tests/test_sequence.py": (
            "from sequence import chunks\n"
            "def test_chunks(items):\n    assert chunks(items) == [[1, 2]]\n"
        ),
        "library/LICENSE": "MIT License\nCopyright sequence-tools\n",
        "README.md": "No repository code may execute on the host.\n",
    }
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    git.run("add", "--all", cwd=root)
    git.run("-c", "commit.gpgSign=false", "commit", "-m", "independent Python library", cwd=root)
    sha = git.run("rev-parse", "HEAD", cwd=root).decode().strip()
    fetches = []

    def fetch(manager, url, commit, *, role, prefix=""):
        fetches.append((role, commit, prefix))
        storage = manager.directory / f"{role}.git"
        manager.git.run("clone", "--bare", "--no-local", "--template=", str(root), str(storage))
        return manager._inventory(storage, role, url, commit, prefix)

    monkeypatch.setattr(RepositoryManager, "public_snapshot", fetch)
    runs = RunStore(tmp_path / "web/runs.sqlite3")
    projects = ProjectStore(runs, tmp_path / "projects")
    return projects, root, git, sha, fetches


def pin(projects, sha, role="source", prefix="library", owner="local-workspace"):
    return projects.create(
        owner,
        ProjectRequest(
            url="https://github.com/team/sequence-tools",
            commit_sha=sha,
            role=role,
            project_root=prefix,
        ),
    )


def test_named_ref_resolves_once_before_pin_and_never_during_reopen(
    independent, tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from app import project_refs

    _, _, _, sha, _ = independent
    calls = []

    def resolve(method, path, **kwargs):
        calls.append(path)
        return {"ref": "refs/heads/release/v2", "object": {"type": "commit", "sha": sha}}

    monkeypatch.setattr(
        project_refs, "GitHubClient", lambda settings: SimpleNamespace(request=resolve)
    )
    app = create_app(Settings(_env_file=None), data_root=tmp_path / "ref-api", start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1:5173") as client:
        response = client.post(
            "/api/projects/resolve-ref",
            headers={"Origin": "http://127.0.0.1:5173"},
            json={
                "url": "https://github.com/team/sequence-tools",
                "kind": "branch",
                "name": "release/v2",
            },
        )
        assert response.status_code == 200 and response.json()["commit_sha"] == sha
        pinned = client.post(
            "/api/projects",
            headers={"Origin": "http://127.0.0.1:5173"},
            json={
                "url": "https://github.com/team/sequence-tools",
                "commit_sha": response.json()["commit_sha"],
                "role": "source",
                "project_root": "library",
            },
        )
        assert pinned.status_code == 201, pinned.text
        store = ProjectStore(app.state.runs, app.state.project_root)
        for _ in range(2):
            assert store.open("local-workspace", pinned.json()["id"])[0].snapshot.commit_sha == sha
        assert calls == ["/repos/team/sequence-tools/git/ref/heads/release%2Fv2"]
        matrix = client.get("/api/projects/support-matrix").json()
        assert matrix["runtime_profile"] == "python312-http-v2"
        assert not matrix["private_analysis_enabled"]


def test_independent_source_only_has_read_backed_structure_and_no_crawler(independent, tmp_path):
    projects, root, git, sha, fetches = independent
    source = pin(projects, sha)
    assert source.support.capability == "evidence_analysis"
    assert source.support.execution_supported
    assert source.support.adapter_version == "python-pytest-v1"
    assert source.support.manifest_sha256
    assert source.root_choices == ["library"]
    before = git.run("status", "--porcelain", cwd=root)
    report, path = record_project_analysis(
        ProjectAnalysisRequest(kind="project_analysis", source_project=source.id),
        projects,
        "local-workspace",
        tmp_path / "artifacts",
    )
    assert report["status"] == "passed"
    assert "target" not in report
    assert report["usage"]["requests"] == 0
    assert fetches == [("source", sha, "library")]
    assert git.run("status", "--porcelain", cwd=root) == before
    summary = summary_from_report("independent", "project_analysis", report)
    artifacts = RunArtifacts(
        path.parent.parent,
        {
            "summary": summary.model_dump_json(),
            "report": f"{path.parent.name}/report.json",
            "digest": digest(path.read_bytes()),
        },
    )
    detail = artifacts.detail()
    assert detail.project_analyses[0].entry_points[0].name == "chunks"
    assert detail.project_analyses[0].entry_points[0].repository_path == "library/src/sequence.py"
    assert detail.references and all(ref.excerpt_available for ref in detail.references)
    assert "Crawler" not in path.read_text()
    assert "MIT License" in (path.parent / "notices/source-notices.json").read_text()


def test_pins_exclude_dirty_files_and_do_not_refetch_after_original_head_moves(independent):
    projects, root, git, sha, fetches = independent
    source = pin(projects, sha)
    (root / "library/src/sequence.py").write_text("raise RuntimeError('dirty code')\n")
    selected, repo = projects.open("local-workspace", source.id)
    assert "def chunks" in repo.read("src/sequence.py")
    git.run("add", "--all", cwd=root)
    git.run("-c", "commit.gpgSign=false", "commit", "-m", "later head", cwd=root)
    assert projects.open("local-workspace", source.id)[0].snapshot.commit_sha == sha
    assert len(fetches) == 1 and selected.requested.project_root == "library"


def test_project_ids_are_owner_scoped_and_roles_cannot_be_swapped(independent):
    projects, _, _, sha, _ = independent
    source = pin(projects, sha)
    target = pin(projects, sha, role="target")
    with pytest.raises(HTTPException) as denied:
        projects.get("other-owner", source.id)
    assert denied.value.status_code == 404
    with pytest.raises(HTTPException) as denied:
        projects.admit(
            "local-workspace",
            ProjectAnalysisRequest(
                kind="project_analysis",
                source_project=target.id,
                target_project=source.id,
            ),
        )
    assert denied.value.status_code == 422


def test_missing_root_lists_inspected_manifests_and_blocks_analysis(independent):
    projects, _, _, sha, _ = independent
    root = pin(projects, sha, prefix="")
    assert root.root_choices == ["library"]
    assert root.support.capability == "browse_index"
    with pytest.raises(HTTPException) as denied:
        projects.admit(
            "local-workspace",
            ProjectAnalysisRequest(
                kind="project_analysis",
                source_project=root.id,
            ),
        )
    assert denied.value.status_code == 409


@pytest.mark.parametrize(
    "manifest, reason",
    [
        ('[project]\nname="sequence-tools"\nrequires-python=">=3.14"\n', "Python range"),
        ('[project]\nname="sequence-tools"\ndependencies=["requests"]\n', "Runtime dependencies"),
        ('[project]\nname="sequence-tools"\n[tool]\npytest="invalid"\n', "Pytest configuration"),
        (
            '[project]\nname="sequence-tools"\n[tool.pytest.ini_options]\naddopts="--ignore=tests"\n',
            "Pytest configuration",
        ),
    ],
)
def test_unsupported_execution_is_explained_but_static_analysis_remains(
    independent, manifest, reason
):
    projects, _, _, sha, _ = independent
    selected = pin(projects, sha)
    _, repo = projects.open("local-workspace", selected.id)
    original_read = repo.read

    class ChangedManifest:
        snapshot, inventory, prefix = repo.snapshot, repo.inventory, repo.prefix

        def read(self, path):
            return manifest if path == "pyproject.toml" else original_read(path)

    support = inspect_project(ChangedManifest())
    assert support.capability == "evidence_analysis"
    assert not support.execution_supported and not support.adaptation_supported
    assert any(reason in text for text in support.reasons)


def test_changed_inventory_is_rejected_before_analysis(independent):
    projects, _, _, sha, _ = independent
    selected = pin(projects, sha)
    with projects.runs.connect() as db:
        data = selected.model_dump(mode="json")
        data["inventory_sha256"] = "0" * 64
        db.execute("UPDATE projects SET descriptor=? WHERE id=?", (json.dumps(data), selected.id))
    with pytest.raises(EvidenceError, match="inventory changed"):
        projects.open("local-workspace", selected.id)


def test_native_pytest_keeps_conftest_and_native_unittest_uses_controller_runner(tmp_path):
    common = dict(
        name="f3-command",
        image_id="sha256:x",
        snapshot=tmp_path,
        acceptance=tmp_path,
        results=tmp_path,
    )
    command = runner.container_command(suite="project_baseline", **common)
    assert "--noconftest" not in command and "/snapshot/pyproject.toml" in command
    assert (
        "PYTHONPATH=/snapshot/src:/snapshot:/opt/runner" in command and "--network=none" in command
    )
    command = runner.container_command(suite="project_unittest", **common)
    assert command[-1] == "/opt/runner/unittest_runner.py"
    assert "--entrypoint" in command and "--noconftest" not in command


def test_native_pytest_case_identity_includes_class_and_module():
    cases = parse_junit(
        b'<testsuite><testcase classname="tests.One" name="test_ok"/>'
        b'<testcase classname="tests.Two" name="test_ok"/></testsuite>',
        qualified_names=True,
    )
    assert [case.name for case in cases] == ["tests.One.test_ok", "tests.Two.test_ok"]


def test_declared_test_dependencies_and_versions_must_be_prepared():
    assert not dependency_reasons(
        {"project": {}, "dependency-groups": {"tests": ["pytest>=9,<10"]}}
    )
    for value in ("pytest<9", "freezegun", "pytest @ https://example.com/package.whl"):
        assert dependency_reasons({"project": {"optional-dependencies": {"test": [value]}}})
    assert dependency_reasons(
        {"project": {}, "dependency-groups": {"tests": [{"include-group": "other"}]}}
    )


def test_failed_fetch_removes_only_its_generated_directory(independent, monkeypatch):
    projects, _, _, sha, _ = independent
    survivor = pin(projects, sha)

    def denied(*args, **kwargs):
        raise EvidenceError("Controlled fetch failure.")

    monkeypatch.setattr(RepositoryManager, "public_snapshot", denied)
    with pytest.raises(EvidenceError, match="Controlled fetch failure"):
        pin(projects, sha)
    assert [path.name for path in projects.root.iterdir()] == [survivor.id]


def test_api_preflight_and_source_only_queue_without_model_key(independent, tmp_path):
    _, _, _, sha, _ = independent
    settings = Settings(_env_file=None, model_api_key="")
    with TestClient(create_app(settings, data_root=tmp_path / "api", start_worker=False)) as client:
        client.app.state.worker.available = True
        response = client.post(
            "/api/projects",
            json={
                "url": "https://github.com/team/sequence-tools",
                "commit_sha": sha,
                "role": "source",
                "project_root": "library",
            },
        )
        assert response.status_code == 201, response.text
        selected = response.json()
        response = client.post(
            "/api/runs",
            json={
                "kind": "project_analysis",
                "source_project": selected["id"],
            },
            headers={"Idempotency-Key": "f3-independent-source-only"},
        )
        assert response.status_code == 202, response.text
        assert response.json()["kind"] == "project_analysis"
        assert client.get(f"/api/projects/{selected['id']}").json() == selected
        for url, root in [
            ("https://user:token@github.com/team/repo", "library"),
            ("https://github.com/team/repo", "../outside"),
        ]:
            assert (
                client.post(
                    "/api/projects",
                    json={
                        "url": url,
                        "commit_sha": sha,
                        "role": "source",
                        "project_root": root,
                    },
                ).status_code
                == 422
            )
        assert (
            client.post(
                "/api/projects",
                json={
                    "url": "https://github.com/team/repo",
                    "commit_sha": sha,
                    "role": "source",
                },
                headers={"Origin": "https://foreign.example"},
            ).status_code
            == 403
        )
        client.app.state.worker.available = False
