"""Behavior tests for typed application settings."""

import pytest
from pydantic import ValidationError

from galaxy_frog.config import Settings


def test_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")

    assert Settings().app_env == "test"


def test_settings_reject_an_unknown_environment() -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"app_env": "preview"})
