"""Bounded Git tree traversal and strict UTF-8 admission, without checking out code."""

import hashlib
import re
from collections import deque
from pathlib import PurePosixPath

from app.contracts import Ref, RepoSnapshot
from app.errors import RepositoryError
from app.text_contracts import (
    LEGACY_TEXT_POLICY,
    Exclusion,
    TextInventory,
    TextInventoryFile,
    TreeEntry,
)
from app.tools.runner_artifacts import MAX_ARTIFACT_BYTES

LANGUAGES = {
    ".py": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".c": "C",
    ".h": "C",
    ".cpp": "C++",
    ".hpp": "C++",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".scala": "Scala",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".erl": "Erlang",
    ".hs": "Haskell",
    ".lua": "Lua",
    ".sh": "Shell",
    ".bash": "Shell",
    ".ps1": "PowerShell",
    ".sql": "SQL",
    ".html": "HTML",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".css": "CSS",
    ".scss": "SCSS",
    ".graphql": "GraphQL",
    ".proto": "Protobuf",
    ".tf": "HCL",
    ".xml": "XML",
    ".r": "R",
    ".dart": "Dart",
    ".clj": "Clojure",
}
MANIFESTS = {
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "Gemfile",
    "composer.json",
    "mix.exs",
    "CMakeLists.txt",
    "Makefile",
    "Dockerfile",
}
DOC_SUFFIXES = {".md", ".mdx", ".rst", ".txt", ".adoc"}
GENERATED_SUFFIXES = {".map", ".lock"}
SENSITIVE_NAMES = {
    ".npmrc",
    ".yarnrc",
    ".pypirc",
    ".netrc",
    "_netrc",
    ".git-credentials",
    ".gitconfig",
}
GENERATED_NAMES = {"package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock"}


def classify(path: str, text: str, *, legacy=False):
    file = PurePosixPath(path)
    name, parts, suffix = file.name.lower(), {p.lower() for p in file.parts}, file.suffix.lower()
    language = LANGUAGES.get(suffix, "text")
    if file.name in MANIFESTS or suffix == ".csproj":
        return language, "manifest", "known manifest filename; contents are not evaluated"
    if (
        parts & {"test", "tests", "__tests__", "spec", "specs", "fixtures", "__fixtures__"}
        or name.startswith("test_")
        or name.endswith(("_test.go", "_test.py"))
        or ".test." in name
        or ".spec." in name
        or (
            not legacy
            and re.search(r"(^|[._-])(test|tests|spec|specs|fixture|fixtures)([._-]|$)", name)
        )
    ):
        return language, "test", "test path/filename hint"
    if suffix in DOC_SUFFIXES or name.startswith(("readme", "license", "notice", "copying")):
        return language, "documentation", "documentation filename hint"
    if language != "text":
        return language, "source", "source suffix hint"
    if text.startswith("#!"):
        first = text.splitlines()[0]
        language = next(
            (
                label
                for token, label in (
                    ("python", "Python"),
                    ("node", "JavaScript"),
                    ("ruby", "Ruby"),
                    ("sh", "Shell"),
                )
                if token in first
            ),
            "unknown",
        )
        return language, "source", "extensionless shebang hint"
    if suffix in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}:
        return "declarative", "source", "declarative text; operative behavior needs corroboration"
    return "unknown", "unknown", "bounded UTF-8 text; implementation role is ambiguous"


def priority(path):
    file = PurePosixPath(path)
    return (
        file.name not in MANIFESTS,
        file.name.lower() != "readme.md",
        bool(set(file.parts) & {"docs", "doc", "examples"}),
        path,
    )


def text_inventory(manager, storage, repo_id, origin, sha, prefix, *, max_entries, max_text_bytes):
    from app.tools.repositories import PinnedRepository, permitted_path

    entries, exclusions, deferred, limits, candidates = [], [], [], set(), []
    frontier = deque([prefix])
    tree_calls = 0
    while frontier and len(entries) < max_entries and tree_calls < 256:
        directory = frontier.popleft()
        raw = manager.git.run("ls-tree", "-l", "-z", f"{sha}:{directory}", cwd=storage)
        tree_calls += 1
        records = []
        for record in filter(None, raw.split(b"\0")):
            metadata, path_bytes = record.split(b"\t", 1)
            mode, kind, oid, size = metadata.split()
            try:
                name = path_bytes.decode("utf-8", errors="strict")
            except UnicodeError:
                exclusions.append(Exclusion(path=path_bytes.hex(), reason="non_utf8_path"))
                continue
            path = f"{directory}/{name}" if directory else name
            records.append((path, mode, kind, oid.decode(), None if size == b"-" else int(size)))
        records.sort(key=lambda row: priority(row[0]))
        for path, mode, kind, oid, size in records:
            if len(entries) >= max_entries:
                deferred.append(path)
                continue
            entries.append(
                TreeEntry(path=path, object_type=kind.decode(), object_id=oid, bytes=size)
            )
            reason = None
            if (
                not permitted_path(path)
                or not permitted_path(path.lower())
                or PurePosixPath(path).name.lower() in SENSITIVE_NAMES
            ):
                reason = "blocked_sensitive_generated_or_vendor_path"
            elif mode == b"040000" and kind == b"tree":
                frontier.append(path)
                continue
            elif mode not in {b"100644", b"100755"} or kind != b"blob":
                reason = "symlink_submodule_or_nonordinary_blob"
            elif PurePosixPath(path).suffix.lower() == ".ipynb":
                reason = "unsupported_compound_source_mapping"
            elif (
                PurePosixPath(path).suffix.lower() in GENERATED_SUFFIXES
                or path.endswith(".min.js")
                or PurePosixPath(path).name in GENERATED_NAMES
            ):
                reason = "generated_or_lockfile"
            elif size > MAX_ARTIFACT_BYTES:
                reason = "file_bytes"
                limits.add(reason)
            if reason:
                exclusions.append(Exclusion(path=path, reason=reason))
            else:
                candidates.append((path, oid, size))
    deferred.extend(frontier)
    enumeration_complete = not deferred
    remaining_tree_entries = len(deferred)
    if deferred:
        limits.add("tree_enumeration_limit_select_smaller_root")
    # Round-robin across modules, with manifests and overview documents first in each module.
    modules = {}
    for row in sorted(candidates, key=lambda row: priority(row[0])):
        relative = row[0][len(prefix) + 1 :] if prefix else row[0]
        module = relative.split("/")[0] if "/" in relative else ""
        modules.setdefault(module, deque()).append(row)
    ordered = []
    while modules:
        for module in list(modules):
            ordered.append(modules[module].popleft())
            if not modules[module]:
                del modules[module]
    admitted, consumed = [], 0
    for path, oid, size in ordered:
        if consumed + size > max_text_bytes:
            deferred.append(path)
            limits.add("analyzed_text_bytes")
        else:
            consumed += size
            admitted.append((path, oid, size))
    batch = (
        manager.git.run(
            "cat-file",
            "--batch",
            cwd=storage,
            input="".join(oid + "\n" for _, oid, _ in admitted).encode(),
            max_bytes=max_text_bytes + 1024 * 1024,
        )
        if admitted
        else b""
    )
    offset, files, objects = 0, [], {}
    for path, oid, size in admitted:
        header_end = batch.index(b"\n", offset)
        if batch[offset:header_end].decode() != f"{oid} blob {size}":
            raise RepositoryError("Git object batch did not match its tree.")
        data = batch[header_end + 1 : header_end + 1 + size]
        offset = header_end + size + 2
        try:
            text = data.decode("utf-8", errors="strict")
            if b"\0" in data:
                raise UnicodeError
        except UnicodeError:
            exclusions.append(Exclusion(path=path, reason="binary_or_non_utf8"))
            continue
        language, role, basis = classify(
            path, text, legacy=manager.inventory_policy == LEGACY_TEXT_POLICY
        )
        files.append(
            TextInventoryFile(
                path=path,
                sha256=hashlib.sha256(data).hexdigest(),
                bytes=size,
                language=language,
                role=role,
                classification_basis=basis,
                blob_oid=oid,
                lines=len(text.splitlines()),
            )
        )
        objects[path] = oid
    inventory = TextInventory(
        repo_id=repo_id,
        policy_version=manager.inventory_policy,
        files=files,
        excluded_paths=[e.path for e in exclusions],
        limits_reached=sorted(limits),
        tree_entries=entries,
        exclusions=exclusions,
        enumeration_complete=enumeration_complete,
        remaining_tree_entries=remaining_tree_entries,
        deferred_paths=sorted(set(deferred)),
    )
    inventory_id = f"{repo_id}-inventory.json"
    (manager.directory / inventory_id).write_text(
        inventory.model_dump_json(indent=2), encoding="utf-8"
    )
    licenses = []
    for entry in files:
        file = PurePosixPath(entry.path)
        if (
            str(file.parent) == (prefix or ".")
            and file.name.upper().startswith(("LICENSE", "NOTICE", "COPYING"))
            and entry.lines
        ):
            licenses.append(
                Ref(
                    repo_id=repo_id,
                    commit_sha=sha,
                    path=entry.path,
                    start_line=1,
                    end_line=entry.lines,
                    symbol=None,
                )
            )
    return PinnedRepository(
        manager.git,
        storage,
        RepoSnapshot(
            repo_id=repo_id,
            origin=origin,
            commit_sha=sha,
            inventory_artifact_id=inventory_id,
            license_refs=licenses,
        ),
        inventory,
        objects,
        prefix,
    )
