import pytest

from app.tools.evidence import EvidenceSession
from app.tools.git import Git
from app.tools.repositories import Registration, RepositoryManager


@pytest.fixture
def committed_repo(tmp_path):
    root = tmp_path / "original"
    root.mkdir()
    git = Git(tmp_path / "git-control")
    git.run("init", "--template=", str(root))
    files = {
        "src/retry.py": "import time\n\ndef retry():\n    return 2\n",
        "tests/test_retry.py": "def test_retry():\n    assert True\n",
        "LICENSE": "Copyright fixture\nMIT License\n",
        ".env": "must never be inventoried\n",
        "credentials.json": "{}",
        "node_modules/dependency.py": "raise RuntimeError('never execute')\n",
        "binary.txt": "a\x00b",
        "pyproject.toml": '[project]\nname="fixture"\ndependencies=[]\n',
    }
    for path, text in files.items():
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    git.run("add", "--all", cwd=root)
    git.run("-c", "commit.gpgSign=false", "commit", "-m", "fixture", cwd=root)
    return root, git


@pytest.fixture
def session(committed_repo, tmp_path):
    root, _ = committed_repo
    manager = RepositoryManager(tmp_path / "managed")
    repo, _ = manager.local_target(Registration("source", root))
    return EvidenceSession(repo)
