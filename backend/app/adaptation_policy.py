"""Constrain actual production bytes, source use, imports and the pinned integration scope."""

import ast
import difflib
import hashlib
import sys
from pathlib import PurePosixPath

from app.contracts import validate_repo_path
from app.errors import EvidenceError
from app.selection_artifacts import feature_refs
from app.source_use import SOURCE_USE_PROFILES, validate_notice
from app.tools.evidence import EvidenceSession
from app.tools.repositories import permitted_path

LIMITS = {
    "production_files": 5,
    "new_files": 1,
    "changed_lines": 400,
    "new_runtime_dependencies": 0,
    "repairs": 3,
}
MIT_BODY = """Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_ref(session, ref):
    for start in range(ref.start_line, ref.end_line + 1, 200):
        session.read_range(ref.path, start, min(start + 199, ref.end_line))
    return session.validate_ref(ref)


def production_path(path: str) -> bool:
    validate_repo_path(path)
    parts = PurePosixPath(path).parts
    return bool(
        permitted_path(path)
        and path.endswith(".py")
        and all(not p.startswith(".") for p in parts)
        and not {p.lower() for p in parts[:-1]} & {"tests", "test", "sandbox", "scripts"}
        and parts[-1] not in {"conftest.py", "setup.py"}
        and not parts[-1].startswith("test_")
        and not parts[-1].endswith("_test.py")
    )


def source_notices(source, card) -> str:
    """Require complete notices matching an explicit reviewed source-use profile."""
    if card.license_spdx not in SOURCE_USE_PROFILES or not card.license_refs:
        raise EvidenceError("Adaptation requires a reviewed standard MIT or BSD-2-Clause notice.")
    session = EvidenceSession(source)
    for ref in feature_refs(card):
        read_ref(session, ref)
    notices = []
    for ref in source.snapshot.license_refs:
        text = source.read(ref.path)
        if len(text.encode()) > 16_000:
            raise EvidenceError("Source notices exceed the reviewed reuse profile.")
        validate_notice(text, card.license_spdx, MIT_BODY)
        notices.append(
            f"Source: {source.snapshot.origin}@{source.snapshot.commit_sha}\n"
            f"Notice: {ref.path}\n{text.rstrip()}\n"
        )
    if not notices or {ref.path for ref in card.license_refs} - {
        ref.path for ref in source.snapshot.license_refs
    }:
        raise EvidenceError("Source notice references are incomplete or unrecognized.")
    return "\n".join(notices)


def validate_plan(plan, source_session, target_session):
    target = target_session.repo
    paths = plan.production_paths
    if len(set(paths)) != len(paths) or len({p.casefold() for p in paths}) != len(paths):
        raise EvidenceError("Production scope contains duplicate or colliding paths.")
    known = {f.path for f in target.inventory.files}
    if sum(p not in known for p in paths) > LIMITS["new_files"]:
        raise EvidenceError("Plan exceeds the one-new-production-file limit.")
    for path in paths:
        if not production_path(path):
            raise EvidenceError("Plan includes a protected or unsupported production path.")
        if path not in known and not any(
            PurePosixPath(p).parent == PurePosixPath(path).parent for p in known
        ):
            raise EvidenceError("A new module must be in an existing production directory.")
        if path not in known and path.casefold() in {p.casefold() for p in known}:
            raise EvidenceError("New module collides with an existing path.")
    for ref in plan.integration_refs:
        target_session.validate_ref(ref)
        if ref.path not in paths:
            raise EvidenceError("Integration must be part of the allowed production scope.")
    for rule in plan.included:
        for ref in rule.source_refs:
            source_session.validate_ref(ref)
            if not production_path(ref.path):
                raise EvidenceError("Included behavior requires production code evidence.")
    # Exported bytes do not carry executable bits. Reject such files rather than losing mode.
    tree = target.git.run("ls-tree", "-r", target.snapshot.commit_sha, cwd=target.storage)
    modes = {
        line.split(b"\t", 1)[1].decode(): line.split(b" ", 1)[0]
        for line in tree.splitlines()
        if b"\t" in line
    }
    for path in paths:
        full = f"{target.prefix}/{path}" if target.prefix else path
        if path in known and modes.get(full) != b"100644":
            raise EvidenceError("Executable, linked or special production modes are unsupported.")


def import_roots(content: str) -> set[str]:
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError, RecursionError):
        raise EvidenceError(
            "Production text exceeds the supported Python syntax boundary."
        ) from None
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and (
            isinstance(node.func, ast.Name)
            and node.func.id in {"exec", "eval", "__import__"}
            or isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
        ):
            raise EvidenceError("Dynamic code/imports require a separately reviewed adapter.")
    return roots


def validate_patch(proposal, plan, original: dict[str, bytes], current: dict[str, bytes], notice):
    """Replacements bind to current hashes; limits always measure the cumulative base diff."""
    changed = [f.path for f in proposal.files]
    if len(set(changed)) != len(changed) or not set(changed) <= set(plan.production_paths):
        raise EvidenceError("Patch contains duplicate or out-of-scope paths.")
    result = dict(current)
    for item in proposal.files:
        expected = sha256(current[item.path]) if item.path in current else None
        if item.before_sha256 != expected:
            raise EvidenceError("Patch preimage does not match the current production bytes.")
        if "\x00" in item.content or not item.content.strip() or not item.content.endswith("\n"):
            raise EvidenceError("Binary, empty, or unterminated production files are unsupported.")
        result[item.path] = item.content.encode()
    actual = sorted(p for p in result if result[p] != original.get(p))
    integration = {ref.path for ref in plan.integration_refs}
    if not actual or not integration <= set(actual):
        raise EvidenceError("The actual patch must change every declared integration file.")
    # Carry complete source notices into the actual tested patch, not only its download bundle.
    path = sorted(integration)[0]
    header = "\n".join(f"# {line}" for line in notice.splitlines()) + "\n\n"
    if not result[path].startswith(header.encode()):
        result[path] = header.encode() + result[path]
    allowed_imports = set(sys.stdlib_module_names)
    for path, raw in original.items():
        if production_path(path):
            allowed_imports.update(import_roots(raw.decode()))
            module = path.removeprefix("src/").split("/")[0].removesuffix(".py")
            allowed_imports.add(module)
    for path in actual:
        allowed_imports.add(path.removeprefix("src/").split("/")[0].removesuffix(".py"))
    added = sum(p not in original for p in actual)
    diff, lines, mapping = [], 0, []
    for path in actual:
        raw = result[path]
        try:
            imports = import_roots(raw.decode())
        except (ValueError, SyntaxError, UnicodeError):
            raise EvidenceError("Production patch is not valid Python text.") from None
        if imports - allowed_imports:
            raise EvidenceError("Actual patch introduces an unapproved runtime dependency.")
        if path in original:

            def definitions(content):
                return {
                    node.name
                    for node in ast.walk(ast.parse(content))
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                }

            if definitions(original[path].decode()) - definitions(raw.decode()):
                raise EvidenceError("Actual patch unexpectedly removes existing definitions.")
        before = original.get(path, b"").decode().splitlines(keepends=True)
        after = raw.decode().splitlines(keepends=True)
        changes = list(
            difflib.unified_diff(
                before,
                after,
                fromfile=f"a/{path}" if path in original else "/dev/null",
                tofile=f"b/{path}",
            )
        )
        lines += sum(line.startswith(("+", "-")) for line in changes[2:])
        # difflib omits Git's marker for unterminated original lines.
        changes = [
            line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
            for line in changes
        ]
        diff.append(f"diff --git a/{path} b/{path}\n")
        if path not in original:
            diff.append("new file mode 100644\n")
        diff.extend(changes)
        mapping.append(
            {
                "path": path,
                "before_sha256": sha256(original[path]) if path in original else None,
                "after_sha256": sha256(raw),
            }
        )
    if len(actual) > LIMITS["production_files"] or added > LIMITS["new_files"] or lines > 400:
        raise EvidenceError("Actual cumulative patch exceeds the production scope limits.")
    return result, "".join(diff).encode(), mapping, lines, added
