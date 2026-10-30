"""F10 readiness must not turn local presence or failed F9 evidence into release claims."""

import json

import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.errors import EvidenceError
from scripts import validate_public_python as f10


def github_settings(**changes):
    return Settings(
        _env_file=None,
        **{
            "auth_mode": "github",
            "github_client_id": "test-client",
            "github_client_secret": "SECRET_CLIENT_SENTINEL",
            "github_app_slug": "test-app",
            "token_encryption_keys": Fernet.generate_key().decode(),
            "model_api_key": "SECRET_MODEL_SENTINEL",
            **changes,
        },
    )


@pytest.mark.parametrize(
    ("changes", "name", "status"),
    [
        ({"model_api_key": None}, "model_configuration", "blocked"),
        ({"github_client_secret": "  "}, "github_configuration", "blocked"),
        ({"token_encryption_keys": "SECRET_INVALID_KEY"}, "encryption_keys", "failed"),
        ({"app_base_url": "https://hosted.example"}, "local_origin", "blocked"),
    ],
)
def test_configuration_failures_are_actionable_and_secret_free(changes, name, status):
    settings = github_settings(**changes)
    checks = f10.configuration_checks(settings)
    assert next(check for check in checks if check.name == name).status == status
    assert "SECRET_" not in repr(checks)


def test_local_defaults_and_invalid_settings_cannot_claim_github_readiness():
    settings = Settings(_env_file=None, auth_mode="local", model_api_key=None)
    checks = f10.configuration_checks(settings)
    assert f10.readiness_status(checks, stable=True) == "blocked"
    assert f10.readiness_status(f10.configuration_checks(None), stable=True) == "failed"


@pytest.mark.parametrize("local_status", ["passed", "failed"])
@pytest.mark.parametrize("docker_status", ["passed", "failed", "not_run", "missing"])
def test_f9_requires_success_as_well_as_integrity_and_actual_docker(
    tmp_path, monkeypatch, local_status, docker_status
):
    path = tmp_path / "receipt.json"
    path.write_text(
        json.dumps(
            {
                "steps": []
                if docker_status == "missing"
                else [
                    {
                        "name": "docker",
                        "status": docker_status,
                        "exit_code": 0 if docker_status == "passed" else 1,
                        "junit": "docker-junit.xml",
                    },
                ]
            }
        )
    )
    monkeypatch.setattr(
        f10, "verify_receipt", lambda *args: {"integrity": "passed", "local_status": local_status}
    )
    check, digest = f10.f9_check(tmp_path, path)
    success = local_status == "passed" and docker_status == "passed"
    assert check.status == ("passed" if success else "failed")
    assert bool(digest) == success


def test_invalid_and_missing_f9_fail_closed_without_input_disclosure(tmp_path, monkeypatch):
    assert f10.f9_check(tmp_path, None)[0].status == "blocked"
    path = tmp_path / "receipt.json"
    path.write_text("SECRET_BROKEN_JSON")
    monkeypatch.setattr(f10, "verify_receipt", lambda *args: json.loads(path.read_bytes()))
    result = f10.f9_check(tmp_path, path)
    assert result[0].status == "failed"
    assert "SECRET_" not in repr(result)


def test_docker_status_without_retained_junit_is_insufficient(tmp_path, monkeypatch):
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps({"steps": [{"name": "docker", "status": "passed", "exit_code": 0}]}))
    monkeypatch.setattr(f10, "verify_receipt", lambda *args: {"local_status": "passed"})
    assert f10.f9_check(tmp_path, path)[0].status == "failed"


def test_source_drift_and_failed_checks_override_presence():
    ready = f10.configuration_checks(github_settings())
    assert f10.readiness_status(ready, stable=True) == "ready_for_live_validation"
    assert f10.readiness_status(ready, stable=False) == "failed"
    assert f10.readiness_status([], stable=True) == "blocked"


def test_cli_keeps_live_gates_pending_even_when_every_local_check_passes(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(f10, "ROOT", tmp_path)
    monkeypatch.setattr(f10, "Settings", github_settings)
    monkeypatch.setattr(f10, "source_manifest", lambda *args: {"app.py": "abc"})
    monkeypatch.setattr(
        f10, "local_checks", lambda *args: [f10.Check("runner", "passed", "Checked")]
    )
    monkeypatch.setattr(
        f10, "f9_check", lambda *args: (f10.Check("f9", "passed", "Checked"), "d" * 64)
    )
    assert f10.main([]) == 0
    summary = json.loads(capsys.readouterr().out)
    receipt = json.loads((tmp_path / summary["receipt"]).read_bytes())
    assert receipt["status"] == "ready_for_live_validation"
    assert receipt["f10_status"] == "pending_live_validation"
    assert {gate["status"] for gate in receipt["live_gates"].values()} == {"pending"}
    assert receipt["publishing_enabled"] is False
    assert "SECRET_" not in json.dumps(receipt)


def test_cli_rejects_redirected_output_before_configuration_access(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(f10, "ROOT", tmp_path)

    def deny(*args):
        raise EvidenceError("SECRET_LOCAL_PATH")

    monkeypatch.setattr(f10, "prepare_directory", deny)
    monkeypatch.setattr(
        f10, "Settings", lambda: pytest.fail("Must reject before accessing settings")
    )
    assert f10.main([]) == 1
    assert "SECRET_" not in capsys.readouterr().out
