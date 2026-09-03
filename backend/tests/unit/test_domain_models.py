"""Validation coverage for transcript and retrieval invariants."""

from math import nan
from uuid import uuid4

import pytest

from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec, RetrievedEvidence
from galaxy_frog.domain.transcripts.models import RetrievalUnit, TranscriptCue
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
