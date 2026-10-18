"""OpenCV-backed perceptual hashing and frame deduplication.

This adapter fulfils the ``FrameDeduplicator`` protocol defined in
``galaxy_frog.domain.vision.ports``.  It computes a 64-bit difference hash
(dHash) for extracted video frames and compares consecutive frames using
Hamming distance.  Near-identical frames (Hamming distance <= threshold)
are dropped to conserve cloud OCR credits and reduce index bloat.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from galaxy_frog.domain.vision.models import (
    DeduplicatedFrameSet,
    FrameSample,
    FrameSource,
    PerceptualHash,
)

_log = logging.getLogger(__name__)

# Default dHash parameters
_DEFAULT_HAMMING_THRESHOLD = 4
_HASH_ALGORITHM = "dhash"
_HASH_BITS = 64


def _import_cv2() -> Any:
    """Return the cv2 module lazily."""
    try:
        import cv2  # pyright: ignore[reportMissingModuleSource]

        return cv2
    except ImportError as exc:
        msg = (
            "opencv-python-headless is not installed.  "
            "Add 'opencv-python-headless' to pyproject.toml dependencies."
        )
        raise ImportError(msg) from exc


def compute_dhash_sync(image_path: Path) -> PerceptualHash:
    """Compute a 64-bit difference hash (dHash) for an image on disk.

    The algorithm:
    1. Read the image in grayscale.
    2. Resize to 9 columns x 8 rows (72 pixels).
    3. Compute adjacent column difference (left > right), producing an 8x8 boolean matrix (64 bits).
    4. Format the 64 bits as a 16-character hexadecimal string.
    """
    cv2: Any = _import_cv2()

    gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        msg = f"Could not read image from {image_path}"
        raise RuntimeError(msg)

    resized = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    diff = resized[:, 1:] > resized[:, :-1]

    bit_string = "".join("1" if b else "0" for b in diff.flat)
    hex_digest = f"{int(bit_string, 2):016x}"

    return PerceptualHash(
        hex_digest=hex_digest,
        algorithm=_HASH_ALGORITHM,
        bits=_HASH_BITS,
    )


def hamming_distance(
    hash1: PerceptualHash | str,
    hash2: PerceptualHash | str,
) -> int:
    """Compute the bitwise Hamming distance between two perceptual hashes.

    Accepts either ``PerceptualHash`` objects or hexadecimal strings.
    """
    hex1 = hash1.hex_digest if isinstance(hash1, PerceptualHash) else hash1
    hex2 = hash2.hex_digest if isinstance(hash2, PerceptualHash) else hash2

    if not hex1 or not hex2:
        msg = "Hash hex string must not be empty"
        raise ValueError(msg)

    val1 = int(hex1, 16)
    val2 = int(hex2, 16)

    return (val1 ^ val2).bit_count()


class OpenCVDHashDeduplicator:
    """Perceptual hash deduplicator using OpenCV difference hash (dHash).

    Parameters
    ----------
    hamming_threshold:
        Maximum Hamming distance to consider two frames near-duplicates.
        Default is 4 bits out of 64 (>93% structural similarity).
    always_keep_scene_boundary:
        If True, frames sampled at scene boundaries (FrameSource.SCENE_BOUNDARY)
        are always retained and reset the similarity baseline, preventing cut
        transitions from being dropped. Defaults to True.
    """

    def __init__(
        self,
        *,
        hamming_threshold: int = _DEFAULT_HAMMING_THRESHOLD,
        always_keep_scene_boundary: bool = True,
    ) -> None:
        if hamming_threshold < 0:
            msg = "hamming_threshold must be non-negative"
            raise ValueError(msg)
        self._hamming_threshold = hamming_threshold
        self._always_keep_scene_boundary = always_keep_scene_boundary

    @property
    def hamming_threshold(self) -> int:
        return self._hamming_threshold

    @property
    def always_keep_scene_boundary(self) -> bool:
        return self._always_keep_scene_boundary

    def compute_hash(self, image_path: Path) -> PerceptualHash:
        """Synchronously compute the dHash of a frame image."""
        return compute_dhash_sync(image_path)

    async def compute_hash_async(self, image_path: Path) -> PerceptualHash:
        """Asynchronously compute the dHash of a frame image."""
        return await asyncio.to_thread(self.compute_hash, image_path)

    def _deduplicate_sync(
        self,
        frames: Sequence[FrameSample],
    ) -> DeduplicatedFrameSet:
        """Synchronously deduplicate a sequence of frames."""
        if not frames:
            return DeduplicatedFrameSet(
                kept=(),
                dropped_count=0,
                hash_algorithm=_HASH_ALGORITHM,
                hamming_threshold=self._hamming_threshold,
            )

        first_video_id = frames[0].video_id
        for frame in frames[1:]:
            if frame.video_id != first_video_id:
                msg = (
                    f"All frames must belong to the same video_id "
                    f"(found {frame.video_id} and {first_video_id})"
                )
                raise ValueError(msg)

        sorted_frames = sorted(frames, key=lambda f: (f.start_ms, f.sequence))

        kept: list[FrameSample] = []
        last_kept_hash: PerceptualHash | None = None
        dropped_count = 0

        for frame in sorted_frames:
            current_hash = self.compute_hash(frame.path)

            if last_kept_hash is None or (
                self._always_keep_scene_boundary and frame.source == FrameSource.SCENE_BOUNDARY
            ):
                kept.append(frame)
                last_kept_hash = current_hash
            else:
                distance = hamming_distance(last_kept_hash, current_hash)
                if distance <= self._hamming_threshold:
                    dropped_count += 1
                    _log.debug(
                        "Dropping duplicate frame sequence=%d (distance=%d <= %d)",
                        frame.sequence,
                        distance,
                        self._hamming_threshold,
                    )
                else:
                    kept.append(frame)
                    last_kept_hash = current_hash

        return DeduplicatedFrameSet(
            kept=tuple(kept),
            dropped_count=dropped_count,
            hash_algorithm=_HASH_ALGORITHM,
            hamming_threshold=self._hamming_threshold,
        )

    async def deduplicate(
        self,
        frames: Sequence[FrameSample],
    ) -> DeduplicatedFrameSet:
        """Asynchronously filter near-duplicate frames from a sequence."""
        return await asyncio.to_thread(self._deduplicate_sync, frames)
