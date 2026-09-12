"""Behavior coverage for bounded audio acquisition values."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from galaxy_frog.domain.media import (
    AcquiredAudio,
    AudioAcquisitionError,
    AudioAcquisitionErrorCode,
    AudioAcquisitionLimits,
    AudioAcquisitionRequest,
    AudioAsset,
    AudioFallbackReason,
)
from galaxy_frog.domain.videos.models import SourceReference, VideoSourceKind

REFERENCE = SourceReference(
    VideoSourceKind.YOUTUBE,
    "dQw4w9WgXcQ",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
)
ABSOLUTE_TEST_MEDIA_PATH = Path(__file__).resolve().parent / "audio.wav"


def request() -> AudioAcquisitionRequest:
    return AudioAcquisitionRequest(
        job_id=uuid4(),
        attempt=1,
        source=REFERENCE,
        expected_duration_ms=12_345,
        fallback_reason=AudioFallbackReason.CAPTIONS_UNAVAILABLE,
    )


def artifact() -> AcquiredAudio:
    audio_request = request()
    return AcquiredAudio(
        job_id=audio_request.job_id,
        attempt=audio_request.attempt,
        source=audio_request.source,
        fallback_reason=audio_request.fallback_reason,
        path=ABSOLUTE_TEST_MEDIA_PATH,
        start_ms=0,
        end_ms=12_345,
        size_bytes=395_084,
        media_type="audio/wav",
        codec="pcm_s16le",
        sample_rate_hz=16_000,
        channels=1,
        downloader="yt-dlp",
        downloader_revision="2026.08.19",
        normalizer="ffmpeg",
        normalizer_revision="9.0.1",
        acquired_at=datetime(2026, 9, 7, 12, tzinfo=UTC),
    )


def test_audio_values_preserve_fallback_interval_and_tool_provenance() -> None:
    audio = artifact()

    assert audio.fallback_reason is AudioFallbackReason.CAPTIONS_UNAVAILABLE
    assert (audio.start_ms, audio.end_ms) == (0, 12_345)
    assert audio.source == REFERENCE
    assert audio.downloader_revision == "2026.08.19"
    assert audio.normalizer_revision == "9.0.1"


def test_audio_asset_preserves_availability_and_deletion_time() -> None:
    audio = artifact()
    retained = AudioAsset(uuid4(), audio)
    deleted_at = datetime(2026, 9, 7, 12, 1, tzinfo=UTC)

    assert retained.is_available is True
    assert replace(retained, deleted_at=deleted_at).is_available is False

    with pytest.raises(ValueError, match="timezone-aware"):
        replace(retained, deleted_at=datetime(2026, 9, 7, 12, 1))
    with pytest.raises(ValueError, match="must not precede"):
        replace(retained, deleted_at=datetime(2026, 9, 7, 11, 59, tzinfo=UTC))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_duration_ms", 0),
        ("max_download_bytes", 0),
        ("max_output_bytes", 0),
        ("timeout_seconds", 0),
        ("max_concurrency", 0),
    ],
)
def test_limits_require_positive_resource_ceilings(field: str, value: object) -> None:
    values: dict[str, object] = {
        "max_duration_ms": 7_200_000,
        "max_download_bytes": 268_435_456,
        "max_output_bytes": 268_435_456,
        "timeout_seconds": 600.0,
        "max_concurrency": 1,
    }
    values[field] = value

    with pytest.raises(ValueError, match=field):
        AudioAcquisitionLimits(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("attempt", 0, "attempt"),
        ("expected_duration_ms", 0, "expected_duration_ms"),
        ("fallback_reason", "other", "approved caption outcome"),
    ],
)
def test_request_rejects_invalid_authorization_values(
    field: str,
    value: object,
    message: str,
) -> None:
    values: dict[str, object] = {
        "job_id": uuid4(),
        "attempt": 1,
        "source": REFERENCE,
        "expected_duration_ms": 1000,
        "fallback_reason": AudioFallbackReason.CAPTIONS_UNUSABLE,
    }
    values[field] = value

    with pytest.raises(ValueError, match=message):
        AudioAcquisitionRequest(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("attempt", 0, "attempt"),
        ("fallback_reason", cast(AudioFallbackReason, "other"), "approved caption outcome"),
        ("path", Path("relative.wav"), "absolute"),
        ("start_ms", 1, "begin at zero"),
        ("end_ms", 0, "greater"),
        ("size_bytes", 0, "size_bytes"),
        ("sample_rate_hz", 0, "sample_rate_hz"),
        ("channels", 0, "channels"),
        ("media_type", " ", "media_type"),
        ("codec", "", "codec"),
        ("downloader", "", "downloader"),
        ("downloader_revision", "", "downloader_revision"),
        ("normalizer", "", "normalizer"),
        ("normalizer_revision", "", "normalizer_revision"),
        ("acquired_at", datetime(2026, 9, 7), "timezone-aware"),
    ],
)
def test_artifact_rejects_invalid_provenance(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(artifact(), **{field: value})


def test_audio_error_exposes_only_stable_safe_metadata() -> None:
    error = AudioAcquisitionError(
        AudioAcquisitionErrorCode.DOWNLOAD_FAILED,
        "The source audio could not be downloaded.",
        retryable=True,
    )

    assert str(error) == "The source audio could not be downloaded."
    assert error.code is AudioAcquisitionErrorCode.DOWNLOAD_FAILED
    assert error.message == str(error)
    assert error.retryable is True
