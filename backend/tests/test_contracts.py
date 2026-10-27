import pytest
from pydantic import ValidationError

from app.config import Settings
from app.contracts import Ref, RepoSnapshot, RunPlan


def reference(**changes: object) -> Ref:
    return Ref.model_validate(
        {
            "repo_id": "source",
            "commit_sha": "a" * 40,
            "path": "src/retry.py",
            "start_line": 2,
            "end_line": 8,
            "symbol": "retry",
            **changes,
        }
    )


@pytest.mark.parametrize(
    "path",
    [
        "",
        ".",
        "../secret",
        "src/../secret",
        "/etc/passwd",
        "C:/secret",
        "C:secret",
        "src\\retry.py",
        "src//retry.py",
        "./src/retry.py",
        "src/retry.py/",
        "src/\x00key",
    ],
)
def test_references_reject_noncanonical_and_escaping_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        reference(path=path)


@pytest.mark.parametrize(
    "changes",
    [
        {"start_line": 0},
        {"end_line": 1},
        {"commit_sha": "main"},
        {"unexpected": True},
        {"start_line": "2"},
    ],
)
def test_references_reject_unpinned_or_invalid_evidence(changes: dict) -> None:
    with pytest.raises(ValidationError):
        reference(**changes)


def test_snapshot_rejects_license_evidence_from_a_different_commit() -> None:
    with pytest.raises(ValidationError, match="exact snapshot"):
        RepoSnapshot(
            repo_id="source",
            origin="https://github.com/scrapy/scrapy",
            commit_sha="b" * 40,
            inventory_artifact_id="inventory-1",
            license_refs=[reference()],
        )


def test_plan_requires_both_functions_once() -> None:
    with pytest.raises(ValidationError, match="exactly once"):
        RunPlan(
            source_focus_paths=["README.md"],
            target_focus_paths=["pyproject.toml"],
            feature_focus=["retry"],
            analysis_tasks=["trace_source", "trace_source"],
            rationale="Inspect both repositories.",
        )


def test_settings_redact_the_key_and_reject_contributor_models() -> None:
    settings = Settings(
        _env_file=None, model_api_key="offline-secret", meta_model_id="muse-spark-1.3"
    )
    assert "offline-secret" not in repr(settings)
    assert "offline-secret" not in settings.model_dump_json()
    with pytest.raises(ValidationError):
        Settings(_env_file=None, meta_model_id="muse-spark-1.3-contributor")


def test_empty_key_counts_as_unconfigured() -> None:
    assert not Settings(_env_file=None, model_api_key="  ").model_configured
