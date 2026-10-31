"""SHA-pinned public sources and explicitly registered local targets. Never import repo code."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.contracts import Ref, RepoSnapshot, validate_repo_path
from app.errors import EvidenceError, RepositoryError
from app.evidence_contracts import Inventory, InventoryFile
from app.tools.git import Git
from app.tools.runner_artifacts import MAX_ARTIFACT_BYTES, physical_root
from app.tools.targets import CRAWLER_FILES, CRAWLER_PREFIX

SCRAPY_URL = "https://github.com/scrapy/scrapy"
SCRAPY_SHA = "5c7886a3f27dbf8b66f557cc9945dbc4a6aceaac"
TEXT_SUFFIXES = {".py", ".md", ".rst", ".txt", ".toml", ".ini", ".cfg", ".json", ".yaml", ".yml"}
SKIP_DIRS = {
    ".git",
    ".github",
    ".venv",
    "venv",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "__pycache__",
    ".ssh",
    ".aws",
    ".secrets",
}


def permitted_path(path: str) -> bool:
    try:
        validate_repo_path(path)
    except ValueError:
        return False
    parts = PurePosixPath(path).parts
    name = parts[-1].lower()
    return not (
        set(parts) & SKIP_DIRS
        or name.startswith((".env", "id_rsa", "id_ed25519"))
        or any(word in name for word in ("credential", "service-account", "secret"))
        or PurePosixPath(path).suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".keystore"}
    )


@dataclass(frozen=True)
class Registration:
    repo_id: str
    root: Path
    prefix: str = ""
    paths: tuple[str, ...] | None = None


@dataclass(frozen=True)
class PinnedRepository:
    git: Git
    storage: Path
    snapshot: RepoSnapshot
    inventory: Inventory
    objects: dict[str, str]
    prefix: str = ""

    def read(self, path: str) -> str:
        validate_repo_path(path)
        entries = {entry.path: entry for entry in self.inventory.files}
        if path not in entries or path not in self.objects:
            raise EvidenceError("Path is absent from the approved text inventory.")
        data = self.git.run(
            "cat-file", "blob", self.objects[path], cwd=self.storage, max_bytes=MAX_ARTIFACT_BYTES
        )
        if (
            len(data) != entries[path].bytes
            or hashlib.sha256(data).hexdigest() != entries[path].sha256
        ):
            raise EvidenceError("Pinned file no longer matches its inventory hash.")
        return data.decode("utf-8")

    def export(self, paths: tuple[str, ...], destination: Path) -> dict[str, str]:
        """Export approved Git blob bytes, never the original checkout or its dirty files."""
        content = {path: self.read(path).encode("utf-8") for path in paths}
        physical_root(destination.parent)
        destination.mkdir(exist_ok=False)
        for relative, data in content.items():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return {path: hashlib.sha256(data).hexdigest() for path, data in content.items()}

    def verify(self) -> None:
        actual = self.git.run("rev-parse", "HEAD", cwd=self.storage).decode().strip()
        if actual != self.snapshot.commit_sha:
            raise EvidenceError("Managed repository HEAD changed after pinning.")
        # Verify Git object contents against their IDs, including the pinned commit/tree.
        self.git.run("fsck", "--full", "--no-reflogs", cwd=self.storage)


class RepositoryManager:
    def __init__(self, directory: Path, *, existing: bool = False):
        if not existing:
            directory.mkdir(parents=True, exist_ok=False)
        self.directory = physical_root(directory)
        for control in (self.directory / "control", self.directory / "control/empty-hooks"):
            if control.exists() or control.is_symlink() or control.is_junction():
                physical_root(control)
        self.git = Git(self.directory / "control")

    def reopen(self, *, role: str, url: str, sha: str, prefix: str) -> PinnedRepository:
        """Rebuild inventory from an already pinned clone, without a ref lookup or fetch."""
        if role not in {"source", "target"}:
            raise RepositoryError("Unknown snapshot role.")
        storage = physical_root(self.directory / f"{role}.git")
        actual = self.git.run("rev-parse", "HEAD^{commit}", cwd=storage).decode().strip()
        if actual != sha:
            raise EvidenceError("Stored project HEAD no longer matches its selection.")
        return self._inventory(storage, role, url, sha, prefix)

    def public_source(self, url: str, sha: str | None = None) -> PinnedRepository:
        """Resolve an omitted source revision once, then fetch only that immutable commit."""
        return self.public_snapshot(url, sha, role="source")

    def public_snapshot(
        self, url: str, sha: str | None = None, *, role: str, prefix: str = ""
    ) -> PinnedRepository:
        """Pin public project data for either role, without enabling executable support."""
        if role not in {"source", "target"}:
            raise RepositoryError("Snapshot role must be source or target.")
        if prefix:
            validate_repo_path(prefix)
        if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", url):
            raise RepositoryError("Only public HTTPS GitHub repository URLs are supported.")
        if sha is None:
            head = self.git.run("ls-remote", "--exit-code", url, "HEAD", max_bytes=200)
            match = re.fullmatch(rb"([0-9a-f]{40}(?:[0-9a-f]{24})?)\tHEAD\n?", head)
            if match is None:
                raise RepositoryError(
                    "The repository default branch has no supported commit snapshot."
                )
            sha = match[1].decode("ascii")
        if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", sha):
            raise RepositoryError("Source revision must be a full commit SHA.")
        return self.fetch_snapshot(url, sha, role=role, prefix=prefix)

    def fetch_snapshot(
        self,
        url: str,
        sha: str,
        *,
        role: str,
        prefix: str = "",
        credential_channel: dict[str, str] | None = None,
    ) -> PinnedRepository:
        """Internal broker entry; callers authorize GitHub identity before supplying credentials."""
        if (
            role not in {"source", "target"}
            or not re.fullmatch(
                r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", url
            )
            or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", sha)
        ):
            raise RepositoryError("Invalid immutable GitHub snapshot input.")
        if prefix:
            validate_repo_path(prefix)
        storage = self.directory / f"{role}.git"
        self.git.run("init", "--bare", "--template=", str(storage))
        self.git.run(
            "fetch",
            "--depth=1",
            "--no-tags",
            "--no-recurse-submodules",
            url,
            sha,
            cwd=storage,
            credential_channel=credential_channel,
        )
        actual = self.git.run("rev-parse", "FETCH_HEAD^{commit}", cwd=storage).decode().strip()
        if actual != sha:
            raise RepositoryError("Fetched source did not match the requested SHA.")
        self.git.run("update-ref", "refs/heads/pinned", sha, cwd=storage)
        self.git.run("symbolic-ref", "HEAD", "refs/heads/pinned", cwd=storage)
        return self._inventory(storage, role, url, sha, prefix)

    def local_target(self, registration: Registration) -> tuple[PinnedRepository, dict]:
        root = physical_root(registration.root)
        # A registered repository must own its .git directory, not point at another checkout.
        if not (root / ".git").is_dir():
            raise RepositoryError("Registered local target must own its Git directory.")
        physical_root(root / ".git")
        sha = self.git.run("rev-parse", "HEAD^{commit}", cwd=root).decode().strip()
        dirty_before = original_fingerprint(self.git, root, registration.prefix)
        storage = self.directory / "target.git"
        self.git.run(
            "clone",
            "--bare",
            "--no-local",
            "--no-hardlinks",
            "--no-tags",
            "--template=",
            "--",
            str(root),
            str(storage),
        )
        if (storage / "objects" / "info" / "alternates").exists():
            raise RepositoryError("Managed clone must use independent Git storage.")
        self.git.run("update-ref", "refs/heads/pinned", sha, cwd=storage)
        self.git.run("symbolic-ref", "HEAD", "refs/heads/pinned", cwd=storage)
        repo = self._inventory(
            storage,
            registration.repo_id,
            f"registered:{registration.repo_id}",
            sha,
            registration.prefix,
            registration.paths,
        )
        return repo, {
            "root": str(root),
            "prefix": registration.prefix,
            "head": sha,
            "dirty_state_sha256": dirty_before["sha256"],
            "uncommitted_changes_excluded": dirty_before["dirty"],
        }

    def worktree(self, repo: PinnedRepository, run_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9-]{1,80}", run_id):
            raise RepositoryError("Invalid managed worktree ID.")
        tree = self.directory / "worktree"
        self.git.run(
            "worktree",
            "add",
            "--no-checkout",
            "-b",
            f"graft/{run_id}",
            str(tree),
            repo.snapshot.commit_sha,
            cwd=repo.storage,
        )
        # Keep the full base index while materializing only approved target files. Omitted
        # monorepo files must not appear as deletions in a later patch.
        paths = [f"{repo.prefix}/{f.path}" if repo.prefix else f.path for f in repo.inventory.files]
        if not paths:
            raise RepositoryError("Registered target has no approved text files to check out.")
        self.git.run(
            "sparse-checkout",
            "set",
            "--no-cone",
            "--stdin",
            cwd=tree,
            input="".join(f"/{path}\n" for path in paths).encode(),
        )
        self.git.run("read-tree", "-mu", "HEAD", cwd=tree)
        target = tree / repo.prefix
        for entry in repo.inventory.files:
            data = (target / entry.path).read_bytes()
            if hashlib.sha256(data).hexdigest() != entry.sha256:
                raise RepositoryError("Worktree file did not match pinned Git bytes.")
        return target

    def _inventory(
        self,
        storage: Path,
        repo_id: str,
        origin: str,
        sha: str,
        prefix: str = "",
        allowed: tuple[str, ...] | None = None,
        *,
        max_entries: int = 4000,
        max_text_bytes: int = 50 * 1024 * 1024,
    ) -> PinnedRepository:
        if prefix:
            validate_repo_path(prefix)
        raw = self.git.run("ls-tree", "-r", "-l", "-z", "--full-tree", sha, cwd=storage)
        candidates = []
        excluded, limits = [], []
        consumed = 0
        records = [record for record in raw.split(b"\0") if record]
        for record in records:
            metadata, raw_path = record.split(b"\t", 1)
            full_path = raw_path.decode("utf-8", errors="replace")
            if prefix and not full_path.startswith(prefix + "/"):
                continue
            path = full_path[len(prefix) + 1 :] if prefix else full_path
            mode, kind, oid, size = metadata.split()
            if len(candidates) + len(excluded) >= max_entries:
                limits.append("inventory_entries")
                break
            if (
                mode not in {b"100644", b"100755"}
                or kind != b"blob"
                or not permitted_path(path)
                or (allowed is not None and path not in allowed)
                or (
                    PurePosixPath(path).suffix.lower() not in TEXT_SUFFIXES
                    and not PurePosixPath(path)
                    .name.upper()
                    .startswith(("LICENSE", "COPYING", "NOTICE"))
                )
            ):
                excluded.append(path)
                continue
            length = int(size)
            if length > MAX_ARTIFACT_BYTES:
                excluded.append(path)
                limits.append("file_bytes")
                continue
            if consumed + length > max_text_bytes:
                excluded.append(path)
                limits.append("analyzed_text_bytes")
                continue
            consumed += length
            candidates.append((path, oid.decode(), length))
        # Batch objects once; no checkout, filters, interpreter, or repository hooks.
        batch = self.git.run(
            "cat-file",
            "--batch",
            cwd=storage,
            input="".join(oid + "\n" for _, oid, _ in candidates).encode(),
            max_bytes=max_text_bytes + 1024 * 1024,
        )
        offset, files, objects = 0, [], {}
        for path, oid, size in candidates:
            header_end = batch.index(b"\n", offset)
            if batch[offset:header_end].decode() != f"{oid} blob {size}":
                raise RepositoryError("Git object batch did not match its tree.")
            data = batch[header_end + 1 : header_end + 1 + size]
            offset = header_end + size + 2
            try:
                data.decode("utf-8")
                if b"\0" in data:
                    raise UnicodeError
            except UnicodeError:
                excluded.append(path)
                continue
            objects[path] = oid
            files.append(
                InventoryFile(
                    path=path,
                    sha256=hashlib.sha256(data).hexdigest(),
                    bytes=size,
                    language="python" if path.endswith(".py") else "text",
                )
            )
        inventory = Inventory(
            repo_id=repo_id,
            files=files,
            excluded_paths=excluded,
            limits_reached=sorted(set(limits)),
        )
        inventory_id = f"{repo_id}-inventory.json"
        (self.directory / inventory_id).write_text(
            inventory.model_dump_json(indent=2), encoding="utf-8"
        )
        licenses = []
        temporary = PinnedRepository(
            self.git,
            storage,
            RepoSnapshot(
                repo_id=repo_id,
                origin=origin,
                commit_sha=sha,
                inventory_artifact_id=inventory_id,
                license_refs=[],
            ),
            inventory,
            objects,
            prefix,
        )
        for entry in files:
            if "/" not in entry.path and entry.path.upper().startswith(
                ("LICENSE", "COPYING", "NOTICE")
            ):
                line_count = len(temporary.read(entry.path).splitlines())
                if not line_count:
                    continue
                licenses.append(
                    Ref(
                        repo_id=repo_id,
                        commit_sha=sha,
                        path=entry.path,
                        start_line=1,
                        end_line=line_count,
                        symbol=None,
                    )
                )
        return PinnedRepository(
            self.git,
            storage,
            temporary.snapshot.model_copy(update={"license_refs": tuple(licenses)}),
            inventory,
            objects,
            prefix,
        )


def registered_target(project_root: Path, target_id: str) -> Registration:
    if target_id != "crawler-demo":
        raise RepositoryError("Unknown registered target; arbitrary host paths are not accepted.")
    return Registration(target_id, project_root, CRAWLER_PREFIX, CRAWLER_FILES)


def verify_original(git: Git, state: dict) -> bool:
    root = physical_root(Path(state["root"]))
    head = git.run("rev-parse", "HEAD^{commit}", cwd=root).decode().strip()
    return (
        head == state["head"]
        and original_fingerprint(git, root, state.get("prefix", ""))["sha256"]
        == state["dirty_state_sha256"]
    )


def original_fingerprint(git: Git, root: Path, prefix: str = "") -> dict:
    scope = ("--", prefix) if prefix else ("--",)
    status = git.run("status", "--porcelain=v1", "-z", "--untracked-files=normal", *scope, cwd=root)
    diff = git.run(
        "diff",
        "--binary",
        "--no-ext-diff",
        "--no-textconv",
        "HEAD",
        *scope,
        cwd=root,
        max_bytes=50 * 1024 * 1024,
    )
    return {"sha256": hashlib.sha256(status + diff).hexdigest(), "dirty": bool(status)}
