"""Opt-in integration coverage for the installed local faster-whisper runtime."""

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest

from galaxy_frog.adapters.transcription import FasterWhisperTranscriptionProvider
from galaxy_frog.config import Settings
from galaxy_frog.domain.media import AudioFallbackReason
from galaxy_frog.domain.transcription import (
    TranscriptionComputeType,
    TranscriptionDevice,
    TranscriptionRequest,
)
from galaxy_frog.domain.videos.models import SourceReference, VideoSourceKind

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_ASR_INTEGRATION") != "1",
    reason="set RUN_ASR_INTEGRATION=1 with a local speech fixture and ASR runtime configured",
)


@pytest.mark.asyncio
async def test_installed_faster_whisper_preserves_timestamped_speech() -> None:
    audio_value = os.getenv("ASR_INTEGRATION_AUDIO_PATH")
    duration_value = os.getenv("ASR_INTEGRATION_AUDIO_DURATION_MS")
    if audio_value is None or duration_value is None:
        pytest.fail("ASR_INTEGRATION_AUDIO_PATH and ASR_INTEGRATION_AUDIO_DURATION_MS are required")
    audio_path = await asyncio.to_thread(Path(audio_value).resolve)
    duration_ms = int(duration_value)
    settings = Settings()
    provider = FasterWhisperTranscriptionProvider(
        model=settings.asr_model,
        model_revision=settings.asr_model_revision,
        device=TranscriptionDevice(settings.asr_device),
        compute_type=TranscriptionComputeType(settings.asr_compute_type),
        max_concurrency=settings.asr_max_concurrency,
    )
    request = TranscriptionRequest(
        audio_job_id=uuid4(),
        attempt=1,
        source=SourceReference(
            VideoSourceKind.LOCAL_FILE,
            audio_path.name,
            audio_path.as_uri(),
        ),
        fallback_reason=AudioFallbackReason.CAPTIONS_UNAVAILABLE,
        audio_path=audio_path,
        audio_start_ms=0,
        audio_end_ms=duration_ms,
        language_code=None,
    )

    result = await provider.transcribe(request)

    text = " ".join(cue.text for cue in result.cues).casefold()
    assert "fellow americans" in text
    assert result.language_code == "en"
    assert result.spec.device.value == settings.asr_device
    assert result.spec.compute_type.value == settings.asr_compute_type
    assert result.spec.model_revision == settings.asr_model_revision
    assert all(0 <= cue.start_ms < cue.end_ms <= duration_ms for cue in result.cues)
    assert [cue.source_order for cue in result.cues] == list(range(len(result.cues)))
    print(
        f"ASR probe: {result.spec.device.value}/{result.spec.compute_type.value}, "
        f"{len(result.cues)} cues, {result.processing_seconds:.2f}s, {result.language_code}"
    )
