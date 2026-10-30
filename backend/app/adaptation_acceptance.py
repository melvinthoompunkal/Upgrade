"""Reviewed function adapter. Generate tests as data; never run project code on the host."""

import ast

from app.adaptation_contracts import AcceptanceSpec, AdaptationPlan
from app.errors import EvidenceError


def validate_acceptance(spec: AcceptanceSpec, plan: AdaptationPlan, repo):
    rules = {rule.id for rule in plan.included}
    if len(rules) != len(plan.included) or {c.rule_id for c in spec.cases} != rules:
        raise EvidenceError("Acceptance must cover every unique included behavior rule.")
    if len({c.id for c in spec.cases}) != len(spec.cases):
        raise EvidenceError("Acceptance case identities must be unique.")
    integration = {ref.path for ref in plan.integration_refs}
    for case in spec.cases:
        module = case.module.replace(".", "/")
        paths = (
            f"src/{module}.py",
            f"{module}.py",
            f"src/{module}/__init__.py",
            f"{module}/__init__.py",
        )
        selected = [p for p in paths if p in integration]
        if len(selected) != 1:
            raise EvidenceError("Acceptance must call an existing explicit integration module.")
        tree = ast.parse(repo.read(selected[0]))
        if not any(isinstance(n, ast.FunctionDef) and n.name == case.function for n in tree.body):
            raise EvidenceError("The function adapter requires an existing synchronous function.")
        if case.function.startswith("__") or any(
            p.startswith("__") for p in case.module.split(".")
        ):
            raise EvidenceError("Dunder entry points are outside the function adapter.")


def render_acceptance(spec: AcceptanceSpec) -> bytes:
    lines = [
        "# Frozen controller-generated acceptance: python-function-acceptance-v1",
        "import importlib",
        "import json",
        "",
    ]
    for case in spec.cases:
        lines.extend(
            [
                f"def test_{case.id}():",
                f"    module = importlib.import_module({case.module!r})",
                f"    function = getattr(module, {case.function!r})",
                f"    args = json.loads({case.args_json!r})",
                f"    kwargs = json.loads({case.kwargs_json!r})",
            ]
        )
        marker = f"F7_EXPECTATION:{case.id}"
        if case.expected_exception:
            lines.extend(
                [
                    "    try:",
                    "        function(*args, **kwargs)",
                    f"    except {case.expected_exception} as error:",
                    f"        if str(error) != {case.exception_message!r}:",
                    "            raise",
                    "    else:",
                    f"        raise AssertionError({marker!r})",
                ]
            )
        else:
            lines.extend(
                [
                    "    actual = function(*args, **kwargs)",
                    f"    expected = json.loads({case.expected_json!r})",
                    "    if actual != expected:",
                    f"        raise AssertionError({marker!r})",
                ]
            )
        lines.append("")
    return ("\n".join(lines) + "\n").encode()


def acceptance_state(capture, spec: AcceptanceSpec, *, require_assertion_red=True) -> str:
    expected = {f"test_{c.id}": f"F7_EXPECTATION:{c.id}" for c in spec.cases}
    if {c.name for c in capture.cases} != set(expected) or len(capture.cases) != len(expected):
        raise EvidenceError("Acceptance case identities differ from the frozen specification.")
    if capture.status == "passed" and all(c.outcome == "passed" for c in capture.cases):
        return "green"
    if capture.status == "failed" and any(c.outcome == "failed" for c in capture.cases):
        for case in capture.cases:
            if (
                require_assertion_red
                and case.outcome == "failed"
                and case.message.strip() != (f"AssertionError: {expected[case.name]}")
            ):
                raise EvidenceError(
                    "Acceptance failed with an unrelated exception, not assertion red."
                )
            if case.outcome not in {"passed", "failed"}:
                raise EvidenceError("Skipped/error acceptance cases cannot establish a gap.")
        return "red" if require_assertion_red else "failed"
    raise EvidenceError("Acceptance did not produce valid assertion red or complete green.")
