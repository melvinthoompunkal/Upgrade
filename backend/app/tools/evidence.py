"""Bounded text windows and Python AST evidence. AST references are not runtime call edges."""

import ast
import hashlib
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from app.contracts import Ref
from app.errors import EvidenceError
from app.evidence_contracts import Coverage, PythonEvidence, SourceTrace, Symbol
from app.tools.repositories import PinnedRepository

MAX_WINDOW_LINES = 200
MAX_WINDOW_BYTES = 24_000


def python_index(path: str, text: str) -> PythonEvidence:
    symbols, imports = [], set()
    try:
        tree = ast.parse(text, filename=path)
    except (SyntaxError, ValueError, RecursionError):
        return PythonEvidence(
            path=path, symbols=[], imports=[], parse_error="Python AST parse failed."
        )

    def visit(node: ast.AST, parents: tuple[str, ...] = ()) -> None:
        nested = parents
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            name = ".".join((*parents, node.name))
            start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            kind = (
                "class"
                if isinstance(node, ast.ClassDef)
                else ("async_function" if isinstance(node, ast.AsyncFunctionDef) else "function")
            )
            symbols.append(Symbol(name=name, kind=kind, start_line=start, end_line=node.end_lineno))
            nested = (*parents, node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    symbols.append(
                        Symbol(
                            name=".".join((*parents, target.id)),
                            kind="assignment",
                            start_line=node.lineno,
                            end_line=node.end_lineno,
                        )
                    )
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for name in names:
                if name:
                    imports.add(name)
                    symbols.append(
                        Symbol(
                            name=name,
                            kind="import",
                            start_line=node.lineno,
                            end_line=node.end_lineno,
                        )
                    )
        for child in ast.iter_child_nodes(node):
            visit(child, nested)

    visit(tree)
    return PythonEvidence(path=path, symbols=symbols, imports=sorted(imports), parse_error=None)


@dataclass
class EvidenceSession:
    repo: PinnedRepository
    max_read_bytes: int = 500_000
    read_bytes: int = 0
    windows: list[dict] = field(default_factory=list)
    indexes: dict[str, PythonEvidence] = field(default_factory=dict)

    def index(self, path: str) -> PythonEvidence:
        if not path.endswith(".py"):
            raise EvidenceError("AST indexing requires an inventoried Python file.")
        if path not in self.indexes:
            self.indexes[path] = python_index(path, self.repo.read(path))
        return self.indexes[path]

    def read_range(self, path: str, start_line: int, end_line: int) -> dict:
        lines = self.repo.read(path).splitlines()
        if (
            not 1 <= start_line <= end_line <= len(lines)
            or end_line - start_line >= MAX_WINDOW_LINES
        ):
            raise EvidenceError("Requested line range is outside file bounds or exceeds 200 lines.")
        text = "\n".join(lines[start_line - 1 : end_line]) + "\n"
        size = len(text.encode("utf-8"))
        if size > MAX_WINDOW_BYTES or self.read_bytes + size > self.max_read_bytes:
            raise EvidenceError("Evidence window or cumulative read-byte budget exceeded.")
        self.read_bytes += size
        excerpt = {
            "path": path,
            "start_line": start_line,
            "end_line": end_line,
            "text": text,
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
        }
        self.windows.append(excerpt)
        return excerpt

    def validate_ref(self, ref: Ref) -> dict:
        if (ref.repo_id, ref.commit_sha) != (
            self.repo.snapshot.repo_id,
            self.repo.snapshot.commit_sha,
        ):
            raise EvidenceError("Citation belongs to a different repository or commit.")
        lines = self.repo.read(ref.path).splitlines()
        if ref.end_line > len(lines):
            raise EvidenceError("Citation extends past the pinned file.")
        covered = set()
        for window in self.windows:
            if window["path"] == ref.path:
                covered.update(range(window["start_line"], window["end_line"] + 1))
        if not set(range(ref.start_line, ref.end_line + 1)) <= covered:
            raise EvidenceError("Citation includes lines the tracer did not read.")
        if ref.symbol is not None:
            symbols = self.index(ref.path).symbols
            if not any(
                s.name == ref.symbol
                and s.start_line <= ref.start_line <= ref.end_line <= s.end_line
                for s in symbols
            ):
                raise EvidenceError("Citation does not resolve to the named Python AST symbol.")
        text = "\n".join(lines[ref.start_line - 1 : ref.end_line]) + "\n"
        return {
            "ref": ref.model_dump(mode="json"),
            "text": text,
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
        }

    def validate_trace(self, trace: SourceTrace) -> SourceTrace:
        if trace.source_sha != self.repo.snapshot.commit_sha:
            raise EvidenceError("Trace SHA does not match the pinned source.")
        if len({f.feature_id for f in trace.features}) != len(trace.features):
            raise EvidenceError("Feature IDs must be unique.")
        for card in trace.features:
            refs = [
                *card.entry_points,
                *card.implementation,
                *card.source_tests,
                *card.license_refs,
                *(r for d in card.dependencies for r in d.refs),
            ]
            if len({r.path for r in refs}) > 15:
                raise EvidenceError("Feature exceeds its 15-file evidence budget.")
            for ref in refs:
                self.validate_ref(ref)
            for ref in card.source_tests:
                if not (
                    "tests" in PurePosixPath(ref.path).parts
                    or PurePosixPath(ref.path).name.startswith("test_")
                ):
                    raise EvidenceError("Source test evidence must refer to a test file.")
            if card.evidence_status == "supported" and (
                not card.entry_points or not card.implementation
            ):
                raise EvidenceError(
                    "Supported features require entry-point and implementation evidence."
                )
            if card.license_spdx is not None and not card.license_refs:
                raise EvidenceError("License identification requires license references.")
            approved_license_paths = {r.path for r in self.repo.snapshot.license_refs}
            if any(r.path not in approved_license_paths for r in card.license_refs):
                raise EvidenceError(
                    "License references must cite inventoried license/notice files."
                )
            for dependency in card.dependencies:
                self._validate_dependency(dependency.name, dependency.kind, dependency.refs)
            if card.evidence_status == "supported":
                imports = {
                    name.split(".")[0]
                    for r in card.implementation
                    if r.path.endswith(".py")
                    for name in self.index(r.path).imports
                }
                runtime = imports - sys.stdlib_module_names
                declared = {d.name.split(".")[0] for d in card.dependencies if d.kind == "runtime"}
                if not runtime <= declared:
                    raise EvidenceError("Supported feature omits direct runtime source imports.")
        examined = sorted({w["path"] for w in self.windows})
        unexamined = sorted({f.path for f in self.repo.inventory.files} - set(examined))
        coverage = Coverage(
            examined_paths=examined,
            excluded_paths=self.repo.inventory.excluded_paths,
            unexamined_areas=unexamined,
            limits_reached=sorted(
                set(self.repo.inventory.limits_reached) | set(trace.coverage.limits_reached)
            ),
        )
        return SourceTrace(source_sha=trace.source_sha, features=trace.features, coverage=coverage)

    def _validate_dependency(self, name: str, kind: str, refs: tuple[Ref, ...]) -> None:
        module = name.split(".")[0]
        is_stdlib = module in sys.stdlib_module_names
        if (kind == "stdlib") != is_stdlib:
            raise EvidenceError("Dependency standard-library classification does not match Python.")
        found = False
        for ref in refs:
            if ref.path.endswith(".py"):
                # Evidence must include the actual import lines, not just another part of that file.
                for symbol in self.index(ref.path).symbols:
                    if (
                        symbol.kind == "import"
                        and symbol.name.split(".")[0] == module
                        and (ref.start_line <= symbol.start_line <= symbol.end_line <= ref.end_line)
                    ):
                        found = True
            elif ref.path.endswith(".toml") and kind != "stdlib":
                try:
                    manifest = tomllib.loads(self.repo.read(ref.path))
                    declared = manifest.get("project", {}).get("dependencies", [])
                    found |= any(
                        re.split(r"[<>=!~\[; ]", d)[0].lower() == name.lower() for d in declared
                    )
                except (tomllib.TOMLDecodeError, TypeError):
                    raise EvidenceError("Dependency manifest cannot be parsed.") from None
        if not found:
            raise EvidenceError("Dependency has no matching cited import or manifest declaration.")
