"""Behaviour tests for vision domain models and provider ports."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

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
from galaxy_frog.domain.vision.ports import OCRProvider, SceneDetector

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ABS_PATH = Path("C:/tmp/frame_001.jpg")
_VIDEO_ID = uuid4()
_JOB_ID = uuid4()
_NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)

_SPEC = OCRProviderSpec(provider="llamaparse", provider_revision="1.0.0")

_FRAME = FrameSample(
    video_id=_VIDEO_ID,
    sequence=0,
    start_ms=0,
    end_ms=1000,
    path=_ABS_PATH,
    source=FrameSource.SCENE_BOUNDARY,
)


# ---------------------------------------------------------------------------
# OCRProviderSpec
# ---------------------------------------------------------------------------


class TestOCRProviderSpec:
    def test_valid(self) -> None:
        spec = OCRProviderSpec(provider="llamaparse", provider_revision="1.0.0")
        assert spec.provider == "llamaparse"
        assert spec.provider_revision == "1.0.0"

    def test_blank_provider_rejected(self) -> None:
        with pytest.raises(ValueError, match="provider must not be blank"):
            OCRProviderSpec(provider="  ", provider_revision="1.0.0")

    def test_blank_revision_rejected(self) -> None:
        with pytest.raises(ValueError, match="provider_revision must not be blank"):
            OCRProviderSpec(provider="llamaparse", provider_revision="")


# ---------------------------------------------------------------------------
# FrameSample
# ---------------------------------------------------------------------------


class TestFrameSample:
    def test_valid_scene_boundary(self) -> None:
        f = FrameSample(
            video_id=_VIDEO_ID,
            sequence=0,
            start_ms=0,
            end_ms=500,
            path=_ABS_PATH,
            source=FrameSource.SCENE_BOUNDARY,
        )
        assert f.start_ms == 0
        assert f.source is FrameSource.SCENE_BOUNDARY

    def test_valid_periodic(self) -> None:
        f = FrameSample(
            video_id=_VIDEO_ID,
            sequence=3,
            start_ms=3000,
            end_ms=4000,
            path=_ABS_PATH,
            source=FrameSource.PERIODIC,
        )
        assert f.source is FrameSource.PERIODIC

    def test_negative_sequence_rejected(self) -> None:
        with pytest.raises(ValueError, match="sequence must be non-negative"):
            FrameSample(
                video_id=_VIDEO_ID,
                sequence=-1,
                start_ms=0,
                end_ms=1000,
                path=_ABS_PATH,
                source=FrameSource.SCENE_BOUNDARY,
            )

    def test_negative_start_ms_rejected(self) -> None:
        with pytest.raises(ValueError, match="start_ms must be non-negative"):
            FrameSample(
                video_id=_VIDEO_ID,
                sequence=0,
                start_ms=-1,
                end_ms=1000,
                path=_ABS_PATH,
                source=FrameSource.SCENE_BOUNDARY,
            )

    def test_end_not_greater_than_start_rejected(self) -> None:
        with pytest.raises(ValueError, match="end_ms must be greater than start_ms"):
            FrameSample(
                video_id=_VIDEO_ID,
                sequence=0,
                start_ms=1000,
                end_ms=1000,
                path=_ABS_PATH,
                source=FrameSource.SCENE_BOUNDARY,
            )

    def test_relative_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="path must be absolute"):
            FrameSample(
                video_id=_VIDEO_ID,
                sequence=0,
                start_ms=0,
                end_ms=1000,
                path=Path("relative/path.jpg"),
                source=FrameSource.SCENE_BOUNDARY,
            )

    def test_invalid_source_rejected(self) -> None:
        with pytest.raises(ValueError, match="source must be a FrameSource member"):
            FrameSample(
                video_id=_VIDEO_ID,
                sequence=0,
                start_ms=0,
                end_ms=1000,
                path=_ABS_PATH,
                source="not_a_frame_source",  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# PerceptualHash
# ---------------------------------------------------------------------------


class TestPerceptualHash:
    def test_valid_64_bit_hash(self) -> None:
        # 64 bits → 16 hex chars
        ph = PerceptualHash(hex_digest="a1b2c3d4e5f60718", algorithm="dhash", bits=64)
        assert ph.bits == 64

    def test_blank_algorithm_rejected(self) -> None:
        with pytest.raises(ValueError, match="algorithm must not be blank"):
            PerceptualHash(hex_digest="a1b2c3d4e5f60718", algorithm="  ", bits=64)

    def test_zero_bits_rejected(self) -> None:
        with pytest.raises(ValueError, match="bits must be positive"):
            PerceptualHash(hex_digest="a1b2c3d4e5f60718", algorithm="dhash", bits=0)

    def test_wrong_hex_length_rejected(self) -> None:
        # 64 bits needs 16 hex chars; "abcd" is only 4
        with pytest.raises(ValueError, match="hex_digest length"):
            PerceptualHash(hex_digest="abcd", algorithm="dhash", bits=64)

    def test_non_hex_digest_rejected(self) -> None:
        # 16 chars but contains 'g' — invalid hex
        with pytest.raises(ValueError, match="valid hexadecimal"):
            PerceptualHash(hex_digest="gggggggggggggggg", algorithm="dhash", bits=64)

    def test_8_bit_hash_needs_2_hex_chars(self) -> None:
        ph = PerceptualHash(hex_digest="ff", algorithm="ahash", bits=8)
        assert ph.hex_digest == "ff"

    def test_non_byte_aligned_bits_rounds_up(self) -> None:
        # 5 bits → ceil(5/4) = 2 hex chars
        ph = PerceptualHash(hex_digest="1f", algorithm="whash", bits=5)
        assert ph.bits == 5


# ---------------------------------------------------------------------------
# SceneBoundary
# ---------------------------------------------------------------------------


class TestSceneBoundary:
    def test_valid(self) -> None:
        sb = SceneBoundary(start_ms=0, end_ms=5000, score=0.85)
        assert sb.score == 0.85

    def test_score_zero_and_one_valid(self) -> None:
        SceneBoundary(start_ms=0, end_ms=1000, score=0.0)
        SceneBoundary(start_ms=0, end_ms=1000, score=1.0)

    def test_negative_score_rejected(self) -> None:
        with pytest.raises(ValueError, match="score must be a finite float"):
            SceneBoundary(start_ms=0, end_ms=5000, score=-0.1)

    def test_score_above_one_rejected(self) -> None:
        with pytest.raises(ValueError, match="score must be a finite float"):
            SceneBoundary(start_ms=0, end_ms=5000, score=1.1)

    def test_equal_interval_rejected(self) -> None:
        with pytest.raises(ValueError, match="end_ms must be greater than start_ms"):
            SceneBoundary(start_ms=1000, end_ms=1000, score=0.5)

    def test_negative_start_rejected(self) -> None:
        with pytest.raises(ValueError, match="start_ms must be non-negative"):
            SceneBoundary(start_ms=-1, end_ms=1000, score=0.5)


# ---------------------------------------------------------------------------
# SceneDetectionResult
# ---------------------------------------------------------------------------


class TestSceneDetectionResult:
    def test_valid_no_boundaries(self) -> None:
        r = SceneDetectionResult(
            video_id=_VIDEO_ID,
            duration_ms=60_000,
            boundaries=(),
            detector="pyscenedetect",
            detector_revision="0.6.5",
        )
        assert r.boundaries == ()

    def test_valid_with_boundaries(self) -> None:
        boundaries = (
            SceneBoundary(start_ms=0, end_ms=3000, score=0.9),
            SceneBoundary(start_ms=3000, end_ms=6000, score=0.7),
        )
        r = SceneDetectionResult(
            video_id=_VIDEO_ID,
            duration_ms=60_000,
            boundaries=boundaries,
            detector="pyscenedetect",
            detector_revision="0.6.5",
        )
        assert len(r.boundaries) == 2

    def test_zero_duration_rejected(self) -> None:
        with pytest.raises(ValueError, match="duration_ms must be positive"):
            SceneDetectionResult(
                video_id=_VIDEO_ID,
                duration_ms=0,
                boundaries=(),
                detector="pyscenedetect",
                detector_revision="0.6.5",
            )

    def test_blank_detector_rejected(self) -> None:
        with pytest.raises(ValueError, match="detector must not be blank"):
            SceneDetectionResult(
                video_id=_VIDEO_ID,
                duration_ms=60_000,
                boundaries=(),
                detector="  ",
                detector_revision="0.6.5",
            )

    def test_blank_detector_revision_rejected(self) -> None:
        with pytest.raises(ValueError, match="detector_revision must not be blank"):
            SceneDetectionResult(
                video_id=_VIDEO_ID,
                duration_ms=60_000,
                boundaries=(),
                detector="pyscenedetect",
                detector_revision="",
            )


# ---------------------------------------------------------------------------
# DeduplicatedFrameSet
# ---------------------------------------------------------------------------


class TestDeduplicatedFrameSet:
    def test_valid_all_kept(self) -> None:
        dfs = DeduplicatedFrameSet(
            kept=(_FRAME,),
            dropped_count=0,
            hash_algorithm="dhash",
            hamming_threshold=10,
        )
        assert dfs.dropped_count == 0

    def test_valid_some_dropped(self) -> None:
        dfs = DeduplicatedFrameSet(
            kept=(_FRAME,),
            dropped_count=5,
            hash_algorithm="dhash",
            hamming_threshold=10,
        )
        assert dfs.dropped_count == 5

    def test_empty_kept_is_valid(self) -> None:
        dfs = DeduplicatedFrameSet(
            kept=(),
            dropped_count=3,
            hash_algorithm="dhash",
            hamming_threshold=10,
        )
        assert dfs.kept == ()

    def test_negative_dropped_rejected(self) -> None:
        with pytest.raises(ValueError, match="dropped_count must be non-negative"):
            DeduplicatedFrameSet(
                kept=(),
                dropped_count=-1,
                hash_algorithm="dhash",
                hamming_threshold=10,
            )

    def test_blank_hash_algorithm_rejected(self) -> None:
        with pytest.raises(ValueError, match="hash_algorithm must not be blank"):
            DeduplicatedFrameSet(
                kept=(),
                dropped_count=0,
                hash_algorithm="",
                hamming_threshold=10,
            )

    def test_negative_threshold_rejected(self) -> None:
        with pytest.raises(ValueError, match="hamming_threshold must be non-negative"):
            DeduplicatedFrameSet(
                kept=(),
                dropped_count=0,
                hash_algorithm="dhash",
                hamming_threshold=-1,
            )

    def test_zero_threshold_valid(self) -> None:
        dfs = DeduplicatedFrameSet(
            kept=(_FRAME,),
            dropped_count=0,
            hash_algorithm="dhash",
            hamming_threshold=0,
        )
        assert dfs.hamming_threshold == 0


# ---------------------------------------------------------------------------
# OCRBlock
# ---------------------------------------------------------------------------


class TestOCRBlock:
    def test_valid_without_confidence(self) -> None:
        block = OCRBlock(
            text="Hello World",
            start_ms=0,
            end_ms=1000,
            source_frame_sequence=0,
            provider_spec=_SPEC,
        )
        assert block.confidence is None
        assert block.confidence_method is None

    def test_valid_with_confidence(self) -> None:
        block = OCRBlock(
            text="Table header",
            start_ms=1000,
            end_ms=2000,
            source_frame_sequence=1,
            provider_spec=_SPEC,
            confidence=0.95,
            confidence_method="llamaparse_score",
        )
        assert block.confidence == 0.95

    def test_blank_text_rejected(self) -> None:
        with pytest.raises(ValueError, match="text must not be blank"):
            OCRBlock(
                text="   ",
                start_ms=0,
                end_ms=1000,
                source_frame_sequence=0,
                provider_spec=_SPEC,
            )

    def test_invalid_interval_rejected(self) -> None:
        with pytest.raises(ValueError, match="end_ms must be greater than start_ms"):
            OCRBlock(
                text="text",
                start_ms=500,
                end_ms=500,
                source_frame_sequence=0,
                provider_spec=_SPEC,
            )

    def test_negative_frame_sequence_rejected(self) -> None:
        with pytest.raises(ValueError, match="source_frame_sequence must be non-negative"):
            OCRBlock(
                text="text",
                start_ms=0,
                end_ms=1000,
                source_frame_sequence=-1,
                provider_spec=_SPEC,
            )

    def test_confidence_without_method_rejected(self) -> None:
        with pytest.raises(ValueError, match="confidence_method is required"):
            OCRBlock(
                text="text",
                start_ms=0,
                end_ms=1000,
                source_frame_sequence=0,
                provider_spec=_SPEC,
                confidence=0.8,
                confidence_method=None,
            )

    def test_method_without_confidence_rejected(self) -> None:
        with pytest.raises(ValueError, match="confidence_method requires confidence"):
            OCRBlock(
                text="text",
                start_ms=0,
                end_ms=1000,
                source_frame_sequence=0,
                provider_spec=_SPEC,
                confidence=None,
                confidence_method="score",
            )

    def test_confidence_above_one_rejected(self) -> None:
        with pytest.raises(ValueError, match="finite and between zero and one"):
            OCRBlock(
                text="text",
                start_ms=0,
                end_ms=1000,
                source_frame_sequence=0,
                provider_spec=_SPEC,
                confidence=1.1,
                confidence_method="score",
            )

    def test_invalid_provider_spec_rejected(self) -> None:
        with pytest.raises(ValueError, match="provider_spec must be an OCRProviderSpec"):
            OCRBlock(
                text="text",
                start_ms=0,
                end_ms=1000,
                source_frame_sequence=0,
                provider_spec="not-a-spec",  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# OCRExtractionRequest
# ---------------------------------------------------------------------------


class TestOCRExtractionRequest:
    def test_valid(self) -> None:
        req = OCRExtractionRequest(job_id=_JOB_ID, video_id=_VIDEO_ID, frame=_FRAME)
        assert req.job_id == _JOB_ID

    def test_invalid_frame_rejected(self) -> None:
        with pytest.raises(ValueError, match="frame must be a FrameSample"):
            OCRExtractionRequest(
                job_id=_JOB_ID,
                video_id=_VIDEO_ID,
                frame="not-a-frame",  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# OCRExtractionResult
# ---------------------------------------------------------------------------


class TestOCRExtractionResult:
    def _make_request(self) -> OCRExtractionRequest:
        return OCRExtractionRequest(job_id=_JOB_ID, video_id=_VIDEO_ID, frame=_FRAME)

    def test_valid_no_blocks(self) -> None:
        req = self._make_request()
        result = OCRExtractionResult(
            request=req,
            blocks=(),
            provider_spec=_SPEC,
            extracted_at=_NOW,
        )
        assert result.blocks == ()

    def test_valid_with_blocks(self) -> None:
        req = self._make_request()
        block = OCRBlock(
            text="slide content",
            start_ms=0,
            end_ms=1000,
            source_frame_sequence=0,
            provider_spec=_SPEC,
        )
        result = OCRExtractionResult(
            request=req,
            blocks=(block,),
            provider_spec=_SPEC,
            extracted_at=_NOW,
        )
        assert len(result.blocks) == 1

    def test_naive_datetime_rejected(self) -> None:
        req = self._make_request()
        with pytest.raises(ValueError, match="extracted_at must be timezone-aware"):
            OCRExtractionResult(
                request=req,
                blocks=(),
                provider_spec=_SPEC,
                extracted_at=datetime(2026, 10, 5, 12, 0, 0),  # naive
            )

    def test_invalid_request_rejected(self) -> None:
        with pytest.raises(ValueError, match="request must be an OCRExtractionRequest"):
            OCRExtractionResult(
                request="not-a-request",  # type: ignore[arg-type]
                blocks=(),
                provider_spec=_SPEC,
                extracted_at=_NOW,
            )

    def test_invalid_provider_spec_rejected(self) -> None:
        req = self._make_request()
        with pytest.raises(ValueError, match="provider_spec must be an OCRProviderSpec"):
            OCRExtractionResult(
                request=req,
                blocks=(),
                provider_spec="bad",  # type: ignore[arg-type]
                extracted_at=_NOW,
            )


# ---------------------------------------------------------------------------
# Protocol structural checks
# ---------------------------------------------------------------------------


class TestProtocolStructure:
    def test_scene_detector_protocol_requires_detect(self) -> None:
        class BadDetector:
            pass

        assert not isinstance(BadDetector(), SceneDetector)

    def test_ocr_provider_protocol_requires_spec_and_extract(self) -> None:
        class BadProvider:
            pass

        assert not isinstance(BadProvider(), OCRProvider)

    def test_conforming_ocr_provider_is_recognised(self) -> None:
        class GoodProvider:
            spec = _SPEC

            async def extract(self, request: OCRExtractionRequest) -> OCRExtractionResult:
                raise NotImplementedError  # pragma: no cover

        assert isinstance(GoodProvider(), OCRProvider)

    def test_conforming_scene_detector_is_recognised(self) -> None:
        class GoodDetector:
            async def detect(
                self,
                video_path: Path,
                video_id: object,
                duration_ms: int,
            ) -> SceneDetectionResult:
                raise NotImplementedError  # pragma: no cover

        assert isinstance(GoodDetector(), SceneDetector)
