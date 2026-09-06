"""Behavior tests for typed application settings."""

import pytest
from pydantic import ValidationError

from galaxy_frog.config import Settings


def test_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://galaxy_frog:secret@localhost:5432/galaxy_frog",
    )
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/")
    monkeypatch.setenv("EMBEDDING_MODEL", "bge-m3:latest")
    monkeypatch.setenv("EMBEDDING_MODEL_REVISION", "sha256:test")
    monkeypatch.setenv("GENERATION_MODEL", "qwen3:8b")
    monkeypatch.setenv("INGESTION_WORKER_ID", " worker-a ")
    monkeypatch.setenv("INGESTION_LEASE_SECONDS", "180")
    monkeypatch.setenv("INGESTION_POLL_SECONDS", "0.5")

    settings = Settings()

    assert settings.app_env == "test"
    assert settings.database_url is not None
    assert settings.database_url.get_secret_value().startswith("postgresql+asyncpg://")
    assert settings.ollama_base_url == "http://127.0.0.1:11434"
    assert settings.embedding_model == "bge-m3:latest"
    assert settings.embedding_model_revision == "sha256:test"
    assert settings.generation_model == "qwen3:8b"
    assert settings.ingestion_worker_id == "worker-a"
    assert settings.ingestion_lease_seconds == 180
    assert settings.ingestion_poll_seconds == 0.5


def test_settings_reject_an_unknown_environment() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"app_env": "preview"})


def test_settings_reject_a_non_async_database_url() -> None:
    with pytest.raises(ValidationError, match="postgresql\\+asyncpg"):
        Settings.model_validate({"database_url": "postgresql://localhost/galaxy_frog"})


@pytest.mark.parametrize(
    "value",
    [
        "localhost:11434",
        "ftp://localhost:11434",
        "http://localhost:11434/api",
        "http://localhost:11434?model=bge-m3",
        "http://localhost:11434/#fragment",
    ],
)
def test_settings_reject_invalid_ollama_origins(value: str) -> None:
    with pytest.raises(ValidationError, match="OLLAMA_BASE_URL"):
        Settings.model_validate({"ollama_base_url": value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ingestion_worker_id", " "),
        ("ingestion_lease_seconds", 29),
        ("ingestion_poll_seconds", 0),
    ],
)
def test_settings_reject_invalid_worker_configuration(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({field: value})
