"""Unit tests for OpenCV perceptual hashing and frame deduplication."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import cv2
import numpy as np
import pytest

from galaxy_frog.adapters.vision.deduplication import (
    OpenCVDHashDeduplicator,
    _import_cv2,
    compute_dhash_sync,
    hamming_distance,
)
from galaxy_frog.domain.vision.models import (
    DeduplicatedFrameSet,
    FrameSample,
    FrameSource,
    PerceptualHash,
)
from galaxy_frog.domain.vision.ports import FrameDeduplicator


def _create_test_image(path: Path, pattern: str) -> Path:
    """Create a synthetic test image and write it to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if pattern == "black":
        img = np.zeros((100, 100, 3), dtype=np.uint8)
    elif pattern == "white":
        img = np.ones((100, 100, 3), dtype=np.uint8) * 255
    elif pattern == "hgradient":
        # Horizontal gradient: pixel values increase left to right
        gradient = np.tile(np.linspace(0, 255, 100, dtype=np.uint8), (100, 1))
        img = cv2.cvtColor(gradient, cv2.COLOR_GRAY2BGR)
    elif pattern == "vgradient":
        # Vertical gradient: pixel values increase top to bottom
        gradient = np.tile(np.linspace(0, 255, 100, dtype=np.uint8).reshape(100, 1), (1, 100))
        img = cv2.cvtColor(gradient, cv2.COLOR_GRAY2BGR)
    elif pattern == "hgradient_slight_noise":
        # Near duplicate of hgradient with very small noise
        gradient = np.tile(np.linspace(0, 255, 100, dtype=np.uint8), (100, 1))
        noisy = np.clip(gradient.astype(np.int16) + 1, 0, 255).astype(np.uint8)
        img = cv2.cvtColor(noisy, cv2.COLOR_GRAY2BGR)
    else:
        msg = f"Unknown pattern: {pattern}"
        raise ValueError(msg)

    cv2.imwrite(str(path), img)
    return path


class TestImportCv2:
    def test_import_cv2_success(self) -> None:
        cv = _import_cv2()
        assert hasattr(cv, "imread")

    def test_import_cv2_raises_on_missing(self) -> None:
        with (
            patch.dict("sys.modules", {"cv2": None}),
            pytest.raises(ImportError, match="opencv-python-headless is not installed"),
        ):
            _import_cv2()


class TestComputeDhashSync:
    def test_compute_dhash_returns_valid_perceptual_hash(self, tmp_path: Path) -> None:
        img_path = _create_test_image(tmp_path / "img.jpg", "hgradient")
        ph = compute_dhash_sync(img_path)

        assert isinstance(ph, PerceptualHash)
        assert ph.algorithm == "dhash"
        assert ph.bits == 64
        assert len(ph.hex_digest) == 16
        # Valid hex
        int(ph.hex_digest, 16)

    def test_identical_images_have_identical_hashes(self, tmp_path: Path) -> None:
        p1 = _create_test_image(tmp_path / "img1.jpg", "hgradient")
        p2 = _create_test_image(tmp_path / "img2.jpg", "hgradient")

        h1 = compute_dhash_sync(p1)
        h2 = compute_dhash_sync(p2)

        assert h1.hex_digest == h2.hex_digest

    def test_different_images_have_different_hashes(self, tmp_path: Path) -> None:
        p1 = _create_test_image(tmp_path / "img1.jpg", "hgradient")
        p2 = _create_test_image(tmp_path / "img2.jpg", "vgradient")

        h1 = compute_dhash_sync(p1)
        h2 = compute_dhash_sync(p2)

        assert h1.hex_digest != h2.hex_digest

    def test_unreadable_image_raises_runtime_error(self, tmp_path: Path) -> None:
        missing = tmp_path / "non_existent.jpg"
        with pytest.raises(RuntimeError, match="Could not read image"):
            compute_dhash_sync(missing)


class TestHammingDistance:
    def test_identical_hashes_distance_zero(self) -> None:
        h1 = PerceptualHash(hex_digest="0123456789abcdef", algorithm="dhash", bits=64)
        h2 = PerceptualHash(hex_digest="0123456789abcdef", algorithm="dhash", bits=64)

        assert hamming_distance(h1, h2) == 0

    def test_identical_hex_strings_distance_zero(self) -> None:
        assert hamming_distance("0123456789abcdef", "0123456789abcdef") == 0

    def test_single_bit_flip_distance_one(self) -> None:
        # 0x0 vs 0x1 -> 1 bit difference
        assert hamming_distance("0000000000000000", "0000000000000001") == 1

    def test_four_bit_flips_distance_four(self) -> None:
        # 0x0 vs 0xf (1111 in binary) -> 4 bits difference
        assert hamming_distance("0000000000000000", "000000000000000f") == 4

    def test_all_bits_flipped_distance_64(self) -> None:
        assert hamming_distance("0000000000000000", "ffffffffffffffff") == 64

    def test_mixed_types_perceptual_hash_and_string(self) -> None:
        h1 = PerceptualHash(hex_digest="0000000000000000", algorithm="dhash", bits=64)
        assert hamming_distance(h1, "0000000000000003") == 2

    def test_empty_string_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Hash hex string must not be empty"):
            hamming_distance("", "0000000000000000")

        with pytest.raises(ValueError, match="Hash hex string must not be empty"):
            hamming_distance("0000000000000000", "")


class TestOpenCVDHashDeduplicator:
    def test_implements_protocol(self) -> None:
        dedup = OpenCVDHashDeduplicator()
        assert isinstance(dedup, FrameDeduplicator)

    def test_negative_threshold_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="hamming_threshold must be non-negative"):
            OpenCVDHashDeduplicator(hamming_threshold=-1)

    def test_properties(self) -> None:
        dedup = OpenCVDHashDeduplicator(hamming_threshold=6, always_keep_scene_boundary=False)
        assert dedup.hamming_threshold == 6
        assert dedup.always_keep_scene_boundary is False

    @pytest.mark.asyncio
    async def test_compute_hash_methods(self, tmp_path: Path) -> None:
        p = _create_test_image(tmp_path / "img.jpg", "hgradient")
        dedup = OpenCVDHashDeduplicator()

        h_sync = dedup.compute_hash(p)
        h_async = await dedup.compute_hash_async(p)

        assert h_sync == h_async
        assert isinstance(h_sync, PerceptualHash)

    @pytest.mark.asyncio
    async def test_deduplicate_empty_sequence(self) -> None:
        dedup = OpenCVDHashDeduplicator(hamming_threshold=4)
        result = await dedup.deduplicate([])

        assert isinstance(result, DeduplicatedFrameSet)
        assert result.kept == ()
        assert result.dropped_count == 0
        assert result.hash_algorithm == "dhash"
        assert result.hamming_threshold == 4

    @pytest.mark.asyncio
    async def test_deduplicate_mismatched_video_ids_raises(self, tmp_path: Path) -> None:
        p1 = _create_test_image(tmp_path / "f1.jpg", "black")
        p2 = _create_test_image(tmp_path / "f2.jpg", "black")

        v1 = uuid4()
        v2 = uuid4()

        f1 = FrameSample(
            video_id=v1,
            sequence=0,
            start_ms=0,
            end_ms=5000,
            path=p1,
            source=FrameSource.SCENE_BOUNDARY,
        )
        f2 = FrameSample(
            video_id=v2,
            sequence=1,
            start_ms=5000,
            end_ms=10000,
            path=p2,
            source=FrameSource.PERIODIC,
        )

        dedup = OpenCVDHashDeduplicator()
        with pytest.raises(ValueError, match="All frames must belong to the same video_id"):
            await dedup.deduplicate([f1, f2])

    @pytest.mark.asyncio
    async def test_deduplicate_drops_near_identical_frames(self, tmp_path: Path) -> None:
        v_id = uuid4()
        p1 = _create_test_image(tmp_path / "f1.jpg", "hgradient")
        p2 = _create_test_image(tmp_path / "f2.jpg", "hgradient_slight_noise")
        p3 = _create_test_image(tmp_path / "f3.jpg", "vgradient")

        f1 = FrameSample(
            video_id=v_id,
            sequence=0,
            start_ms=0,
            end_ms=5000,
            path=p1,
            source=FrameSource.SCENE_BOUNDARY,
        )
        f2 = FrameSample(
            video_id=v_id,
            sequence=1,
            start_ms=5000,
            end_ms=10000,
            path=p2,
            source=FrameSource.PERIODIC,
        )
        f3 = FrameSample(
            video_id=v_id,
            sequence=2,
            start_ms=10000,
            end_ms=15000,
            path=p3,
            source=FrameSource.PERIODIC,
        )

        dedup = OpenCVDHashDeduplicator(hamming_threshold=4)
        result = await dedup.deduplicate([f1, f2, f3])

        assert len(result.kept) == 2
        assert result.kept[0] == f1
        assert result.kept[1] == f3
        assert result.dropped_count == 1

    @pytest.mark.asyncio
    async def test_deduplicate_always_keeps_scene_boundary_by_default(self, tmp_path: Path) -> None:
        v_id = uuid4()
        # All frames identical
        p1 = _create_test_image(tmp_path / "f1.jpg", "black")
        p2 = _create_test_image(tmp_path / "f2.jpg", "black")
        p3 = _create_test_image(tmp_path / "f3.jpg", "black")

        f1 = FrameSample(
            video_id=v_id,
            sequence=0,
            start_ms=0,
            end_ms=5000,
            path=p1,
            source=FrameSource.SCENE_BOUNDARY,
        )
        f2 = FrameSample(
            video_id=v_id,
            sequence=1,
            start_ms=5000,
            end_ms=10000,
            path=p2,
            source=FrameSource.PERIODIC,
        )
        f3 = FrameSample(
            video_id=v_id,
            sequence=2,
            start_ms=10000,
            end_ms=15000,
            path=p3,
            source=FrameSource.SCENE_BOUNDARY,
        )

        # With always_keep_scene_boundary=True (default), f3 is kept even though identical
        dedup = OpenCVDHashDeduplicator(hamming_threshold=4, always_keep_scene_boundary=True)
        result = await dedup.deduplicate([f1, f2, f3])

        assert len(result.kept) == 2
        assert result.kept == (f1, f3)
        assert result.dropped_count == 1

    @pytest.mark.asyncio
    async def test_deduplicate_drops_scene_boundary_if_disabled(self, tmp_path: Path) -> None:
        v_id = uuid4()
        p1 = _create_test_image(tmp_path / "f1.jpg", "black")
        p2 = _create_test_image(tmp_path / "f2.jpg", "black")

        f1 = FrameSample(
            video_id=v_id,
            sequence=0,
            start_ms=0,
            end_ms=5000,
            path=p1,
            source=FrameSource.SCENE_BOUNDARY,
        )
        f2 = FrameSample(
            video_id=v_id,
            sequence=1,
            start_ms=5000,
            end_ms=10000,
            path=p2,
            source=FrameSource.SCENE_BOUNDARY,
        )

        # With always_keep_scene_boundary=False, f2 is dropped
        dedup = OpenCVDHashDeduplicator(hamming_threshold=4, always_keep_scene_boundary=False)
        result = await dedup.deduplicate([f1, f2])

        assert len(result.kept) == 1
        assert result.kept == (f1,)
        assert result.dropped_count == 1

    @pytest.mark.asyncio
    async def test_deduplicate_sorts_out_of_order_input(self, tmp_path: Path) -> None:
        v_id = uuid4()
        p1 = _create_test_image(tmp_path / "f1.jpg", "hgradient")
        p2 = _create_test_image(tmp_path / "f2.jpg", "vgradient")

        f1 = FrameSample(
            video_id=v_id,
            sequence=0,
            start_ms=0,
            end_ms=5000,
            path=p1,
            source=FrameSource.SCENE_BOUNDARY,
        )
        f2 = FrameSample(
            video_id=v_id,
            sequence=1,
            start_ms=5000,
            end_ms=10000,
            path=p2,
            source=FrameSource.PERIODIC,
        )

        dedup = OpenCVDHashDeduplicator()
        # Pass in reverse order [f2, f1]
        result = await dedup.deduplicate([f2, f1])

        # Kept should be ordered [f1, f2]
        assert result.kept == (f1, f2)
        assert result.dropped_count == 0
