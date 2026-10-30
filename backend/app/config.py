import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

META_BASE_URL = "https://api.meta.ai/v1"
BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
    )

    model_api_key: SecretStr | None = Field(default=None, repr=False)
    meta_model_id: Literal["muse-spark-1.3", "muse-spark-1.2", "muse-spark-1.1"] = "muse-spark-1.3"
    meta_timeout_seconds: float = Field(default=45, gt=0, le=120)
    meta_max_retries: int = Field(default=2, ge=0, le=2)
    meta_max_completion_tokens: int = Field(default=4096, ge=256, le=8000)
    meta_trace_max_completion_tokens: int = Field(default=8000, ge=256, le=8000)
    auth_mode: Literal["local", "github"] = "local"
    app_base_url: str = "http://127.0.0.1:5173"
    github_client_id: str = ""
    github_client_secret: SecretStr | None = Field(default=None, repr=False)
    github_app_slug: str = ""
    github_app_id: int | None = Field(default=None, gt=0)
    github_private_key_path: Path | None = Field(default=None, repr=False)
    github_webhook_secret: SecretStr | None = Field(default=None, repr=False)
    github_private_fetch_enabled: bool = False
    token_encryption_keys: SecretStr | None = Field(default=None, repr=False)
    deployment_mode: Literal["local", "hosted"] = "local"
    data_root: Path | None = None
    deployment_id: str = Field(default="", pattern=r"^[A-Za-z0-9_-]{0,80}$")
    hosted_release_path: Path | None = None
    operator_github_ids: tuple[int, ...] = ()
    job_lease_seconds: int = Field(default=30, ge=5, le=300)
    job_max_seconds: int = Field(default=1200, ge=1, le=3600)
    job_owner_limit: int = Field(default=4, ge=1, le=16)
    job_global_limit: int = Field(default=16, ge=1, le=64)
    job_artifact_bytes: int = Field(default=200 * 1024 * 1024, ge=1024)
    storage_owner_bytes: int = Field(default=2 * 1024**3, ge=1024)
    storage_global_bytes: int = Field(default=8 * 1024**3, ge=1024)
    daily_github_requests: int = Field(default=1000, ge=1)
    daily_model_attempts: int = Field(default=128, ge=1)
    daily_model_input_tokens: int = Field(default=1_600_000, ge=1)
    daily_model_output_tokens: int = Field(default=160_000, ge=1)
    global_daily_github_requests: int = Field(default=4000, ge=1)
    global_daily_model_attempts: int = Field(default=512, ge=1)
    global_daily_model_input_tokens: int = Field(default=6_400_000, ge=1)
    global_daily_model_output_tokens: int = Field(default=640_000, ge=1)
    retention_days: int = Field(default=30, ge=1, le=365)

    @field_validator(
        "github_app_id",
        "github_private_key_path",
        "data_root",
        "hosted_release_path",
        mode="before",
    )
    @classmethod
    def empty_optional_setting(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("app_base_url")
    @classmethod
    def valid_base_url(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or any(ord(c) < 32 for c in value)
            or url.port == 0
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
            or (url.scheme == "http" and url.hostname not in {"127.0.0.1", "localhost", "::1"})
        ):
            raise ValueError("Use an HTTPS origin or a loopback HTTP development origin.")
        return value.rstrip("/")

    @model_validator(mode="after")
    def github_configuration(self) -> "Settings":
        if self.auth_mode == "github":
            if not all(
                (
                    self.github_client_id,
                    self.github_client_secret,
                    self.github_app_slug,
                    self.token_encryption_keys,
                )
            ):
                raise ValueError(
                    "GitHub mode requires client ID/secret, app slug and encryption keys."
                )
            if not re.fullmatch(r"[A-Za-z0-9-]+", self.github_app_slug):
                raise ValueError("Invalid GitHub App slug.")
            if self.github_private_fetch_enabled and not all(
                (self.github_app_id, self.github_private_key_path, self.github_webhook_secret)
            ):
                raise ValueError("Private fetch requires App ID, private key and webhook secret.")
        elif self.github_private_fetch_enabled:
            raise ValueError("Private fetch requires GitHub authentication mode.")
        if self.deployment_mode == "hosted":
            if (
                self.auth_mode != "github"
                or urlsplit(self.app_base_url).scheme != "https"
                or urlsplit(self.app_base_url).hostname in {"localhost", "127.0.0.1", "::1"}
                or not self.deployment_id
                or self.data_root is None
                or not self.data_root.is_absolute()
            ):
                raise ValueError(
                    "Hosted mode requires GitHub, public HTTPS, a deployment ID "
                    "and absolute DATA_ROOT."
                )
            if self.github_private_fetch_enabled:
                raise ValueError("Hosted private retrieval requires a separately reviewed profile.")
            if self.hosted_release_path is not None and (
                not self.hosted_release_path.is_absolute()
                or self.hosted_release_path.resolve().is_relative_to(self.data_root.resolve())
            ):
                raise ValueError(
                    "Keep the absolute hosted release path outside writable data storage."
                )
        return self

    @field_validator("model_api_key", mode="before")
    @classmethod
    def normalize_key(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @property
    def model_configured(self) -> bool:
        return self.model_api_key is not None and bool(self.model_api_key.get_secret_value())
