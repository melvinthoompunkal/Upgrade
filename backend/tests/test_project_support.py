from dataclasses import replace

from app.project_support import inspect_project


def test_readable_python_project_does_not_claim_executable_support(session):
    support = inspect_project(session.repo)
    assert support.name == "fixture" and support.python_files > 0
    assert support.capability == "browse_index"
    assert not support.execution_supported and not support.adaptation_supported
    assert support.manifest_sha256
    assert "offline test profile" in support.reasons[0]


def test_missing_manifest_is_explained_without_execution(session):
    repo = session.repo
    inventory = repo.inventory.model_copy(
        update={
            "files": tuple(
                entry for entry in repo.inventory.files if entry.path != "pyproject.toml"
            )
        }
    )
    support = inspect_project(replace(repo, inventory=inventory))
    assert support.manifest_sha256 is None and not support.execution_supported
    assert "No Python project manifest" in support.reasons[0]
