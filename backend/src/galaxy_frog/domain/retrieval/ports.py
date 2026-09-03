"""Provider and persistence ports for dense transcript retrieval."""

from typing import Protocol
from uuid import UUID

from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec, RetrievedEvidence


class TextEmbeddingProvider(Protocol):
    """Generate vectors in one declared embedding space."""

    spec: EmbeddingCollectionSpec

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...


class TranscriptSearch(Protocol):
    """Index and query transcript units without mixing collections."""

    async def ensure_indexed(self, video_id: UUID) -> UUID: ...

    async def search(
        self, video_id: UUID, query: str, *, limit: int
    ) -> tuple[RetrievedEvidence, ...]: ...
