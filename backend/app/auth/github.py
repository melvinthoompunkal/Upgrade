"""Bounded GitHub App user authorization; credentials stay in this backend."""

import json

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.config import Settings
from app.errors import GraftError


class GitHubError(GraftError):
    code = "github_unavailable"


class GitHubTokens(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)
    access_token: SecretStr = Field(min_length=1)
    refresh_token: SecretStr = Field(min_length=1)
    expires_in: int = Field(strict=True, gt=0)
    refresh_token_expires_in: int = Field(strict=True, gt=0)
    token_type: str


class GitHubUser(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)
    id: int = Field(strict=True, gt=0)
    login: str = Field(min_length=1, max_length=100)
    name: str | None = None


class GitHubClient:
    def __init__(self, settings: Settings, *, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    def request(self, method: str, path: str, *, token: str | None = None, data=None):
        # Callers supply only fixed endpoint prefixes and validated numeric IDs.
        url = (
            "https://github.com/login/oauth/access_token"
            if data
            else "https://api.github.com" + path
        )
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"}
        if data:
            headers["Accept"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            with (
                httpx.Client(
                    timeout=10, follow_redirects=False, transport=self.transport, trust_env=False
                ) as client,
                client.stream(method, url, headers=headers, data=data) as response,
            ):
                if response.status_code in {401, 403, 404}:
                    raise GitHubError("GitHub access is unavailable. Check grants or reconnect.")
                if response.status_code != 200:
                    raise GitHubError("GitHub is unavailable or rate limited. Try again later.")
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > 2 * 1024 * 1024:
                        raise GitHubError("GitHub response exceeded the supported size.")
                result = json.loads(content)
                if not isinstance(result, dict):
                    raise GitHubError("GitHub returned an invalid response shape.")
                return result
        except (httpx.HTTPError, ValueError):
            raise GitHubError("GitHub did not return a usable response. Try again later.") from None

    def tokens(self, **data) -> GitHubTokens:
        config = self.settings
        try:
            result = GitHubTokens.model_validate(
                self.request(
                    "POST",
                    "",
                    data={
                        "client_id": config.github_client_id,
                        "client_secret": config.github_client_secret.get_secret_value(),
                        **data,
                    },
                )
            )
            if result.token_type.lower() != "bearer":
                raise ValueError("Unsupported token type")
            return result
        except (ValidationError, ValueError):
            raise GitHubError("GitHub authorization failed. Start sign-in again.") from None

    def exchange(self, code: str, verifier: str) -> GitHubTokens:
        return self.tokens(
            code=code,
            code_verifier=verifier,
            redirect_uri=self.settings.app_base_url + "/api/auth/github/callback",
        )

    def refresh(self, token: str) -> GitHubTokens:
        return self.tokens(grant_type="refresh_token", refresh_token=token)

    def profile(self, token: str) -> GitHubUser:
        try:
            return GitHubUser.model_validate(self.request("GET", "/user", token=token))
        except ValidationError:
            raise GitHubError("GitHub did not return a valid account identity.") from None
