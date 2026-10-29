"""Reviewed Python data/AST inspection and offline pytest admission. Never import projects."""

import sys
import tomllib
from configparser import ConfigParser
from configparser import Error as ConfigError
from pathlib import PurePosixPath
from time import monotonic

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet

from app.tools.evidence import python_index
from app.tools.repositories import PinnedRepository

ADAPTER_VERSION = "python-pytest-v1"
MAX_PYTHON_FILES = 200
MAX_PYTHON_BYTES = 2 * 1024 * 1024
MAX_MANIFEST_BYTES = 24_000
PREPARED_DEPENDENCIES = {"httpx==0.28.1"}
PREPARED_TEST_PACKAGES = {"pytest": "9.0.2", "pytest-timeout": "2.4.0", "httpx": "0.28.1"}


def test_dependency_reasons(manifest: dict) -> list[str]:
    reasons = []
    for groups in (
        manifest.get("dependency-groups", {}),
        manifest["project"].get("optional-dependencies", {}),
    ):
        if not isinstance(groups, dict):
            return ["Test dependency metadata cannot be statically validated."]
        for name in ("test", "tests", "testing"):
            dependencies = groups.get(name, [])
            if not isinstance(dependencies, list):
                return ["Test dependency metadata cannot be statically validated."]
            for dependency in dependencies:
                try:
                    requirement = Requirement(dependency) if isinstance(dependency, str) else None
                except InvalidRequirement:
                    requirement = None
                if requirement is None or requirement.url or requirement.extras:
                    reasons.append(
                        "Test requirements need a separately prepared dependency profile."
                    )
                    continue
                version = PREPARED_TEST_PACKAGES.get(requirement.name.lower().replace("_", "-"))
                if version is None or not requirement.specifier.contains(version):
                    reasons.append(f"Unprepared test requirement: {requirement.name}.")
    return list(dict.fromkeys(reasons))


def python_facts(repo: PinnedRepository) -> tuple[dict, list[str]]:
    files = [entry for entry in repo.inventory.files if entry.path.endswith(".py")]
    reasons = []
    if len(files) > MAX_PYTHON_FILES or sum(f.bytes for f in files) > MAX_PYTHON_BYTES:
        return {}, ["Python analysis exceeds 200 files or 2 MiB; select a smaller project root."]
    indexes = {}
    deadline = monotonic() + 10
    for entry in files:
        if monotonic() > deadline:
            return indexes, ["Python indexing exceeded the 10-second wall-time limit."]
        try:
            index = python_index(entry.path, repo.read(entry.path))
        except RecursionError:
            return indexes, ["Python AST nesting exceeds the supported inspection bound."]
        if index.parse_error:
            reasons.append(f"Python syntax cannot be indexed: {entry.path}.")
        indexes[entry.path] = index
    return indexes, reasons


def manifest_data(repo: PinnedRepository) -> dict:
    text = repo.read("pyproject.toml")
    if len(text.encode()) > MAX_MANIFEST_BYTES:
        raise ValueError("Project manifest exceeds the 24 KiB inspection limit.")
    try:
        result = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise ValueError("Project manifest is invalid TOML.") from None
    if not isinstance(result.get("project"), dict):
        raise ValueError("A static PEP 621 project table is required.")
    return result


def execution_reasons(repo: PinnedRepository, manifest: dict, indexes: dict) -> list[str]:
    """Accept a deliberately narrow native suite, including conftest, without hiding tests."""
    project = manifest["project"]
    reasons = test_dependency_reasons(manifest)
    try:
        if not project.get("requires-python") or not SpecifierSet(
            project["requires-python"]
        ).contains("3.12.0"):
            reasons.append("The declared Python range does not include the prepared Python 3.12.")
    except InvalidSpecifier:
        reasons.append("The declared Python range is invalid.")
    if project.get("dynamic", []) and set(project["dynamic"]) - {"version", "description"}:
        reasons.append("Dynamic runtime metadata needs a separate preparation profile.")
    if not set(project.get("dependencies", [])) <= PREPARED_DEPENDENCIES:
        reasons.append("Runtime dependencies must be empty or exactly httpx==0.28.1.")
    tool = manifest.get("tool", {})
    pytest_config = tool.get("pytest", {}) if isinstance(tool, dict) else None
    options = pytest_config.get("ini_options", {}) if isinstance(pytest_config, dict) else None
    if not isinstance(options, dict) or set(options) - {"testpaths", "pythonpath"}:
        reasons.append("Pytest configuration requires a separately reviewed adapter.")
    elif options.get("testpaths", ["tests"]) != ["tests"] or options.get(
        "pythonpath", ["src"]
    ) not in (["src"], ["."], ["src", "."]):
        reasons.append("Offline tests support tests/ with src/ or root Python modules.")
    paths = {entry.path for entry in repo.inventory.files}
    for path in sorted(paths & {"pytest.ini", "tox.ini", "setup.cfg"}):
        config = ConfigParser(interpolation=None)
        try:
            config.read_string(repo.read(path))
            if path == "pytest.ini" or {"pytest", "tool:pytest"} & set(config.sections()):
                reasons.append("Additional pytest configuration files require review.")
        except ConfigError:
            reasons.append("Additional configuration cannot be statically validated.")
    if not any(
        p.startswith("tests/") and PurePosixPath(p).name.startswith("test_") for p in indexes
    ):
        reasons.append("No native tests/test_*.py suite was inventoried.")
    native_unittest = any(
        s.name == "load_tests" for index in indexes.values() for s in index.symbols
    )
    if native_unittest and any(
        path.endswith("conftest.py")
        or "pytest" in index.imports
        or any(
            s.name.startswith("test_")
            and "." not in s.name
            and s.kind in {"function", "async_function"}
            for s in index.symbols
        )
        for path, index in indexes.items()
    ):
        reasons.append(
            "Mixed unittest load_tests and pytest collection requires a reviewed adapter."
        )
    excluded_inputs = [
        p
        for p in repo.inventory.excluded_paths
        if (
            p.startswith(("src/", "tests/"))
            or "/" not in p
            and p.endswith((".py", ".toml", ".ini", ".cfg"))
        )
    ]
    if repo.inventory.limits_reached or excluded_inputs:
        # Missing data can change collection or behavior. Never declare an incomplete export green.
        reasons.append(
            "The project export has exclusions or inventory limits; tests are unsupported."
        )
    modules = {
        PurePosixPath(path.removeprefix("src/")).parts[0].removesuffix(".py")
        for path in indexes
        if not path.startswith("tests/")
    }
    modules |= {PurePosixPath(path).stem for path in indexes if path.startswith("tests/")}
    modules |= {PurePosixPath(path).stem for path in indexes}
    modules |= {part for path in indexes for part in PurePosixPath(path).parts[:-1]}
    allowed = sys.stdlib_module_names | modules | {"pytest"}
    if "httpx==0.28.1" in project.get("dependencies", []):
        allowed |= {"httpx"}
    for path, index in indexes.items():
        if any(name.split(".")[0] not in allowed for name in index.imports):
            reasons.append(f"Unprepared imports in {path}; no packages will be installed.")
        if any(s.name.startswith("pytest_") or s.name == "pytest_plugins" for s in index.symbols):
            reasons.append(f"Custom pytest hooks/plugins in {path} require review.")
    return list(dict.fromkeys(reasons))
