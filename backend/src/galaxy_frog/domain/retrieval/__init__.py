"""Embedding compatibility and transcript retrieval contracts."""

from galaxy_frog.domain.retrieval.models import (
    EmbeddingCollectionSpec,
    RetrievedEvidence,
)
from galaxy_frog.domain.retrieval.ports import TextEmbeddingProvider, TranscriptSearch

__all__ = [
    "EmbeddingCollectionSpec",
    "RetrievedEvidence",
    "TextEmbeddingProvider",
    "TranscriptSearch",
]
