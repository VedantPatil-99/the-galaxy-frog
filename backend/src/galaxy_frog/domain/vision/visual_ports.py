"""Domain-facing visual provider ports with explicit identity and failure contracts."""

from typing import Protocol

from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec
from galaxy_frog.domain.vision.visual import (
    VisualEmbedding,
    VisualFrame,
    VisualProviderIdentity,
    VisualReasoningRequest,
    VisualReasoningResult,
)


class VisualEmbeddingProvider(Protocol):
    identity: VisualProviderIdentity
    collection: EmbeddingCollectionSpec

    async def embed_frames(
        self, frames: tuple[VisualFrame, ...]
    ) -> tuple[VisualEmbedding, ...]: ...

    async def embed_query(self, query: str) -> tuple[float, ...]: ...


class VisualReasoningProvider(Protocol):
    identity: VisualProviderIdentity

    async def reason(self, request: VisualReasoningRequest) -> VisualReasoningResult: ...
