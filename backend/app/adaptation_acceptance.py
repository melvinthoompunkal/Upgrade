"""Reviewed function adapter. Generate tests as data; never run project code on the host."""

import ast
import json
from pathlib import Path

from app.adaptation_contracts import AcceptanceSpec, AdaptationPlan, InvocationCase
from app.errors import EvidenceError


def validate_acceptance(spec: AcceptanceSpec, plan: AdaptationPlan, repo):
    if (spec.schema_version == "python-integration-acceptance-v2") != (
        plan.schema_version == "python-integration-plan-v2"
    ):
        raise EvidenceError("Acceptance and behavior contract adapter versions differ.")
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
        body = tree.body
        invocation = case.invocation if isinstance(case, InvocationCase) else None
        if invocation and invocation.class_name:
            classes = [
                n for n in body if isinstance(n, ast.ClassDef) and n.name == invocation.class_name
            ]
            if len(classes) != 1:
                raise EvidenceError("The method adapter requires an existing explicit class.")
            if classes[0].decorator_list:
                raise EvidenceError("The method adapter requires an undecorated integration class.")
            if invocation.kind == "new_method" and any(
                not isinstance(base, ast.Name) or base.id != "object" for base in classes[0].bases
            ):
                raise EvidenceError("New methods require a class without inherited APIs.")
            body = classes[0].body
        functions = [
            n
            for n in body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == case.function
        ]
        new_api = invocation and invocation.kind.startswith("new_")
        if invocation and invocation.class_name and any(n.decorator_list for n in functions):
            raise EvidenceError("The method adapter requires undecorated instance methods.")
        if new_api:
            bindings = {getattr(n, "name", None) for n in body}
            for node in body:
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    bindings.update(
                        child.id
                        for child in ast.walk(node)
                        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
                    )
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    bindings.update(
                        alias.asname or alias.name.split(".")[0] for alias in node.names
                    )
            if case.function in bindings:
                raise EvidenceError("A new integration API must be absent in the pinned target.")
        elif len(functions) != 1 or isinstance(functions[0], ast.AsyncFunctionDef) != bool(
            invocation and invocation.await_result
        ):
            raise EvidenceError("Acceptance call must match an existing sync/async definition.")
        if (
            invocation
            and invocation.http
            and invocation.http.client_parameter in json.loads(case.kwargs_json)
        ):
            raise EvidenceError("HTTP client injection cannot replace a declared JSON argument.")
        if case.function.startswith("__") or any(
            p.startswith("__") for p in case.module.split(".")
        ):
            raise EvidenceError("Dunder entry points are outside the function adapter.")


def render_acceptance(spec: AcceptanceSpec) -> bytes:
    lines = [
        f"# Frozen controller-generated acceptance: {spec.schema_version}",
        "import importlib",
        "import json",
        "",
    ]
    if spec.schema_version == "python-integration-acceptance-v2":
        lines.append(Path(__file__).with_name("invocation_runtime.py").read_text(encoding="utf-8"))
    for case in spec.cases:
        lines.extend(
            [
                f"def test_{case.id}():",
                f"    module = importlib.import_module({case.module!r})",
                f"    args = json.loads({case.args_json!r})",
                f"    kwargs = json.loads({case.kwargs_json!r})",
            ]
        )
        marker = f"F7_EXPECTATION:{case.id}"
        if isinstance(case, InvocationCase):
            invocation = case.invocation.model_dump(mode="json")
            call = (
                f"_integration_call({case.module!r}, {case.function!r}, args, kwargs, "
                f"{invocation!r}, {marker!r}, {case.expected_exception!r}, "
                f"{case.exception_message!r})"
            )
        else:
            lines.append(f"    function = getattr(module, {case.function!r})")
            call = "function(*args, **kwargs)"
        if case.expected_exception:
            lines.extend(
                [
                    "    try:",
                    f"        {call}",
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
                    f"    actual = {call}",
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
