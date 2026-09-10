"""Validation coverage for transcript and retrieval invariants."""

from math import nan
from uuid import uuid4

import pytest

from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec, RetrievedEvidence
from galaxy_frog.domain.transcription import TranscriptionCue, TranscriptionResult
from galaxy_frog.domain.transcripts.models import RetrievalUnit, TranscriptCue, TranscriptOrigin
from galaxy_frog.domain.videos.models import CaptionKind, SourceReference, VideoSourceKind


def source() -> SourceReference:
    return SourceReference(
        VideoSourceKind.YOUTUBE,
        "dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    )


def unit() -> RetrievalUnit:
    return RetrievalUnit("a" * 64, 0, 1000, "Evidence.", ("b" * 64,))


@pytest.mark.parametrize("field", ["provider", "model", "revision", "normalization"])
def test_embedding_collection_requires_complete_identity(field: str) -> None:
    values: dict[str, object] = {
        "provider": "provider",
        "model": "model",
        "revision": "revision",
        "dimension": 1024,
        "normalization": "l2",
    }
    values[field] = " "
    with pytest.raises(ValueError, match="metadata"):
        EmbeddingCollectionSpec(**values)  # type: ignore[arg-type]


def test_embedding_collection_requires_positive_dimension() -> None:
    with pytest.raises(ValueError, match="dimension"):
        EmbeddingCollectionSpec("provider", "model", "revision", 0, "l2")


@pytest.mark.parametrize("score", [nan, -0.1, 1.1])
def test_retrieved_evidence_rejects_invalid_scores(score: float) -> None:
    with pytest.raises(ValueError, match="score"):
        RetrievedEvidence(uuid4(), unit(), score)


@pytest.mark.parametrize(
    "arguments",
    [
        {"cue_id": "short"},
        {"source_order": -1},
        {"start_ms": -1},
        {"start_ms": 1, "end_ms": 1},
        {"text": ""},
    ],
)
def test_transcript_cue_rejects_broken_provenance(arguments: dict[str, object]) -> None:
    values: dict[str, object] = {
        "cue_id": "b" * 64,
        "source": source(),
        "track_id": "manual:en",
        "language_code": "en",
        "caption_kind": CaptionKind.MANUAL,
        "source_order": 0,
        "start_ms": 0,
        "end_ms": 1000,
        "text": "Evidence.",
        "origin": TranscriptOrigin.CAPTION,
    }
    values.update(arguments)
    with pytest.raises(ValueError):
        TranscriptCue(**values)  # type: ignore[arg-type]


def test_transcript_cue_normalizes_asr_evidence_without_claiming_caption_provenance() -> None:
    from datetime import UTC, datetime

    from galaxy_frog.domain.media import AudioFallbackReason
    from galaxy_frog.domain.transcription import (
        TranscriptionComputeType,
        TranscriptionDevice,
        TranscriptionProviderSpec,
    )

    run_id = uuid4()
    provider_cue = TranscriptionCue(0, 100, 900, "Spoken evidence.", 0.9, "word_mean")
    result = TranscriptionResult(
        job_id=uuid4(),
        attempt=1,
        source=source(),
        fallback_reason=AudioFallbackReason.CAPTIONS_UNAVAILABLE,
        audio_start_ms=0,
        audio_end_ms=1000,
        spec=TranscriptionProviderSpec(
            "faster-whisper",
            "1.2.1",
            "small",
            "model-revision",
            TranscriptionDevice.CUDA,
            TranscriptionComputeType.INT8_FLOAT16,
        ),
        language_code="hi",
        language_confidence=0.8,
        language_confidence_method="provider_probability",
        cues=(provider_cue,),
        processing_seconds=1.0,
        transcribed_at=datetime(2026, 9, 10, tzinfo=UTC),
    )

    cue = TranscriptCue.from_transcription(run_id=run_id, result=result, cue=provider_cue)

    assert cue.origin is TranscriptOrigin.ASR
    assert cue.transcription_run_id == run_id
    assert cue.track_id is None
    assert cue.caption_kind is None
    assert cue.confidence == 0.9


@pytest.mark.parametrize(
    "arguments",
    [
        {"origin": TranscriptOrigin.CAPTION, "track_id": None},
        {"origin": TranscriptOrigin.CAPTION, "caption_kind": None},
        {"origin": TranscriptOrigin.CAPTION, "transcription_run_id": uuid4()},
        {
            "origin": TranscriptOrigin.ASR,
            "track_id": None,
            "caption_kind": None,
            "transcription_run_id": None,
        },
        {"origin": TranscriptOrigin.ASR, "transcription_run_id": uuid4()},
        {"confidence": float("nan")},
        {"confidence": 0.5, "confidence_method": None},
        {"confidence": None, "confidence_method": "unknown"},
    ],
)
def test_transcript_cue_rejects_mixed_or_invalid_origin_provenance(
    arguments: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "cue_id": "b" * 64,
        "source": source(),
        "track_id": "manual:en",
        "language_code": "en",
        "caption_kind": CaptionKind.MANUAL,
        "source_order": 0,
        "start_ms": 0,
        "end_ms": 1000,
        "text": "Evidence.",
        "origin": TranscriptOrigin.CAPTION,
    }
    values.update(arguments)

    with pytest.raises(ValueError):
        TranscriptCue(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "arguments",
    [
        {"unit_id": "short"},
        {"start_ms": -1},
        {"start_ms": 1, "end_ms": 1},
        {"text": ""},
        {"cue_ids": ()},
    ],
)
def test_retrieval_unit_rejects_broken_provenance(arguments: dict[str, object]) -> None:
    values: dict[str, object] = {
        "unit_id": "a" * 64,
        "start_ms": 0,
        "end_ms": 1000,
        "text": "Evidence.",
        "cue_ids": ("b" * 64,),
    }
    values.update(arguments)
    with pytest.raises(ValueError):
        RetrievalUnit(**values)  # type: ignore[arg-type]
