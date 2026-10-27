import asyncio
import json

import httpx

from app import smoke
from app.config import Settings
from app.main import create_app


def health_response(settings: Settings) -> httpx.Response:
    async def request():
        transport = httpx.ASGITransport(app=create_app(settings))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/health")

    return asyncio.run(request())


def test_health_works_without_credentials_and_does_not_claim_model_verification() -> None:
    response = health_response(Settings(_env_file=None, model_api_key=None))
    assert response.status_code == 200
    assert response.json()["model_configured"] is False
    assert response.json()["phase"] == "source_tracing"


def test_health_never_returns_the_configured_key() -> None:
    response = health_response(Settings(_env_file=None, model_api_key="offline-secret"))
    assert response.json()["model_configured"] is True
    assert "offline-secret" not in response.text


def test_smoke_cli_reports_missing_key_as_failure(monkeypatch, capsys) -> None:
    monkeypatch.setattr(smoke, "Settings", lambda: Settings(_env_file=None, model_api_key=None))
    assert smoke.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    error = json.loads(captured.err)
    assert error["status"] == "failed"
    assert error["error_code"] == "missing_configuration"
