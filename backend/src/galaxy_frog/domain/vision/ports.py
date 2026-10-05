"""Application-facing protocols for scene detection and OCR extraction."""

from pathlib import Path
from typing import Protocol, runtime_checkable

from galaxy_frog.domain.vision.models import (
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
class OCRProvider(Protocol):
    """Extract text from a single deduplicated video frame."""

    spec: OCRProviderSpec

    async def extract(self, request: OCRExtractionRequest) -> OCRExtractionResult: ...
