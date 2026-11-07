"""Visual retrieval application ports, constrained before top-k."""

from typing import Protocol
from uuid import UUID

from galaxy_frog.domain.retrieval.temporal import TimeWindow
from galaxy_frog.domain.vision.visual import VisualFrame, VisualHit


class VisualSearch(Protocol):
    async def search(
        self, video_id: UUID, query: str, *, limit: int = 12, window: TimeWindow | None = None
    ) -> tuple[VisualHit, ...]: ...

    async def nearby(
        self, video_id: UUID, seeds: tuple[VisualHit, ...], *, window: TimeWindow | None = None
    ) -> tuple[VisualFrame, ...]: ...


class VisualIndexer(Protocol):
    async def ensure_indexed(self, video_id: UUID) -> UUID: ...
