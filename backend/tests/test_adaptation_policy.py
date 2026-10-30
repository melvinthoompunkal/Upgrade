"""Adversarial patch payloads and exact cumulative production-scope boundaries."""

from types import SimpleNamespace

import pytest

from app.adaptation_contracts import FunctionCase, PatchProposal, ProductionFile
from app.adaptation_policy import sha256, validate_patch
from app.errors import EvidenceError

ORIGINAL = {
    "src/api.py": b"def handle(value):\n    return value\n",
    "tests/test_api.py": b"def test_original():\n    assert True\n",
}


def scope(*paths):
    return SimpleNamespace(
        production_paths=("src/api.py", *paths),
        integration_refs=[SimpleNamespace(path="src/api.py")],
    )


def proposal(path="src/api.py", content="def handle(value):\n    return [value]\n", before=None):
    return PatchProposal(
        files=[
            ProductionFile(
                path=path, content=content, before_sha256=before or sha256(ORIGINAL["src/api.py"])
            )
        ],
        rationale="A bounded production adaptation.",
    )


@pytest.mark.parametrize("path", ["tests/test_api.py", "pyproject.toml", "src/conftest.py"])
def test_protected_paths_are_outside_approved_scope(path):
    with pytest.raises(EvidenceError, match="out-of-scope"):
        validate_patch(proposal(path), scope(), ORIGINAL, ORIGINAL, "notice")


@pytest.mark.parametrize(
    "path", ["../outside.py", "C:/outside.py", "src/../outside.py", "src\\api.py"]
)
def test_traversal_never_becomes_a_filesystem_operation(path):
    with pytest.raises(ValueError):
        proposal(path)


@pytest.mark.parametrize(
    "content",
    [
        "\x00\n",
        " \n",
        "def handle(value):\n    return value",
        "def unrelated():\n    return 1\n",
        "invalid (\n",
        "import pytest\ndef handle(value):\n    return value\n",
        "def handle(value):\n    return " + "1+" * 10_000 + "1\n",
    ],
)
def test_binary_deletion_syntax_and_test_only_runtime_dependency_are_rejected(content):
    with pytest.raises(EvidenceError):
        validate_patch(proposal(content=content), scope(), ORIGINAL, ORIGINAL, "notice")


def test_repair_preimage_and_changed_lines_measure_the_original_snapshot():
    first, _, _, lines, _ = validate_patch(proposal(), scope(), ORIGINAL, ORIGINAL, "notice")
    with pytest.raises(EvidenceError, match="preimage"):
        validate_patch(proposal(), scope(), ORIGINAL, first, "notice")
    repaired, _, _, repaired_lines, _ = validate_patch(
        proposal(
            content=first["src/api.py"].decode().replace("[value]", "[value, value]"),
            before=sha256(first["src/api.py"]),
        ),
        scope(),
        ORIGINAL,
        first,
        "notice",
    )
    assert (
        repaired_lines == lines and repaired["tests/test_api.py"] == ORIGINAL["tests/test_api.py"]
    )


def test_second_new_file_is_rejected_even_when_added_in_a_later_repair():
    plan = scope("src/one.py", "src/two.py")
    first = PatchProposal(
        files=[
            *proposal().files,
            ProductionFile(
                path="src/one.py", before_sha256=None, content="def extra():\n    return 1\n"
            ),
        ],
        rationale="First new production module.",
    )
    current, *_ = validate_patch(first, plan, ORIGINAL, ORIGINAL, "notice")
    second = PatchProposal(
        files=[
            ProductionFile(
                path="src/two.py", before_sha256=None, content="def extra_two():\n    return 2\n"
            )
        ],
        rationale="Second new module is forbidden.",
    )
    with pytest.raises(EvidenceError, match="cumulative"):
        validate_patch(second, plan, ORIGINAL, current, "notice")


@pytest.mark.parametrize("json_args", ["{}", "[NaN]", "[Infinity]", "[" * 1500 + "]" * 1500])
def test_acceptance_arguments_are_bounded_standard_json(json_args):
    with pytest.raises(ValueError):
        FunctionCase(
            id="case",
            rule_id="rule",
            module="api",
            function="handle",
            args_json=json_args,
            kwargs_json="{}",
            expected_json="null",
            expected_exception=None,
            exception_message=None,
        )
