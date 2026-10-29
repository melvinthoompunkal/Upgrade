"""Explicit Docker-only checks for native project collection and truthful failure capture."""

import json
from dataclasses import asdict
from uuid import uuid4

from app.project_support import inspect_project
from app.tools.git import Git
from app.tools.repositories import Registration, RepositoryManager, verify_original
from app.tools.runner import PROJECT_ROOT, prepared_image, run_suite
from app.tools.runner_artifacts import file_hashes

SOURCE = 'def double(value):\n    """\n    >>> double(3)\n    6\n    """\n    return value * 2\n'
UNITTEST = (
    "import unittest\nimport doctest\nimport sequence\n"
    "def load_tests(loader, tests, pattern):\n"
    "    tests.addTests(doctest.DocTestSuite(sequence))\n    return tests\n"
    "class APassing(unittest.TestCase):\n"
    "    def test_double(self):\n        self.assertEqual(sequence.double(4), 8)\n"
)
CASES = {
    "pytest_native": {
        "tests/conftest.py": "import pytest\n@pytest.fixture\ndef number():\n    return 4\n",
        "tests/test_sequence.py": (
            "from sequence import double\n"
            "def test_double(number):\n    assert double(number) == 8\n"
        ),
    },
    "unittest_native": {"tests/__init__.py": "", "tests/test_sequence.py": UNITTEST},
    "unittest_setup_error": {
        "tests/__init__.py": "",
        "tests/test_sequence.py": UNITTEST
        + (
            "class ZBroken(unittest.TestCase):\n"
            "    @classmethod\n    def setUpClass(cls):\n"
            "        raise RuntimeError('fixture failure')\n"
            "    def test_unreached(self):\n        self.fail('must not execute')\n"
        ),
    },
    "unittest_skip": {
        "tests/__init__.py": "",
        "tests/test_sequence.py": UNITTEST
        + (
            "class ZSkipped(unittest.TestCase):\n"
            "    @unittest.skip('controlled unsupported case')\n"
            "    def test_skipped(self):\n        self.fail('must not execute')\n"
        ),
    },
}


def main() -> int:
    directory = PROJECT_ROOT / "data" / f"f3-native-checks-{uuid4().hex}"
    directory.mkdir()
    acceptance = directory / "acceptance"
    acceptance.mkdir()
    image = prepared_image(PROJECT_ROOT / "data/runner-image.json")
    results = []
    for name, tests in CASES.items():
        operation = directory / name
        original = operation / "original"
        original.mkdir(parents=True)
        git = Git(operation / "control")
        git.run("init", "--template=", str(original))
        content = {
            "library/pyproject.toml": (
                '[project]\nname="native-sequence-fixture"\nrequires-python=">=3.11"\n'
                '[tool.pytest.ini_options]\ntestpaths=["tests"]\npythonpath=["src"]\n'
            ),
            "library/src/sequence.py": SOURCE,
            **{f"library/{path}": text for path, text in tests.items()},
        }
        for path, text in content.items():
            target = original / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        git.run("add", "--all", cwd=original)
        git.run("-c", "commit.gpgSign=false", "commit", "-m", name, cwd=original)
        manager = RepositoryManager(operation / "repositories")
        repo, state = manager.local_target(Registration("target", original, "library"))
        support = inspect_project(repo)
        if not support.execution_supported:
            raise AssertionError(support.reasons)
        paths = tuple(f.path for f in repo.inventory.files)
        hashes = repo.export(paths, operation / "snapshot")
        if name == "pytest_native":
            isolation = run_suite(
                suite="isolation",
                image_id=image,
                snapshot=operation / "snapshot",
                acceptance=acceptance,
                directory=operation / "isolation",
            )
            if isolation.status != "passed":
                raise AssertionError("Prepared image failed isolation checks.")
            results.append({"name": "runner_isolation", "capture": asdict(isolation)})
        suite = "project_baseline" if name == "pytest_native" else "project_unittest"
        capture = run_suite(
            suite=suite,
            image_id=image,
            snapshot=operation / "snapshot",
            acceptance=acceptance,
            directory=operation / "capture",
        )
        unchanged = verify_original(manager.git, state) and hashes == file_hashes(
            operation / "snapshot", paths
        )
        passing = [c for c in capture.cases if c.outcome == "passed"]
        expected_status = "passed" if name in {"pytest_native", "unittest_native"} else "error"
        correct = unchanged and capture.status == expected_status
        if name == "unittest_native":
            correct = correct and len(passing) == 2  # TestCase plus native load_tests doctest.
        if name == "unittest_setup_error":
            correct = correct and any("APassing.test_double" in c.name for c in passing)
        if not correct:
            raise AssertionError(f"Native adapter check failed: {name}: {capture.status}")
        results.append(
            {
                "name": name,
                "fixture_kind": "generated independent Git repository",
                "commit_sha": repo.snapshot.commit_sha,
                "project_root": repo.prefix,
                "adapter_version": support.adapter_version,
                "input_hashes": hashes,
                "original_and_export_unchanged": unchanged,
                "capture": asdict(capture),
                "capture_path": (operation / "capture/capture.json")
                .relative_to(PROJECT_ROOT)
                .as_posix(),
            }
        )
    path = directory / "checks.json"
    path.write_text(json.dumps({"status": "passed", "checks": results}, indent=2), encoding="utf-8")
    print(json.dumps({"status": "passed", "checks": len(results), "receipt": str(path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
