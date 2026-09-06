"""Typed configuration for the Galaxy Frog application."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPOSITORY_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    """Configuration loaded from environment variables and an optional local .env file."""

    model_config = SettingsConfigDict(
        env_file=(_REPOSITORY_ENV_FILE, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    app_env: Literal["development", "test", "production"] = "development"
    database_url: SecretStr | None = None
    ollama_base_url: str = "http://127.0.0.1:11434"
    embedding_model: str = "bge-m3"
    embedding_model_revision: str = "ollama"
    generation_model: str = "qwen3:4b"
    ingestion_worker_id: str | None = None
    ingestion_lease_seconds: int = Field(default=120, ge=30)
    ingestion_poll_seconds: float = Field(default=1.0, gt=0)

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        """Require the async PostgreSQL driver used by the backend."""

        if value is None:
            return value

        scheme = urlsplit(value.get_secret_value()).scheme
        if scheme != "postgresql+asyncpg":
            msg = "DATABASE_URL must use the postgresql+asyncpg scheme"
            raise ValueError(msg)

        return value

    @field_validator("ollama_base_url")
    @classmethod
    def validate_ollama_base_url(cls, value: str) -> str:
        """Require an explicit HTTP origin and discard a trailing slash."""

        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            msg = "OLLAMA_BASE_URL must be an absolute HTTP(S) URL"
            raise ValueError(msg)
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            msg = "OLLAMA_BASE_URL must not include a path, query, or fragment"
            raise ValueError(msg)
        return value.rstrip("/")

    @field_validator("ingestion_worker_id")
    @classmethod
    def validate_ingestion_worker_id(cls, value: str | None) -> str | None:
        """Normalize an optional operator-supplied durable lease owner."""

        if value is None:
            return None
        worker_id = value.strip()
        if not worker_id:
            msg = "INGESTION_WORKER_ID must not be blank"
            raise ValueError(msg)
        return worker_id
