"""Embedding compatibility and transcript retrieval contracts."""

from galaxy_frog.domain.retrieval.models import (
    EmbeddingCollectionSpec,
    RetrievedEvidence,
)
from galaxy_frog.domain.retrieval.multimodal import (
    EvidenceModality,
    MultimodalEvidence,
    QueryModality,
    QueryRoutingAnalysis,
    analyze_multimodal_query,
    fuse_multimodal_rankings,
)
from galaxy_frog.domain.retrieval.ports import (
    OCRRetriever,
    TextEmbeddingProvider,
    TranscriptRetriever,
    TranscriptSearch,
)

__all__ = [
    "EmbeddingCollectionSpec",
    "EvidenceModality",
    "MultimodalEvidence",
    "OCRRetriever",
    "QueryModality",
    "QueryRoutingAnalysis",
    "RetrievedEvidence",
    "TextEmbeddingProvider",
    "TranscriptRetriever",
    "TranscriptSearch",
    "analyze_multimodal_query",
    "fuse_multimodal_rankings",
]
