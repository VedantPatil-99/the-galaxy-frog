"""Credential isolation for independent Gemini service projects."""

from unittest.mock import Mock

import pytest
from pydantic import SecretStr, ValidationError

from galaxy_frog.api import app as app_module
from galaxy_frog.application.provider_registry import provider_catalog
from galaxy_frog.config import Settings


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg, arg-type]


def test_study_does_not_borrow_the_interactive_project() -> None:
    config = settings(gemini_api_key=SecretStr("interactive"), gemini_project_id="frog-interactive")
    assert config.gemini_credentials("study") == (None, None)
    assert config.gemini_credentials("generation") == (SecretStr("interactive"), "frog-interactive")
    assert config.gemini_credentials("vision") == config.gemini_credentials("generation")


def test_independent_study_and_visual_credentials() -> None:
    config = settings(
        gemini_api_key=SecretStr("interactive"),
        gemini_project_id="frog-interactive",
        gemini_study_api_key=SecretStr("private-study-secret"),
        gemini_study_project_id="frog-background",
        gemini_visual_api_key=SecretStr("visual"),
        gemini_visual_project_id="frog-visual",
    )
    assert config.gemini_credentials("study") == (
        SecretStr("private-study-secret"),
        "frog-background",
    )
    assert config.gemini_credentials("vision") == (SecretStr("visual"), "frog-visual")
    assert config.gemini_credentials("generation") == (SecretStr("interactive"), "frog-interactive")
    assert "private-study-secret" not in repr(config)


@pytest.mark.parametrize(
    "field", ["gemini_api_key", "gemini_study_api_key", "gemini_visual_api_key"]
)
def test_blank_credentials_are_not_configured(field: str) -> None:
    config = settings(**{field: SecretStr("  ")})
    assert getattr(config, field) is None


@pytest.mark.parametrize(
    "values",
    [
        {"gemini_visual_api_key": SecretStr("private-key")},
        {"gemini_visual_project_id": "frog-visual"},
    ],
)
def test_incomplete_visual_override_does_not_fall_back(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="require each other") as caught:
        settings(**values)
    assert "private-key" not in str(caught.value)


@pytest.mark.parametrize("project", ["BAD-project", "short", "project/with/path", "a" * 31])
def test_project_ids_are_validated(project: str) -> None:
    with pytest.raises(ValidationError, match="project IDs"):
        settings(gemini_project_id=project)


def test_empty_override_and_whitespace_normalization() -> None:
    config = settings(
        gemini_api_key=SecretStr(" interactive "),
        gemini_project_id=" frog-interactive ",
        gemini_visual_api_key=SecretStr(" "),
        gemini_visual_project_id=" ",
    )
    assert config.gemini_credentials("vision") == (SecretStr("interactive"), "frog-interactive")


def test_visual_readiness_uses_the_override_without_an_interactive_key() -> None:
    config = settings(
        vlm_provider="gemini",
        external_frame_processing_enabled=True,
        gemini_visual_api_key=SecretStr("visual-only"),
        gemini_visual_project_id="frog-visual",
        gemini_visual_model="gemini-test",
        gemini_visual_revision="pinned",
    )
    entry = next(item for item in provider_catalog(config) if item.provider_id == "gemini-vision")
    assert entry.readiness == "configured"
    assert "visual-only" not in entry.model_dump_json()


def test_app_constructs_vision_with_the_selected_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = Mock()
    monkeypatch.setattr(app_module, "GeminiVisualReasoner", factory)
    app_module.create_app(
        settings(
            vlm_provider="gemini",
            gemini_api_key=SecretStr("interactive"),
            gemini_visual_api_key=SecretStr("visual-only"),
            gemini_visual_project_id="frog-visual",
            gemini_visual_model="gemini-test",
            gemini_visual_revision="pinned",
        )
    )
    assert factory.call_args.kwargs["api_key"] == "visual-only"
