"""Real disposable Git repositories, never imports or executes their contents."""

from pathlib import Path

import pytest

from app.errors import EvidenceError, RepositoryError
from app.tools.repositories import (
    Registration,
    RepositoryManager,
    registered_target,
    verify_original,
)


def test_pin_independent_clone_worktree_and_dirty_original(committed_repo, tmp_path):
    root, git = committed_repo
    (root / "src/retry.py").write_text("dirty local change")
    manager = RepositoryManager(tmp_path / "managed")
    repo, original = manager.local_target(Registration("target", root))
    assert repo.read("src/retry.py").startswith("import time")
    assert original["uncommitted_changes_excluded"]
    assert not (repo.storage / "objects/info/alternates").exists()
    assert not {".env", "credentials.json", "binary.txt"} & set(repo.objects)
    assert repo.snapshot.license_refs[0].path == "LICENSE"
    worktree = manager.worktree(repo, "test-run")
    assert git.run("status", "--porcelain", cwd=worktree) == b""
    assert not (worktree / ".env").exists()
    assert not (worktree / "node_modules").exists()
    (worktree / "src/retry.py").write_text("managed edit")
    assert (root / "src/retry.py").read_text() == "dirty local change"
    assert verify_original(git, original)
    (root / "src/retry.py").write_text("second dirty change")
    assert not verify_original(git, original)
    repo.verify()
    with pytest.raises(EvidenceError):
        repo.read(".env")
    for path in ("../src/retry.py", "/etc/passwd", "C:/secret", "src\\retry.py"):
        with pytest.raises(ValueError):
            repo.read(path)
    gitdir = Path(git.run("rev-parse", "--git-common-dir", cwd=worktree).decode().strip())
    assert gitdir.resolve() == repo.storage.resolve()


def test_registered_monorepo_prefix_and_unknown_target(committed_repo, tmp_path):
    root, _ = committed_repo
    manager = RepositoryManager(tmp_path / "managed")
    repo, _ = manager.local_target(Registration("fixture", root, "src", ("retry.py",)))
    assert tuple(repo.objects) == ("retry.py",)
    tree = manager.worktree(repo, "prefixed")
    assert tree.name == "src"
    assert (tree / "retry.py").read_text().startswith("import time")
    assert manager.git.run("status", "--porcelain", cwd=tree) == b""
    with pytest.raises(RepositoryError, match="Unknown"):
        registered_target(root, "arbitrary-path")


def test_pinned_export_preserves_git_bytes_and_rejects_unapproved_paths(committed_repo, tmp_path):
    root, git = committed_repo
    content = b"# UTF-8: \xc3\xa9\r\nimport time\r\n"
    (root / "src/retry.py").write_bytes(content)
    git.run("add", "--", "src/retry.py", cwd=root)
    git.run("-c", "commit.gpgSign=false", "commit", "-m", "exact bytes", cwd=root)
    manager = RepositoryManager(tmp_path / "managed")
    repo, _ = manager.local_target(Registration("fixture", root))
    (root / "src/retry.py").write_text("dirty replacement")
    destination = tmp_path / "export"
    hashes = repo.export(("src/retry.py",), destination)
    assert (destination / "src/retry.py").read_bytes() == content
    assert hashes["src/retry.py"] == next(
        f.sha256 for f in repo.inventory.files if f.path == "src/retry.py"
    )
    with pytest.raises(EvidenceError):
        repo.export((".env",), tmp_path / "denied")
    assert not (tmp_path / "denied").exists()
    with pytest.raises(FileExistsError):
        repo.export(("src/retry.py",), destination)


def test_inventory_limits_are_reported(committed_repo, tmp_path):
    root, _ = committed_repo
    manager = RepositoryManager(tmp_path / "managed")
    repo, _ = manager.local_target(Registration("target", root))
    limited = manager._inventory(
        repo.storage, "small", "fixture", repo.snapshot.commit_sha, max_entries=1
    )
    assert limited.inventory.limits_reached == ("inventory_entries",)
    limited = manager._inventory(
        repo.storage, "small", "fixture", repo.snapshot.commit_sha, max_text_bytes=1
    )
    assert "analyzed_text_bytes" in limited.inventory.limits_reached


@pytest.mark.parametrize(
    "url,sha",
    [
        ("file:///outside", "a" * 40),
        ("file:///outside", None),
        ("https://token@github.com/org/repo", "a" * 40),
        ("https://token@github.com/org/repo", None),
        ("https://github.com/org/repo", "main"),
        ("https://evil.test/org/repo", "a" * 40),
        ("https://evil.test/org/repo", None),
    ],
)
def test_public_retrieval_rejects_credentials_protocols_and_unpinned_refs(tmp_path, url, sha):
    manager = RepositoryManager(tmp_path / "managed")
    with pytest.raises(RepositoryError):
        manager.public_source(url, sha)


def test_public_fetch_pins_exact_requested_commit(committed_repo, tmp_path, monkeypatch):
    root, git = committed_repo
    sha = git.run("rev-parse", "HEAD", cwd=root).decode().strip()
    manager = RepositoryManager(tmp_path / "managed")
    original_run = manager.git.run

    def local_transport(*args, **kwargs):
        assert args[0] != "ls-remote", "An explicit SHA must not look up a mutable revision."
        args = tuple(
            str(root) if arg == "https://github.com/example/fixture" else arg for arg in args
        )
        return original_run(*args, **kwargs)

    monkeypatch.setattr(manager.git, "run", local_transport)
    repo = manager.public_source("https://github.com/example/fixture", sha)
    assert repo.snapshot.commit_sha == sha
    assert repo.read("src/retry.py").startswith("import time")
    repo.verify()


@pytest.mark.parametrize("advance_after_resolution", [False, True])
def test_omitted_source_sha_pins_default_branch_once(
    committed_repo, tmp_path, monkeypatch, advance_after_resolution
):
    root, git = committed_repo
    git.run("branch", "-m", "fixture-default", cwd=root)
    (root / "src/retry.py").write_bytes(b"def retry():\n    return 3\n")
    git.run("add", "--all", cwd=root)
    git.run("-c", "commit.gpgSign=false", "commit", "-m", "latest default", cwd=root)
    sha = git.run("rev-parse", "HEAD", cwd=root).decode().strip()
    manager = RepositoryManager(tmp_path / "managed")
    run = manager.git.run
    resolutions = []
    fetches = []

    def local_transport(*args, **kwargs):
        result = run(
            *(str(root) if a == "https://github.com/example/fixture" else a for a in args),
            **kwargs,
        )
        if args[0] == "ls-remote":
            resolutions.append(args)
            if advance_after_resolution:
                (root / "src/retry.py").write_bytes(b"def retry():\n    return 4\n")
                git.run("add", "--all", cwd=root)
                git.run("-c", "commit.gpgSign=false", "commit", "-m", "moved default", cwd=root)
        if args[0] == "fetch":
            fetches.append(args[-1])
        return result

    monkeypatch.setattr(manager.git, "run", local_transport)
    repo = manager.public_source("https://github.com/example/fixture")
    assert len(resolutions) == 1
    assert fetches == [sha]
    assert repo.snapshot.commit_sha == sha
    assert repo.read("src/retry.py") == "def retry():\n    return 3\n"
    if advance_after_resolution:
        assert git.run("rev-parse", "HEAD", cwd=root).decode().strip() != sha
    repo.verify()


@pytest.mark.parametrize("head", [b"", b"not-a-sha\tHEAD\n", b"a" * 40 + b"\trefs/heads/main\n"])
def test_missing_or_invalid_default_head_stops_before_fetch(tmp_path, monkeypatch, head):
    manager = RepositoryManager(tmp_path / "managed")

    def lookup_only(*args, **kwargs):
        assert args[0] == "ls-remote"
        return head

    monkeypatch.setattr(manager.git, "run", lookup_only)
    with pytest.raises(RepositoryError, match="default branch"):
        manager.public_source("https://github.com/example/fixture")
    assert not (manager.directory / "source.git").exists()


def test_public_target_snapshot_is_independent_and_preserves_project_prefix(
    committed_repo, tmp_path, monkeypatch
):
    root, git = committed_repo
    sha = git.run("rev-parse", "HEAD", cwd=root).decode().strip()
    manager = RepositoryManager(tmp_path / "managed")
    run = manager.git.run

    def local_transport(*args, **kwargs):
        return run(
            *(str(root) if a == "https://github.com/example/fixture" else a for a in args), **kwargs
        )

    monkeypatch.setattr(manager.git, "run", local_transport)
    repo = manager.public_snapshot(
        "https://github.com/example/fixture", sha, role="target", prefix="src"
    )
    assert repo.snapshot.repo_id == "target" and repo.prefix == "src"
    assert repo.snapshot.commit_sha == sha and repo.storage.name == "target.git"
    assert repo.read("retry.py").startswith("import time")
    assert not (repo.storage / "objects/info/alternates").exists()
    repo.verify()
