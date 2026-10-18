"""Vision adapters: scene detection, frame sampling, and OCR providers."""

from galaxy_frog.adapters.vision.deduplication import (
    OpenCVDHashDeduplicator,
    compute_dhash_sync,
    hamming_distance,
)
from galaxy_frog.adapters.vision.scene_detect import PySceneDetectAdapter

__all__ = [
    "OpenCVDHashDeduplicator",
    "PySceneDetectAdapter",
    "compute_dhash_sync",
    "hamming_distance",
]
