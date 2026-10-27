from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
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

    @field_validator("model_api_key", mode="before")
    @classmethod
    def normalize_key(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @property
    def model_configured(self) -> bool:
        return self.model_api_key is not None and bool(self.model_api_key.get_secret_value())
