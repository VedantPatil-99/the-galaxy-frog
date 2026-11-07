"""Application-facing protocols for scene detection, frame deduplication, and OCR extraction."""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from galaxy_frog.domain.vision.models import (
    DeduplicatedFrameSet,
    FrameSample,
    OCRExtractionRequest,
    OCRExtractionResult,
    OCRProviderSpec,
    SceneDetectionResult,
)


@runtime_checkable
class SceneDetector(Protocol):
    """Detect visual scene boundaries and sample frames from a video file."""

    async def detect(
        self,
        video_path: Path,
        video_id: object,
        duration_ms: int,
    ) -> SceneDetectionResult: ...


@runtime_checkable
class FrameDeduplicator(Protocol):
    """Filter sampled frames by perceptual hash to drop near-identical frames."""

    async def deduplicate(
        self,
        frames: Sequence[FrameSample],
    ) -> DeduplicatedFrameSet: ...


@runtime_checkable
class OCRProvider(Protocol):
    """Extract text from a single deduplicated video frame."""

    spec: OCRProviderSpec

    async def extract(self, request: OCRExtractionRequest) -> OCRExtractionResult: ...
