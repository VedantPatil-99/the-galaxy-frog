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

    settings = Settings()

    assert settings.app_env == "test"
    assert settings.database_url is not None
    assert settings.database_url.get_secret_value().startswith("postgresql+asyncpg://")


def test_settings_reject_an_unknown_environment() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"app_env": "preview"})


def test_settings_reject_a_non_async_database_url() -> None:
    with pytest.raises(ValidationError, match="postgresql\\+asyncpg"):
        Settings.model_validate({"database_url": "postgresql://localhost/galaxy_frog"})
