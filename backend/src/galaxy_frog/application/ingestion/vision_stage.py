"""Optional durable vision checkpoint; transcript success survives visual-provider loss."""

from dataclasses import dataclass, replace
from typing import Protocol
from uuid import UUID

from galaxy_frog.application.ingestion.runner import (
    IngestionStageHandler,
    StageContext,
    StageResult,
)
from galaxy_frog.application.videos.ports import VideoRepository
from galaxy_frog.domain.ingestion.models import IngestionJob, IngestionStage


@dataclass(frozen=True, slots=True)
class VisionIndexResult:
    frame_count: int
    collection_id: UUID | None
    warnings: tuple[str, ...]


class VisionIndex(Protocol):
    async def execute(self, video_id: UUID, job_id: UUID) -> VisionIndexResult: ...


class ContinueToVision:
    stage = IngestionStage.EMBEDDING

    def __init__(self, handler: IngestionStageHandler) -> None:
        self._handler = handler

    async def execute(self, job: IngestionJob, context: StageContext) -> StageResult:
        result = await self._handler.execute(job, context)
        return replace(result, next_stage=IngestionStage.INDEXING)


class VisionIndexingStage:
    stage = IngestionStage.INDEXING

    def __init__(self, videos: VideoRepository, index: VisionIndex) -> None:
        self._videos = videos
        self._index = index

    async def execute(self, job: IngestionJob, context: StageContext) -> StageResult:
        video = await self._videos.find_by_source(job.source)
        if video is None:
            from galaxy_frog.application.ingestion.runner import IngestionStageError

            raise IngestionStageError(
                "PERSISTED_VIDEO_NOT_FOUND",
                "The video is unavailable for visual indexing.",
                retryable=True,
            )
        result = await context.run_with_heartbeats(self._index.execute(video.video_id, job.job_id))
        return StageResult(
            IngestionStage.CLEANUP,
            {
                "video_id": str(video.video_id),
                "visual_frame_count": result.frame_count,
                "visual_collection_id": str(result.collection_id) if result.collection_id else None,
                "visual_warnings": list(result.warnings),
                "visual_degraded": bool(result.warnings),
            },
        )
