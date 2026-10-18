"""Vision domain: scene detection, frame sampling, and OCR provider protocols."""

from galaxy_frog.domain.vision.models import (
    DeduplicatedFrameSet,
    FrameSample,
    FrameSource,
    OCRBlock,
    OCRExtractionRequest,
    OCRExtractionResult,
    OCRProviderSpec,
    PerceptualHash,
    SceneBoundary,
    SceneDetectionResult,
)
from galaxy_frog.domain.vision.ports import FrameDeduplicator, OCRProvider, SceneDetector

__all__ = [
    "DeduplicatedFrameSet",
    "FrameDeduplicator",
    "FrameSample",
    "FrameSource",
    "OCRBlock",
    "OCRExtractionRequest",
    "OCRExtractionResult",
    "OCRProvider",
    "OCRProviderSpec",
    "PerceptualHash",
    "SceneBoundary",
    "SceneDetectionResult",
    "SceneDetector",
]
