"""Compose existing scene/OCR persistence with visual indexing in the durable worker."""

from pathlib import Path
from uuid import UUID

from galaxy_frog.adapters.vision.frame_pipeline import LocalFramePipeline
from galaxy_frog.application.ingestion.vision_stage import VisionIndexResult
from galaxy_frog.application.retrieval.visual_ports import VisualIndexer
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.db.vision_repository import SqlAlchemyVisionRepository
from galaxy_frog.domain.vision.errors import VisionProviderError
from galaxy_frog.domain.vision.models import (
    FrameSample,
    FrameSource,
    OCRExtractionRequest,
    OCRExtractionResult,
)
from galaxy_frog.domain.vision.ports import OCRProvider
from galaxy_frog.domain.vision.visual import VisualProviderError


class PersistVisionIndex:
    def __init__(
        self,
        *,
        videos: SqlAlchemyVideoRepository,
        vision: SqlAlchemyVisionRepository,
        pipeline: LocalFramePipeline,
        visual: VisualIndexer | None = None,
        ocr: OCRProvider | None = None,
        ocr_permitted: bool = False,
    ) -> None:
        self._videos, self._vision, self._pipeline = videos, vision, pipeline
        self._visual, self._ocr, self._ocr_permitted = visual, ocr, ocr_permitted

    async def execute(self, video_id: UUID, job_id: UUID) -> VisionIndexResult:
        video = await self._videos.get_video(video_id)
        if video is None:
            raise ValueError("vision index requires a persisted video")
        warnings: list[str] = []
        try:
            frames = await self._vision.get_frames(video_id)
            if not frames:
                scenes, samples, deduplicated, hashes = await self._pipeline.extract(video)
                await self._vision.save_scenes(video_id, scenes)
                frames = await self._vision.save_frames(
                    video_id, samples, deduplication=deduplicated, dhashes=hashes
                )
        except VisualProviderError as exc:
            return VisionIndexResult(0, None, (exc.code.value,))
        if self._ocr is None:
            warnings.append("ocr_provider_unavailable")
        elif not self._ocr_permitted:
            warnings.append("external_processing_not_permitted")
        elif not await self._vision.get_ocr_blocks(video_id):
            try:
                results: list[OCRExtractionResult] = []
                for frame in frames:
                    if frame.is_duplicate:
                        continue
                    sample = FrameSample(
                        video_id,
                        frame.sequence,
                        frame.start_ms,
                        frame.end_ms,
                        Path(frame.image_path),
                        FrameSource(frame.source),
                    )
                    results.append(
                        await self._ocr.extract(OCRExtractionRequest(job_id, video_id, sample))
                    )
                await self._vision.save_ocr_results(video_id, results)
            except VisionProviderError:
                warnings.append("ocr_provider_unavailable")
        collection = None
        if self._visual is None:
            warnings.append("visual_provider_unavailable")
        else:
            try:
                collection = await self._visual.ensure_indexed(video_id)
            except VisualProviderError as exc:
                warnings.append(exc.code.value)
        return VisionIndexResult(len(frames), collection, tuple(warnings))
