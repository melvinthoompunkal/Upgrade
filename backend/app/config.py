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
    token_encryption_keys: SecretStr | None = Field(default=None, repr=False)

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
