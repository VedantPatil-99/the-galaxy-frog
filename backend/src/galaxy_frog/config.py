"""Typed configuration for the Galaxy Frog application."""

from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_REPOSITORY_ENV_FILE = _REPOSITORY_ROOT / ".env"


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
    job_dispatcher: Literal["local", "qstash"] = "local"
    qstash_token: SecretStr | None = None
    qstash_callback_url: str | None = None
    qstash_current_signing_key: SecretStr | None = None
    qstash_next_signing_key: SecretStr | None = None
    media_workspace_root: Path = _REPOSITORY_ROOT / "tmp" / "media"
    media_max_duration_seconds: int = Field(default=7200, ge=1)
    media_max_download_bytes: int = Field(default=268_435_456, ge=1)
    media_max_output_bytes: int = Field(default=268_435_456, ge=1)
    media_timeout_seconds: float = Field(default=600, gt=0)
    media_max_concurrency: int = Field(default=1, ge=1, le=4)
    media_retain_on_success: bool = False
    ffmpeg_executable: str = "ffmpeg"
    ffprobe_executable: str = "ffprobe"
    asr_provider: Literal["faster-whisper"] = "faster-whisper"
    asr_model: str = "small"
    asr_model_revision: str = "Systran/faster-whisper-small"
    asr_device: Literal["cpu", "cuda"] = "cuda"
    asr_compute_type: Literal["int8", "int8_float16", "float16", "float32"] = "int8_float16"
    asr_max_concurrency: int = Field(default=1, ge=1, le=4)

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

    @field_validator("qstash_callback_url")
    @classmethod
    def validate_qstash_callback_url(cls, value: str | None) -> str | None:
        """Require the exact absolute destination whose subject QStash signs."""

        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            msg = "QSTASH_CALLBACK_URL must be an absolute HTTP(S) URL"
            raise ValueError(msg)
        if parsed.query or parsed.fragment:
            msg = "QSTASH_CALLBACK_URL must not include a query or fragment"
            raise ValueError(msg)
        return value.rstrip("/")

    @field_validator("media_workspace_root")
    @classmethod
    def validate_media_workspace_root(cls, value: Path) -> Path:
        """Resolve local media beneath an explicit non-filesystem-root directory."""

        resolved = (value if value.is_absolute() else _REPOSITORY_ROOT / value).resolve()
        if resolved == Path(resolved.anchor):
            msg = "MEDIA_WORKSPACE_ROOT must not be a filesystem root"
            raise ValueError(msg)
        return resolved

    @field_validator("ffmpeg_executable", "ffprobe_executable")
    @classmethod
    def validate_media_executable(cls, value: str) -> str:
        """Reject blank executable configuration while allowing absolute paths."""

        executable = value.strip()
        if not executable:
            msg = "media executable paths must not be blank"
            raise ValueError(msg)
        return executable

    @field_validator("asr_model", "asr_model_revision")
    @classmethod
    def validate_asr_model_identity(cls, value: str) -> str:
        """Require explicit non-empty model identity and provenance."""

        identity = value.strip()
        if not identity:
            msg = "ASR model identity must not be blank"
            raise ValueError(msg)
        return identity

    @model_validator(mode="after")
    def validate_qstash_configuration(self) -> Self:
        """Keep hosted dispatch optional but complete when explicitly selected."""

        if self.job_dispatcher == "local":
            return self
        required = {
            "QSTASH_TOKEN": self.qstash_token,
            "QSTASH_CALLBACK_URL": self.qstash_callback_url,
            "QSTASH_CURRENT_SIGNING_KEY": self.qstash_current_signing_key,
            "QSTASH_NEXT_SIGNING_KEY": self.qstash_next_signing_key,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            msg = f"QStash dispatch requires: {', '.join(missing)}"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def validate_asr_device_compute_pair(self) -> Self:
        """Reject CTranslate2 compute modes that are invalid on the selected device."""

        if self.asr_device == "cpu" and self.asr_compute_type in {"float16", "int8_float16"}:
            msg = "CPU ASR does not support float16 compute; use int8 or float32"
            raise ValueError(msg)
        return self
