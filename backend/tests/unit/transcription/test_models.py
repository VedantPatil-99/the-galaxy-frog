"""Behavior coverage for provider-independent transcription values."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from galaxy_frog.domain.media import AcquiredAudio, AudioFallbackReason
from galaxy_frog.domain.transcription import (
    TranscriptionComputeType,
    TranscriptionCue,
    TranscriptionDevice,
    TranscriptionError,
    TranscriptionErrorCode,
    TranscriptionProvider,
    TranscriptionProviderSpec,
    TranscriptionRequest,
    TranscriptionResult,
)
from galaxy_frog.domain.videos.models import SourceReference, VideoSourceKind

SOURCE = SourceReference(
    VideoSourceKind.YOUTUBE,
    "dQw4w9WgXcQ",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
)


def audio() -> AcquiredAudio:
    return AcquiredAudio(
        job_id=uuid4(),
        attempt=2,
        source=SOURCE,
        fallback_reason=AudioFallbackReason.CAPTIONS_UNAVAILABLE,
        path=Path("C:/tmp/audio.wav"),
        start_ms=0,
        end_ms=4000,
        size_bytes=128_078,
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


def spec() -> TranscriptionProviderSpec:
    return TranscriptionProviderSpec(
        provider="faster-whisper",
        provider_revision="1.2.1",
        model="small",
        model_revision="Systran/faster-whisper-small",
        device=TranscriptionDevice.CUDA,
        compute_type=TranscriptionComputeType.INT8_FLOAT16,
    )


def cue(
    source_order: int = 0,
    start_ms: int = 0,
    end_ms: int = 2000,
) -> TranscriptionCue:
    return TranscriptionCue(
        source_order=source_order,
        start_ms=start_ms,
        end_ms=end_ms,
        text="Namaste, welcome to Galaxy Frog.",
        confidence=0.91,
        confidence_method="mean_word_probability",
    )


def result() -> TranscriptionResult:
    return TranscriptionResult(
        job_id=uuid4(),
        attempt=2,
        source=SOURCE,
        fallback_reason=AudioFallbackReason.CAPTIONS_UNAVAILABLE,
        audio_start_ms=0,
        audio_end_ms=4000,
        spec=spec(),
        language_code="hi",
        language_confidence=0.88,
        language_confidence_method="provider_language_probability",
        cues=(cue(), cue(1, 2000, 4000)),
        processing_seconds=1.25,
        transcribed_at=datetime(2026, 9, 7, 12, 1, tzinfo=UTC),
    )


def test_request_copies_complete_audio_lineage_and_normalizes_language() -> None:
    acquired = audio()

    request = TranscriptionRequest.from_acquired_audio(acquired, language_code=" HI ")

    assert request.audio_job_id == acquired.job_id
    assert request.attempt == acquired.attempt
    assert request.source == acquired.source
    assert request.fallback_reason is AudioFallbackReason.CAPTIONS_UNAVAILABLE
    assert request.audio_path == acquired.path
    assert (request.audio_start_ms, request.audio_end_ms) == (0, 4000)
    assert request.language_code == "hi"


def test_request_allows_provider_language_detection() -> None:
    request = TranscriptionRequest.from_acquired_audio(audio())

    assert request.language_code is None


def test_result_preserves_temporal_model_language_and_confidence_provenance() -> None:
    transcription = result()

    assert transcription.spec.provider == "faster-whisper"
    assert transcription.spec.model == "small"
    assert transcription.spec.device is TranscriptionDevice.CUDA
    assert transcription.spec.compute_type is TranscriptionComputeType.INT8_FLOAT16
    assert transcription.language_code == "hi"
    assert transcription.language_confidence == 0.88
    assert [(item.start_ms, item.end_ms) for item in transcription.cues] == [
        (0, 2000),
        (2000, 4000),
    ]


@pytest.mark.parametrize("field", ["provider", "provider_revision", "model", "model_revision"])
def test_provider_spec_requires_complete_identity(field: str) -> None:
    with pytest.raises(ValueError, match=field):
        replace(spec(), **{field: " "})


def test_provider_spec_requires_declared_device_and_compute_enums() -> None:
    with pytest.raises(ValueError, match="supported transcription device"):
        replace(spec(), device=cast(TranscriptionDevice, "gpu"))

    with pytest.raises(ValueError, match="supported transcription compute type"):
        replace(spec(), compute_type=cast(TranscriptionComputeType, "auto"))


@pytest.mark.parametrize(
    "compute_type",
    [TranscriptionComputeType.FLOAT16, TranscriptionComputeType.INT8_FLOAT16],
)
def test_provider_spec_rejects_float16_compute_on_cpu(
    compute_type: TranscriptionComputeType,
) -> None:
    with pytest.raises(ValueError, match=r"CPU.*float16"):
        replace(spec(), device=TranscriptionDevice.CPU, compute_type=compute_type)


def test_provider_spec_accepts_explicit_cpu_int8() -> None:
    cpu = replace(
        spec(),
        device=TranscriptionDevice.CPU,
        compute_type=TranscriptionComputeType.INT8,
    )

    assert cpu.device is TranscriptionDevice.CPU
    assert cpu.compute_type is TranscriptionComputeType.INT8


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("attempt", 0, "attempt"),
        ("fallback_reason", "other", "approved caption outcome"),
        ("audio_path", Path("relative.wav"), "absolute"),
        ("audio_start_ms", 1, "whole-source"),
        ("audio_end_ms", 0, "whole-source"),
        ("language_code", " ", "language_code"),
    ],
)
def test_request_rejects_invalid_audio_authorization(
    field: str,
    value: object,
    message: str,
) -> None:
    request = TranscriptionRequest.from_acquired_audio(audio(), language_code="en")

    with pytest.raises(ValueError, match=message):
        replace(request, **{field: value})


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source_order", -1, "source_order"),
        ("start_ms", -1, "interval"),
        ("end_ms", 0, "interval"),
        ("text", " ", "text"),
        ("confidence", float("nan"), "finite"),
        ("confidence", -0.1, "between"),
        ("confidence", 1.1, "between"),
        ("confidence_method", None, "required"),
    ],
)
def test_cue_rejects_invalid_evidence(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(cue(), **{field: value})


def test_confidence_method_cannot_exist_without_a_score() -> None:
    without_confidence = replace(cue(), confidence=None, confidence_method=None)
    assert without_confidence.confidence is None

    with pytest.raises(ValueError, match="requires confidence"):
        replace(cue(), confidence=None)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("attempt", 0, "attempt"),
        ("fallback_reason", "other", "approved caption outcome"),
        ("audio_start_ms", 1, "whole-source"),
        ("audio_end_ms", 0, "whole-source"),
        ("spec", "faster-whisper", "spec must identify"),
        ("language_code", " ", "language_code"),
        ("language_confidence", float("inf"), "finite"),
        ("language_confidence_method", None, "required"),
        ("cues", (), "at least one cue"),
        ("processing_seconds", -1, "processing_seconds"),
        ("processing_seconds", float("nan"), "processing_seconds"),
        ("transcribed_at", datetime(2026, 9, 7), "timezone-aware"),
    ],
)
def test_result_rejects_invalid_provenance(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(result(), **{field: value})


def test_result_rejects_language_confidence_method_without_score() -> None:
    without_confidence = replace(
        result(),
        language_confidence=None,
        language_confidence_method=None,
    )
    assert without_confidence.language_confidence is None

    with pytest.raises(ValueError, match="requires language_confidence"):
        replace(result(), language_confidence=None)


def test_result_requires_contiguous_chronological_bounded_cues() -> None:
    with pytest.raises(ValueError, match="contiguous"):
        replace(result(), cues=(cue(1),))

    with pytest.raises(ValueError, match="ordered by start"):
        replace(result(), cues=(cue(0, 1000, 2000), cue(1, 0, 1000)))

    with pytest.raises(ValueError, match="within the source audio"):
        replace(result(), cues=(cue(0, 0, 4001),))


def test_transcription_error_exposes_only_stable_safe_metadata() -> None:
    error = TranscriptionError(
        TranscriptionErrorCode.DEVICE_UNAVAILABLE,
        "The configured transcription device is unavailable.",
        retryable=True,
    )

    assert str(error) == "The configured transcription device is unavailable."
    assert error.code is TranscriptionErrorCode.DEVICE_UNAVAILABLE
    assert error.message == str(error)
    assert error.retryable is True


def test_transcription_provider_protocol_is_runtime_checkable() -> None:
    class FakeProvider:
        spec = spec()

        async def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
            del request
            return result()

    assert isinstance(FakeProvider(), TranscriptionProvider)
