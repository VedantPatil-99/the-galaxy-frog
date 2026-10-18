"""PySceneDetect-backed scene boundary detection and frame extraction.

This adapter fulfils the ``SceneDetector`` protocol defined in
``galaxy_frog.domain.vision.ports``.  It detects visual scene changes using
PySceneDetect's ``AdaptiveDetector`` and extracts one representative frame per
scene boundary.  For scenes that span longer than ``periodic_interval_ms`` it
also inserts additional periodic sample frames so that dense-text slides are not
missed between cuts.

All frame images are written as JPEG files under ``workspace_dir`` and the
caller is responsible for cleaning up the directory after OCR is complete.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from importlib import metadata
from pathlib import Path
from typing import Any
from uuid import UUID

from galaxy_frog.domain.vision.models import (
    FrameSample,
    FrameSource,
    SceneBoundary,
    SceneDetectionResult,
)

_log = logging.getLogger(__name__)

# Label used as the detector name in SceneDetectionResult.
_DETECTOR_NAME = "pyscenedetect-adaptive"

# Default periodic sampling interval: one frame every 10 seconds.
_DEFAULT_PERIODIC_INTERVAL_MS = 10_000


# ---------------------------------------------------------------------------
# Internal: lazy-import helpers so the module can be imported without
# scenedetect/cv2 on the path (ImportError surfaces only at runtime).
# ---------------------------------------------------------------------------


def _import_scenedetect() -> tuple[Any, Any, Any]:
    """Return (SceneManager, AdaptiveDetector, open_video) from scenedetect."""
    try:
        from scenedetect import (  # pyright: ignore[reportMissingTypeStubs]
            AdaptiveDetector,
            SceneManager,
            open_video,  # pyright: ignore[reportUnknownVariableType]
        )
    except ImportError as exc:
        msg = (
            "scenedetect is not installed.  "
            "Add 'scenedetect[opencv]' to pyproject.toml dependencies."
        )
        raise ImportError(msg) from exc
    sm: Any = SceneManager
    ad: Any = AdaptiveDetector
    ov: Any = open_video  # pyright: ignore[reportUnknownVariableType]
    return sm, ad, ov


def _import_cv2() -> Any:
    """Return the cv2 module."""
    try:
        import cv2  # pyright: ignore[reportMissingModuleSource]

        return cv2
    except ImportError as exc:
        msg = (
            "opencv-python-headless is not installed.  "
            "Add 'opencv-python-headless' to pyproject.toml dependencies."
        )
        raise ImportError(msg) from exc


def _scenedetect_version() -> str:
    try:
        return metadata.version("scenedetect")
    except metadata.PackageNotFoundError:
        return "unknown"


# ---------------------------------------------------------------------------
# Synchronous helpers (run inside asyncio.to_thread)
# ---------------------------------------------------------------------------


def _detect_scenes_sync(
    video_path: Path,
) -> list[tuple[Any, Any]]:
    """Run PySceneDetect synchronously and return raw scene list."""
    SceneManager, AdaptiveDetector, open_video = _import_scenedetect()

    video = open_video(str(video_path))
    manager = SceneManager()
    manager.add_detector(AdaptiveDetector())
    manager.detect_scenes(video, show_progress=False)  # pyright: ignore[reportUnknownMemberType]
    scenes: list[tuple[Any, Any]] = manager.get_scene_list()  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
    return scenes


def _extract_frame_sync(
    video_path: Path,
    timestamp_ms: int,
    output_path: Path,
) -> None:
    """Extract a single JPEG frame at ``timestamp_ms`` using OpenCV."""
    cv2: Any = _import_cv2()

    cap = cv2.VideoCapture(str(video_path))
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, float(timestamp_ms))
        ok: bool
        frame: Any
        ok, frame = cap.read()
        if not ok or frame is None:
            msg = f"Could not read frame at {timestamp_ms} ms from {video_path}"
            raise RuntimeError(msg)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_path), frame)
    finally:
        cap.release()


def _build_sample_points(
    boundaries: list[tuple[int, int]],
    duration_ms: int,
    periodic_interval_ms: int,
) -> list[tuple[int, int, int, FrameSource]]:
    """Compute (capture_ms, start_ms, end_ms, source) for every frame to extract.

    For each scene boundary, one frame is taken at the scene's start.
    For scenes longer than ``periodic_interval_ms``, additional periodic frames
    are inserted at regular intervals within the scene.
    """
    samples: list[tuple[int, int, int, FrameSource]] = []

    for scene_start_ms, scene_end_ms in boundaries:
        # Scene-boundary frame at the start of each scene.
        samples.append((scene_start_ms, scene_start_ms, scene_end_ms, FrameSource.SCENE_BOUNDARY))

        # Periodic frames inside long scenes.
        scene_len = scene_end_ms - scene_start_ms
        if scene_len > periodic_interval_ms:
            intervals = scene_len // periodic_interval_ms
            for i in range(1, intervals):
                capture_ms = scene_start_ms + i * periodic_interval_ms
                samples.append((capture_ms, scene_start_ms, scene_end_ms, FrameSource.PERIODIC))

    return samples


def _timecode_to_ms(tc: object) -> int:
    """Convert a PySceneDetect FrameTimecode to integer milliseconds."""
    # FrameTimecode.get_seconds() returns a float.
    get_seconds = getattr(tc, "get_seconds", None)
    if callable(get_seconds):
        raw = get_seconds()
        seconds: float = float(raw)  # pyright: ignore[reportArgumentType]
        return max(0, int(seconds * 1000))
    msg = f"Unexpected timecode type: {type(tc)}"
    raise TypeError(msg)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class PySceneDetectAdapter:
    """Detect scene boundaries and sample frames from a local video file.

    Parameters
    ----------
    workspace_dir:
        Directory where extracted JPEG frames are written.  Created on first
        use if it does not exist.
    periodic_interval_ms:
        Milliseconds between periodic sample frames inside long static scenes.
        Defaults to 10 000 ms (10 seconds).
    detector_revision:
        Override the scenedetect package version string (useful in tests).
    """

    def __init__(
        self,
        *,
        workspace_dir: Path,
        periodic_interval_ms: int = _DEFAULT_PERIODIC_INTERVAL_MS,
        detector_revision: str | None = None,
    ) -> None:
        if periodic_interval_ms <= 0:
            msg = "periodic_interval_ms must be positive"
            raise ValueError(msg)
        if not workspace_dir.is_absolute():
            msg = "workspace_dir must be an absolute path"
            raise ValueError(msg)
        self._workspace_dir = workspace_dir
        self._periodic_interval_ms = periodic_interval_ms
        self._detector_revision = detector_revision or _scenedetect_version()

    async def detect(
        self,
        video_path: Path,
        video_id: object,
        duration_ms: int,
    ) -> tuple[SceneDetectionResult, tuple[FrameSample, ...]]:
        """Detect scene boundaries and extract frames; returns both results.

        The ``SceneDetector`` protocol only declares ``detect`` returning
        ``SceneDetectionResult``.  This concrete implementation returns a
        2-tuple ``(SceneDetectionResult, tuple[FrameSample, ...])`` so that
        callers which know the concrete type can receive extracted frame paths
        without a separate round-trip.  Protocol-typed callers will receive the
        tuple transparently (Python protocols are structural).

        Parameters
        ----------
        video_path:
            Absolute path to the local video file.
        video_id:
            Domain UUID of the video; stored in every ``FrameSample``.
        duration_ms:
            Total video duration in milliseconds; used as the end of the last
            scene boundary.
        """
        if not isinstance(video_id, UUID):
            msg = "video_id must be a UUID"
            raise TypeError(msg)
        if duration_ms <= 0:
            msg = "duration_ms must be positive"
            raise ValueError(msg)

        raw_scenes = await asyncio.to_thread(_detect_scenes_sync, video_path)

        boundaries = self._raw_to_boundaries(raw_scenes, duration_ms)
        detection_result = SceneDetectionResult(
            video_id=video_id,
            duration_ms=duration_ms,
            boundaries=tuple(boundaries),
            detector=_DETECTOR_NAME,
            detector_revision=self._detector_revision,
        )

        sample_points = _build_sample_points(
            [(b.start_ms, b.end_ms) for b in boundaries],
            duration_ms,
            self._periodic_interval_ms,
        )

        frames = await self._extract_frames(video_path, video_id, sample_points)
        return detection_result, frames

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _raw_to_boundaries(
        raw_scenes: Sequence[tuple[Any, Any]],
        duration_ms: int,
    ) -> list[SceneBoundary]:
        """Convert PySceneDetect scene list to ``SceneBoundary`` objects."""
        boundaries: list[SceneBoundary] = []
        for start_tc, end_tc in raw_scenes:
            start_ms = _timecode_to_ms(start_tc)
            raw_end_ms = _timecode_to_ms(end_tc)
            # Clamp end to duration and ensure the interval is valid.
            end_ms = min(raw_end_ms, duration_ms)
            if end_ms <= start_ms:
                continue
            boundaries.append(SceneBoundary(start_ms=start_ms, end_ms=end_ms, score=1.0))
        if not boundaries:
            # No cuts detected — treat the whole video as one scene.
            boundaries.append(SceneBoundary(start_ms=0, end_ms=duration_ms, score=1.0))
        return boundaries

    async def _extract_frames(
        self,
        video_path: Path,
        video_id: UUID,
        sample_points: list[tuple[int, int, int, FrameSource]],
    ) -> tuple[FrameSample, ...]:
        """Extract one JPEG per sample point and return ``FrameSample`` objects."""
        tasks = [
            asyncio.to_thread(
                _extract_frame_sync,
                video_path,
                capture_ms,
                self._frame_path(video_id, sequence),
            )
            for sequence, (capture_ms, _start_ms, _end_ms, _source) in enumerate(sample_points)
        ]
        await asyncio.gather(*tasks)

        samples: list[FrameSample] = []
        for sequence, (_capture_ms, start_ms, end_ms, source) in enumerate(sample_points):
            samples.append(
                FrameSample(
                    video_id=video_id,
                    sequence=sequence,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    path=self._frame_path(video_id, sequence),
                    source=source,
                )
            )
        return tuple(samples)

    def _frame_path(self, video_id: UUID, sequence: int) -> Path:
        return self._workspace_dir / str(video_id) / f"frame_{sequence:05d}.jpg"
