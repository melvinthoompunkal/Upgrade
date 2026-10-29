"""Bounded GitHub App user authorization; credentials stay in this backend."""

import base64
import json
import time
from datetime import UTC, datetime
from time import perf_counter
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.config import Settings
from app.errors import GraftError
from app.jobs.control import checkpoint
from app.jobs.quotas import charge, metric


class GitHubError(GraftError):
    code = "github_unavailable"


class GitHubDenied(GitHubError):
    code = "github_access_denied"


class GitHubRateLimited(GitHubError):
    code = "github_rate_limited"

    def __init__(self, retry_after: int):
        super().__init__("GitHub is rate limited. Wait before trying again.")
        self.retry_after = retry_after


class InstallationToken(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)
    token: SecretStr = Field(min_length=1, repr=False)
    expires_at: datetime
    permissions: dict[str, str]
    repositories: list[dict]


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

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        data=None,
        json_body=None,
        basic: bool = False,
        expected: tuple[int, ...] = (200,),
    ):
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
        auth = (
            (self.settings.github_client_id, self.settings.github_client_secret.get_secret_value())
            if basic
            else None
        )
        started = perf_counter()
        try:
            with httpx.Client(
                timeout=10, follow_redirects=False, transport=self.transport, trust_env=False
            ) as client:
                for attempt in range(2 if method == "GET" else 1):
                    charge("github_requests")
                    with client.stream(
                        method, url, headers=headers, data=data, json=json_body, auth=auth
                    ) as response:
                        if response.status_code == 429 or (
                            response.status_code == 403
                            and (
                                response.headers.get("x-ratelimit-remaining") == "0"
                                or "retry-after" in response.headers
                            )
                        ):
                            try:
                                delay = float(
                                    response.headers.get("retry-after")
                                    or max(
                                        60,
                                        float(response.headers.get("x-ratelimit-reset", "0"))
                                        - time.time(),
                                    )
                                )
                                delay = min(3600, max(1, int(delay)))
                            except (ValueError, OverflowError):
                                delay = 60
                            raise GitHubRateLimited(delay)
                        if response.status_code in {401, 403, 404}:
                            raise GitHubDenied(
                                "GitHub access is unavailable. Check grants or reconnect."
                            )
                        if (
                            response.status_code in {502, 503, 504}
                            and attempt == 0
                            and method == "GET"
                        ):
                            continue
                        if response.status_code not in expected:
                            raise GitHubError("GitHub is unavailable. Try again later.")
                        if response.status_code == 204:
                            return {}
                        content = bytearray()
                        for chunk in response.iter_bytes():
                            checkpoint()
                            content.extend(chunk)
                            if len(content) > 2 * 1024 * 1024:
                                raise GitHubError("GitHub response exceeded the supported size.")
                        result = json.loads(content)
                        if not isinstance(result, dict):
                            raise GitHubError("GitHub returned an invalid response shape.")
                        return result
        except (httpx.HTTPError, ValueError):
            raise GitHubError("GitHub did not return a usable response. Try again later.") from None
        finally:
            metric("github_latency_ms", round((perf_counter() - started) * 1000))

    def app_jwt(self) -> str:
        try:
            path = self.settings.github_private_key_path
            if not self.settings.github_app_id or not path or path.stat().st_size > 65536:
                raise ValueError
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
            if not isinstance(key, rsa.RSAPrivateKey):
                raise ValueError

            def encode(value):
                return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=")

            now = int(time.time())
            body = (
                encode({"alg": "RS256", "typ": "JWT"})
                + b"."
                + encode(
                    {"iat": now - 60, "exp": now + 540, "iss": str(self.settings.github_app_id)}
                )
            )
            signature = key.sign(body, padding.PKCS1v15(), hashes.SHA256())
            return (body + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()
        except (OSError, ValueError, TypeError):
            raise GitHubError("Configure a usable GitHub App RSA private key.") from None

    def installation_token(self, installation: int, repository: int) -> InstallationToken:
        body = self.request(
            "POST",
            f"/app/installations/{installation}/access_tokens",
            token=self.app_jwt(),
            json_body={"repository_ids": [repository], "permissions": {"contents": "read"}},
            expected=(201,),
        )
        try:
            result = InstallationToken.model_validate(body)
            if (
                result.expires_at.tzinfo is None
                or not 60 < (result.expires_at - datetime.now(UTC)).total_seconds() <= 3660
                or result.permissions.get("contents") != "read"
                or any(type(r.get("id")) is not int for r in result.repositories)
                or [r.get("id") for r in result.repositories] != [repository]
            ):
                raise ValueError
            return result
        except (ValidationError, ValueError):
            if isinstance(body.get("token"), str) and body["token"]:
                try:
                    self.revoke_installation_token(body["token"])
                except GitHubError:
                    pass
            raise GitHubError(
                "GitHub returned an unusable restricted installation token."
            ) from None

    def revoke_installation_token(self, token: str) -> None:
        self.request("DELETE", "/installation/token", token=token, expected=(204,))

    def revoke_authorization(self, token: str) -> None:
        self.request(
            "DELETE",
            f"/applications/{quote(self.settings.github_client_id, safe='')}/grant",
            json_body={"access_token": token},
            basic=True,
            expected=(204,),
        )

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
