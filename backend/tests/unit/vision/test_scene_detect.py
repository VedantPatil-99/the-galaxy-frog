"""Unit tests for PySceneDetectAdapter (scene detection and frame sampling).

All heavy I/O (PySceneDetect, OpenCV) is replaced by lightweight fakes so the
tests run without any video files or native libraries installed.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

from galaxy_frog.adapters.vision.scene_detect import (
    PySceneDetectAdapter,
    _build_sample_points,
    _detect_scenes_sync,
    _extract_frame_sync,
    _import_cv2,
    _import_scenedetect,
    _scenedetect_version,
    _timecode_to_ms,
)
from galaxy_frog.domain.vision.models import FrameSource, SceneBoundary, SceneDetectionResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "frames"


@pytest.fixture()
def video_id() -> UUID:
    return uuid4()


@pytest.fixture()
def video_path(tmp_path: Path) -> Path:
    p = tmp_path / "video.mp4"
    p.write_bytes(b"fake")
    return p


@pytest.fixture()
def adapter(workspace: Path) -> PySceneDetectAdapter:
    return PySceneDetectAdapter(
        workspace_dir=workspace,
        periodic_interval_ms=10_000,
        detector_revision="0.6.5",
    )


# ---------------------------------------------------------------------------
# Helper: fake FrameTimecode
# ---------------------------------------------------------------------------


class _FakeTimecode:
    def __init__(self, seconds: float) -> None:
        self._seconds = seconds

    def get_seconds(self) -> float:
        return self._seconds


def _fake_scenes(
    *pairs: tuple[float, float],
) -> list[tuple[_FakeTimecode, _FakeTimecode]]:
    return [(_FakeTimecode(s), _FakeTimecode(e)) for s, e in pairs]


# ---------------------------------------------------------------------------
# _timecode_to_ms
# ---------------------------------------------------------------------------


class TestTimecodeToMs:
    def test_converts_seconds_to_ms(self) -> None:
        tc = _FakeTimecode(2.5)
        assert _timecode_to_ms(tc) == 2500

    def test_clamps_negative_to_zero(self) -> None:
        # Negative seconds can occur for the very first frame.
        tc = _FakeTimecode(-0.001)
        assert _timecode_to_ms(tc) == 0

    def test_integer_seconds(self) -> None:
        tc = _FakeTimecode(10.0)
        assert _timecode_to_ms(tc) == 10_000

    def test_raises_for_non_timecode(self) -> None:
        with pytest.raises(TypeError, match="Unexpected timecode type"):
            _timecode_to_ms(object())


# ---------------------------------------------------------------------------
# _build_sample_points
# ---------------------------------------------------------------------------


class TestBuildSamplePoints:
    def test_single_short_scene_yields_one_boundary_frame(self) -> None:
        result = _build_sample_points([(0, 5_000)], 5_000, 10_000)
        assert len(result) == 1
        capture_ms, start_ms, end_ms, source = result[0]
        assert capture_ms == 0
        assert start_ms == 0
        assert end_ms == 5_000
        assert source is FrameSource.SCENE_BOUNDARY

    def test_long_scene_yields_periodic_frames(self) -> None:
        # 35-second scene with 10 s interval:
        # intervals = 35000 // 10000 = 3, range(1, 3) gives i=1,2 → 2 periodic frames.
        result = _build_sample_points([(0, 35_000)], 35_000, 10_000)
        sources = [r[3] for r in result]
        assert sources.count(FrameSource.SCENE_BOUNDARY) == 1
        assert sources.count(FrameSource.PERIODIC) == 2

    def test_periodic_frame_not_added_beyond_scene_end(self) -> None:
        # 20-second scene: boundary at 0, periodic at 10 s; not at 20 s.
        result = _build_sample_points([(0, 20_000)], 20_000, 10_000)
        captures = [r[0] for r in result]
        assert 20_000 not in captures

    def test_two_scenes_each_yield_boundary(self) -> None:
        result = _build_sample_points([(0, 5_000), (5_000, 10_000)], 10_000, 10_000)
        boundaries = [r for r in result if r[3] is FrameSource.SCENE_BOUNDARY]
        assert len(boundaries) == 2

    def test_exact_multiple_of_interval(self) -> None:
        # Scene is exactly 2x the interval.  One periodic frame at 10 s.
        result = _build_sample_points([(0, 20_000)], 20_000, 10_000)
        periodics = [r for r in result if r[3] is FrameSource.PERIODIC]
        assert len(periodics) == 1
        assert periodics[0][0] == 10_000

    def test_multiple_scenes_preserves_start_end(self) -> None:
        result = _build_sample_points([(0, 8_000), (8_000, 15_000)], 15_000, 10_000)
        # First scene: boundary at 0, no periodic (8 s < 10 s).
        # Second scene: boundary at 8 000, no periodic (7 s < 10 s).
        assert len(result) == 2

    def test_empty_boundaries_returns_empty(self) -> None:
        result = _build_sample_points([], 5_000, 10_000)
        assert result == []


# ---------------------------------------------------------------------------
# PySceneDetectAdapter.__init__
# ---------------------------------------------------------------------------


class TestAdapterInit:
    def test_valid_construction(self, workspace: Path) -> None:
        a = PySceneDetectAdapter(
            workspace_dir=workspace,
            periodic_interval_ms=5_000,
            detector_revision="test",
        )
        assert a._periodic_interval_ms == 5_000
        assert a._detector_revision == "test"

    def test_default_periodic_interval(self, workspace: Path) -> None:
        a = PySceneDetectAdapter(workspace_dir=workspace, detector_revision="x")
        assert a._periodic_interval_ms == 10_000

    def test_zero_interval_rejected(self, workspace: Path) -> None:
        with pytest.raises(ValueError, match="periodic_interval_ms must be positive"):
            PySceneDetectAdapter(workspace_dir=workspace, periodic_interval_ms=0)

    def test_negative_interval_rejected(self, workspace: Path) -> None:
        with pytest.raises(ValueError, match="periodic_interval_ms must be positive"):
            PySceneDetectAdapter(workspace_dir=workspace, periodic_interval_ms=-1)

    def test_relative_workspace_rejected(self) -> None:
        with pytest.raises(ValueError, match="workspace_dir must be an absolute path"):
            PySceneDetectAdapter(workspace_dir=Path("relative/path"))

    def test_version_fallback_used_when_no_override(self, workspace: Path) -> None:
        with patch(
            "galaxy_frog.adapters.vision.scene_detect._scenedetect_version",
            return_value="0.7.0",
        ):
            a = PySceneDetectAdapter(workspace_dir=workspace)
        assert a._detector_revision == "0.7.0"


# ---------------------------------------------------------------------------
# PySceneDetectAdapter._raw_to_boundaries
# ---------------------------------------------------------------------------


class TestRawToBoundaries:
    def test_converts_scenes_to_boundaries(self) -> None:
        raw = _fake_scenes((0.0, 5.0), (5.0, 10.0))
        result = PySceneDetectAdapter._raw_to_boundaries(raw, 10_000)
        assert len(result) == 2
        assert result[0] == SceneBoundary(start_ms=0, end_ms=5_000, score=1.0)
        assert result[1] == SceneBoundary(start_ms=5_000, end_ms=10_000, score=1.0)

    def test_end_clamped_to_duration(self) -> None:
        # PySceneDetect may report end past actual duration.
        raw = _fake_scenes((0.0, 12.0))
        result = PySceneDetectAdapter._raw_to_boundaries(raw, 10_000)
        assert result[0].end_ms == 10_000

    def test_empty_raw_yields_single_whole_video_scene(self) -> None:
        result = PySceneDetectAdapter._raw_to_boundaries([], 30_000)
        assert len(result) == 1
        assert result[0] == SceneBoundary(start_ms=0, end_ms=30_000, score=1.0)

    def test_invalid_interval_after_clamping_skipped(self) -> None:
        # Scene that starts after duration_ms — end_ms <= start_ms after clamping.
        raw = _fake_scenes((15.0, 20.0))
        result = PySceneDetectAdapter._raw_to_boundaries(raw, 10_000)
        # Clamped: end_ms = 10 000, start_ms = 15 000 → invalid → skipped → fallback.
        assert len(result) == 1
        assert result[0] == SceneBoundary(start_ms=0, end_ms=10_000, score=1.0)

    def test_score_is_always_one(self) -> None:
        raw = _fake_scenes((0.0, 5.0))
        result = PySceneDetectAdapter._raw_to_boundaries(raw, 10_000)
        assert all(b.score == 1.0 for b in result)


# ---------------------------------------------------------------------------
# PySceneDetectAdapter.detect  (end-to-end with mocked I/O)
# ---------------------------------------------------------------------------


def _make_mock_detect_sync(
    scenes: list[tuple[float, float]],
) -> object:
    """Return a callable that returns fake PySceneDetect scene list."""

    def _inner(_video_path: Path) -> list[tuple[_FakeTimecode, _FakeTimecode]]:
        return _fake_scenes(*scenes)

    return _inner


class TestAdapterDetect:
    @pytest.mark.asyncio
    async def test_detect_returns_correct_types(
        self,
        adapter: PySceneDetectAdapter,
        video_path: Path,
        video_id: UUID,
    ) -> None:
        with (
            patch(
                "galaxy_frog.adapters.vision.scene_detect._detect_scenes_sync",
                side_effect=_make_mock_detect_sync([(0.0, 5.0), (5.0, 10.0)]),
            ),
            patch(
                "galaxy_frog.adapters.vision.scene_detect._extract_frame_sync",
            ),
        ):
            detection, frames = await adapter.detect(video_path, video_id, 10_000)

        assert isinstance(detection, SceneDetectionResult)
        assert detection.video_id == video_id
        assert detection.duration_ms == 10_000
        assert detection.detector == "pyscenedetect-adaptive"
        assert len(detection.boundaries) == 2
        assert isinstance(frames, tuple)

    @pytest.mark.asyncio
    async def test_detect_fallback_to_whole_video_when_no_cuts(
        self,
        adapter: PySceneDetectAdapter,
        video_path: Path,
        video_id: UUID,
    ) -> None:
        with (
            patch(
                "galaxy_frog.adapters.vision.scene_detect._detect_scenes_sync",
                side_effect=_make_mock_detect_sync([]),
            ),
            patch(
                "galaxy_frog.adapters.vision.scene_detect._extract_frame_sync",
            ),
        ):
            detection, frames = await adapter.detect(video_path, video_id, 30_000)

        assert len(detection.boundaries) == 1
        assert detection.boundaries[0] == SceneBoundary(start_ms=0, end_ms=30_000, score=1.0)
        # 30 s scene / 10 s interval: boundary + 2 periodic (at 10 s, 20 s) = 3 frames.
        assert len(frames) == 3

    @pytest.mark.asyncio
    async def test_detect_frame_paths_are_absolute(
        self,
        adapter: PySceneDetectAdapter,
        video_path: Path,
        video_id: UUID,
        workspace: Path,
    ) -> None:
        with (
            patch(
                "galaxy_frog.adapters.vision.scene_detect._detect_scenes_sync",
                side_effect=_make_mock_detect_sync([(0.0, 5.0)]),
            ),
            patch(
                "galaxy_frog.adapters.vision.scene_detect._extract_frame_sync",
            ),
        ):
            _, frames = await adapter.detect(video_path, video_id, 5_000)

        for frame in frames:
            assert frame.path.is_absolute()

    @pytest.mark.asyncio
    async def test_detect_long_scene_produces_periodic_frames(
        self,
        adapter: PySceneDetectAdapter,
        video_path: Path,
        video_id: UUID,
    ) -> None:
        with (
            patch(
                "galaxy_frog.adapters.vision.scene_detect._detect_scenes_sync",
                side_effect=_make_mock_detect_sync([(0.0, 35.0)]),
            ),
            patch(
                "galaxy_frog.adapters.vision.scene_detect._extract_frame_sync",
            ),
        ):
            _, frames = await adapter.detect(video_path, video_id, 35_000)

        sources = [f.source for f in frames]
        assert FrameSource.SCENE_BOUNDARY in sources
        assert FrameSource.PERIODIC in sources

    @pytest.mark.asyncio
    async def test_detect_sequence_numbers_are_contiguous(
        self,
        adapter: PySceneDetectAdapter,
        video_path: Path,
        video_id: UUID,
    ) -> None:
        with (
            patch(
                "galaxy_frog.adapters.vision.scene_detect._detect_scenes_sync",
                side_effect=_make_mock_detect_sync([(0.0, 5.0), (5.0, 10.0)]),
            ),
            patch(
                "galaxy_frog.adapters.vision.scene_detect._extract_frame_sync",
            ),
        ):
            _, frames = await adapter.detect(video_path, video_id, 10_000)

        sequences = [f.sequence for f in frames]
        assert sequences == list(range(len(frames)))

    @pytest.mark.asyncio
    async def test_detect_video_id_propagated_to_frames(
        self,
        adapter: PySceneDetectAdapter,
        video_path: Path,
        video_id: UUID,
    ) -> None:
        with (
            patch(
                "galaxy_frog.adapters.vision.scene_detect._detect_scenes_sync",
                side_effect=_make_mock_detect_sync([(0.0, 10.0)]),
            ),
            patch(
                "galaxy_frog.adapters.vision.scene_detect._extract_frame_sync",
            ),
        ):
            _, frames = await adapter.detect(video_path, video_id, 10_000)

        for frame in frames:
            assert frame.video_id == video_id

    @pytest.mark.asyncio
    async def test_detect_non_uuid_video_id_raises(
        self,
        adapter: PySceneDetectAdapter,
        video_path: Path,
    ) -> None:
        with pytest.raises(TypeError, match="video_id must be a UUID"):
            await adapter.detect(video_path, "not-a-uuid", 10_000)

    @pytest.mark.asyncio
    async def test_detect_zero_duration_raises(
        self,
        adapter: PySceneDetectAdapter,
        video_path: Path,
        video_id: UUID,
    ) -> None:
        with pytest.raises(ValueError, match="duration_ms must be positive"):
            await adapter.detect(video_path, video_id, 0)

    @pytest.mark.asyncio
    async def test_detect_negative_duration_raises(
        self,
        adapter: PySceneDetectAdapter,
        video_path: Path,
        video_id: UUID,
    ) -> None:
        with pytest.raises(ValueError, match="duration_ms must be positive"):
            await adapter.detect(video_path, video_id, -1)

    @pytest.mark.asyncio
    async def test_detect_detector_revision_propagated(
        self,
        adapter: PySceneDetectAdapter,
        video_path: Path,
        video_id: UUID,
    ) -> None:
        with (
            patch(
                "galaxy_frog.adapters.vision.scene_detect._detect_scenes_sync",
                side_effect=_make_mock_detect_sync([(0.0, 5.0)]),
            ),
            patch(
                "galaxy_frog.adapters.vision.scene_detect._extract_frame_sync",
            ),
        ):
            detection, _ = await adapter.detect(video_path, video_id, 5_000)

        assert detection.detector_revision == "0.6.5"


# ---------------------------------------------------------------------------
# _scenedetect_version
# ---------------------------------------------------------------------------


class TestScenedetectVersion:
    def test_returns_string(self) -> None:
        with patch(
            "galaxy_frog.adapters.vision.scene_detect.metadata.version",
            return_value="0.7.0",
        ):
            assert _scenedetect_version() == "0.7.0"

    def test_returns_unknown_on_missing_package(self) -> None:
        from importlib.metadata import PackageNotFoundError

        with patch(
            "galaxy_frog.adapters.vision.scene_detect.metadata.version",
            side_effect=PackageNotFoundError("scenedetect"),
        ):
            assert _scenedetect_version() == "unknown"


# ---------------------------------------------------------------------------
# _import_scenedetect / _import_cv2 error paths
# ---------------------------------------------------------------------------


class TestImportHelpers:
    def test_import_scenedetect_raises_on_missing(self) -> None:
        with (
            patch.dict("sys.modules", {"scenedetect": None}),
            pytest.raises(ImportError, match="scenedetect is not installed"),
        ):
            _import_scenedetect()

    def test_import_cv2_raises_on_missing(self) -> None:
        with (
            patch.dict("sys.modules", {"cv2": None}),
            pytest.raises(ImportError, match="opencv-python-headless is not installed"),
        ):
            _import_cv2()

    def test_import_scenedetect_returns_tuple_when_available(self) -> None:
        # scenedetect is installed as a project dependency; success path must work.
        sm, ad, ov = _import_scenedetect()
        assert sm is not None
        assert ad is not None
        assert ov is not None

    def test_import_cv2_returns_module_when_available(self) -> None:
        # opencv-python-headless is installed as a project dependency.
        cv2 = _import_cv2()
        assert cv2 is not None


# ---------------------------------------------------------------------------
# _detect_scenes_sync
# ---------------------------------------------------------------------------


class TestDetectScenesSync:
    def test_returns_scene_list(self, video_path: Path) -> None:
        mock_tc = _FakeTimecode(0.0)
        mock_manager = MagicMock()
        mock_manager.get_scene_list.return_value = [(mock_tc, _FakeTimecode(5.0))]
        mock_video = MagicMock()
        mock_scene_manager_cls = MagicMock(return_value=mock_manager)
        mock_adaptive_cls = MagicMock()
        mock_open_video = MagicMock(return_value=mock_video)

        with patch(
            "galaxy_frog.adapters.vision.scene_detect._import_scenedetect",
            return_value=(mock_scene_manager_cls, mock_adaptive_cls, mock_open_video),
        ):
            result = _detect_scenes_sync(video_path)

        assert len(result) == 1
        mock_manager.detect_scenes.assert_called_once_with(mock_video, show_progress=False)


# ---------------------------------------------------------------------------
# _extract_frame_sync
# ---------------------------------------------------------------------------


class TestExtractFrameSync:
    def test_extracts_and_writes_frame(self, tmp_path: Path, video_path: Path) -> None:
        output = tmp_path / "frames" / "frame_00001.jpg"
        mock_cv2 = MagicMock()
        mock_cap = MagicMock()
        mock_cv2.VideoCapture.return_value = mock_cap
        mock_cap.read.return_value = (True, MagicMock())
        mock_cap.CAP_PROP_POS_MSEC = 0

        with patch(
            "galaxy_frog.adapters.vision.scene_detect._import_cv2",
            return_value=mock_cv2,
        ):
            _extract_frame_sync(video_path, 1_000, output)

        mock_cv2.imwrite.assert_called_once()
        mock_cap.release.assert_called_once()

    def test_raises_when_frame_unreadable(self, tmp_path: Path, video_path: Path) -> None:
        output = tmp_path / "frame.jpg"
        mock_cv2 = MagicMock()
        mock_cap = MagicMock()
        mock_cv2.VideoCapture.return_value = mock_cap
        mock_cap.read.return_value = (False, None)

        with (
            patch(
                "galaxy_frog.adapters.vision.scene_detect._import_cv2",
                return_value=mock_cv2,
            ),
            pytest.raises(RuntimeError, match="Could not read frame"),
        ):
            _extract_frame_sync(video_path, 999, output)

        mock_cap.release.assert_called_once()

    def test_creates_parent_directories(self, tmp_path: Path, video_path: Path) -> None:
        output = tmp_path / "deep" / "nested" / "frame.jpg"
        mock_cv2 = MagicMock()
        mock_cap = MagicMock()
        mock_cv2.VideoCapture.return_value = mock_cap
        mock_cap.read.return_value = (True, MagicMock())

        with patch(
            "galaxy_frog.adapters.vision.scene_detect._import_cv2",
            return_value=mock_cv2,
        ):
            _extract_frame_sync(video_path, 500, output)

        assert output.parent.exists()


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_adapter_satisfies_scene_detector_protocol(self, workspace: Path) -> None:
        from galaxy_frog.domain.vision.ports import SceneDetector

        adapter = PySceneDetectAdapter(workspace_dir=workspace, detector_revision="x")
        assert isinstance(adapter, SceneDetector)
