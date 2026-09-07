"""Behavior tests for typed application settings."""

from pathlib import Path

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
    monkeypatch.setenv("MEDIA_WORKSPACE_ROOT", "tmp/test-media")
    monkeypatch.setenv("MEDIA_MAX_DURATION_SECONDS", "3600")
    monkeypatch.setenv("MEDIA_MAX_DOWNLOAD_BYTES", "1024")
    monkeypatch.setenv("MEDIA_MAX_OUTPUT_BYTES", "2048")
    monkeypatch.setenv("MEDIA_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("MEDIA_MAX_CONCURRENCY", "2")
    monkeypatch.setenv("MEDIA_RETAIN_ON_SUCCESS", "true")
    monkeypatch.setenv("FFMPEG_EXECUTABLE", " C:/Tools/ffmpeg.exe ")
    monkeypatch.setenv("FFPROBE_EXECUTABLE", " C:/Tools/ffprobe.exe ")
    monkeypatch.setenv("ASR_MODEL", " small ")
    monkeypatch.setenv("ASR_MODEL_REVISION", " Systran/faster-whisper-small ")
    monkeypatch.setenv("ASR_DEVICE", "cpu")
    monkeypatch.setenv("ASR_COMPUTE_TYPE", "int8")
    monkeypatch.setenv("ASR_MAX_CONCURRENCY", "1")

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
    assert settings.media_workspace_root.is_absolute()
    assert settings.media_workspace_root.parts[-2:] == ("tmp", "test-media")
    assert settings.media_max_duration_seconds == 3600
    assert settings.media_max_download_bytes == 1024
    assert settings.media_max_output_bytes == 2048
    assert settings.media_timeout_seconds == 45
    assert settings.media_max_concurrency == 2
    assert settings.media_retain_on_success is True
    assert settings.ffmpeg_executable == "C:/Tools/ffmpeg.exe"
    assert settings.ffprobe_executable == "C:/Tools/ffprobe.exe"
    assert settings.asr_provider == "faster-whisper"
    assert settings.asr_model == "small"
    assert settings.asr_model_revision == "Systran/faster-whisper-small"
    assert settings.asr_device == "cpu"
    assert settings.asr_compute_type == "int8"
    assert settings.asr_max_concurrency == 1


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("media_max_duration_seconds", 0),
        ("media_max_download_bytes", 0),
        ("media_max_output_bytes", 0),
        ("media_timeout_seconds", 0),
        ("media_max_concurrency", 0),
        ("media_max_concurrency", 5),
        ("ffmpeg_executable", " "),
        ("ffprobe_executable", ""),
    ],
)
def test_settings_reject_invalid_media_configuration(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({field: value})


def test_settings_reject_a_filesystem_root_as_the_media_workspace() -> None:
    with pytest.raises(ValidationError, match="MEDIA_WORKSPACE_ROOT"):
        Settings.model_validate({"media_workspace_root": Path.cwd().anchor})


def test_asr_defaults_select_multilingual_small_on_the_gpu() -> None:
    settings = Settings.model_validate({})

    assert settings.asr_provider == "faster-whisper"
    assert settings.asr_model == "small"
    assert not settings.asr_model.endswith(".en")
    assert settings.asr_device == "cuda"
    assert settings.asr_compute_type == "int8_float16"
    assert settings.asr_max_concurrency == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("asr_provider", "cloud"),
        ("asr_model", " "),
        ("asr_model_revision", ""),
        ("asr_device", "gpu"),
        ("asr_compute_type", "auto"),
        ("asr_max_concurrency", 0),
        ("asr_max_concurrency", 5),
    ],
)
def test_settings_reject_invalid_asr_configuration(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({field: value})


@pytest.mark.parametrize("compute_type", ["float16", "int8_float16"])
def test_settings_reject_float16_compute_on_cpu(compute_type: str) -> None:
    with pytest.raises(ValidationError, match=r"CPU ASR.*int8 or float32"):
        Settings.model_validate({"asr_device": "cpu", "asr_compute_type": compute_type})


def test_qstash_dispatch_requires_complete_secret_configuration() -> None:
    with pytest.raises(ValidationError, match=r"QSTASH_TOKEN.*QSTASH_CALLBACK_URL"):
        Settings.model_validate({"job_dispatcher": "qstash"})

    settings = Settings.model_validate(
        {
            "job_dispatcher": "qstash",
            "qstash_token": "token",
            "qstash_callback_url": "https://frog.example/internal/qstash/dispatch/",
            "qstash_current_signing_key": "current",
            "qstash_next_signing_key": "next",
        }
    )

    assert settings.qstash_callback_url == "https://frog.example/internal/qstash/dispatch"
    assert settings.qstash_token is not None
    assert settings.qstash_token.get_secret_value() == "token"


@pytest.mark.parametrize(
    "value",
    [
        "frog.example/internal/qstash/dispatch",
        "ftp://frog.example/dispatch",
        "https://frog.example/dispatch?x=1",
    ],
)
def test_settings_reject_invalid_qstash_callback_urls(value: str) -> None:
    with pytest.raises(ValidationError, match="QSTASH_CALLBACK_URL"):
        Settings.model_validate({"qstash_callback_url": value})
