"""Bounded local acquisition and process isolation for existing Phase 4 components."""

import asyncio
import json
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from galaxy_frog.adapters.media.process import AsyncSubprocessRunner, CommandRunner, CommandTimedOut
from galaxy_frog.config import Settings
from galaxy_frog.domain.videos.models import VideoSourceKind
from galaxy_frog.domain.videos.records import VideoRecord
from galaxy_frog.domain.vision.models import (
    DeduplicatedFrameSet,
    FrameSample,
    FrameSource,
    SceneBoundary,
    SceneDetectionResult,
)
from galaxy_frog.domain.vision.visual import VisualFailureCode, VisualProviderError


class _Frame(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    sequence: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    source: str
    kept: bool
    dhash: str = Field(pattern="^[a-f0-9]{16}$")


class _Result(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    scenes: list[SceneBoundary] = Field(min_length=1, max_length=720)
    detector: str = Field(min_length=1, max_length=64)
    revision: str = Field(min_length=1, max_length=255)
    frames: list[_Frame] = Field(min_length=1, max_length=720)


class LocalFramePipeline:
    def __init__(self, settings: Settings, runner: CommandRunner | None = None) -> None:
        self._settings = settings
        self._runner = runner or AsyncSubprocessRunner()

    async def extract(
        self, video: VideoRecord
    ) -> tuple[SceneDetectionResult, tuple[FrameSample, ...], DeduplicatedFrameSet, dict[int, str]]:
        settings = self._settings
        duration = video.metadata.duration_ms
        if (
            video.metadata.reference.kind != VideoSourceKind.YOUTUBE
            or duration > settings.media_max_duration_seconds * 1000
        ):
            raise VisualProviderError(VisualFailureCode.UNAVAILABLE)
        workspace = (settings.vision_workspace_root / str(video.video_id)).resolve()
        await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=True)
        try:
            # A single non-merged low-resolution stream bounds acquisition and decoder memory.
            downloaded = await self._runner.run(
                (
                    sys.executable,
                    "-m",
                    "yt_dlp",
                    "--no-playlist",
                    "--no-progress",
                    "--no-warnings",
                    "--quiet",
                    "--js-runtimes",
                    settings.yt_dlp_js_runtime,
                    "--format",
                    "best[height<=480][ext=mp4]/best[height<=480]",
                    "--max-filesize",
                    str(settings.media_max_download_bytes),
                    "--match-filters",
                    f"duration <= {settings.media_max_duration_seconds}",
                    "--socket-timeout",
                    "15",
                    "--retries",
                    "1",
                    "--output",
                    str(workspace / "source.%(ext)s"),
                    video.metadata.reference.canonical_url,
                ),
                cwd=workspace,
                timeout_seconds=settings.media_timeout_seconds,
            )
            if downloaded.return_code != 0:
                raise VisualProviderError(VisualFailureCode.UNAVAILABLE)
            source = await asyncio.to_thread(self._source, workspace)
            request, result = workspace / "request.json", workspace / "result.json"
            # Delete only a known stale result; a failed child cannot reuse previous output.
            await asyncio.to_thread(result.unlink, missing_ok=True)
            await asyncio.to_thread(
                request.write_text,
                json.dumps(
                    {
                        "source": str(source),
                        "workspace": str(settings.vision_workspace_root.resolve()),
                        "video_id": str(video.video_id),
                        "duration_ms": duration,
                        "max_frames": settings.vision_max_frames,
                        "result": str(result),
                    }
                ),
                encoding="utf-8",
            )
            process = await self._runner.run(
                (sys.executable, "-m", "galaxy_frog.entrypoints.frames", str(request)),
                cwd=workspace,
                timeout_seconds=settings.vision_timeout_seconds,
            )
            if process.return_code != 0:
                raise VisualProviderError(VisualFailureCode.UNAVAILABLE)
            parsed = await asyncio.to_thread(self._result, result)
            if len(parsed.frames) > settings.vision_max_frames or [
                frame.sequence for frame in parsed.frames
            ] != list(range(len(parsed.frames))):
                raise ValueError("invalid frame budget or sequence")
            scenes = SceneDetectionResult(
                video.video_id, duration, tuple(parsed.scenes), parsed.detector, parsed.revision
            )
            if any(scene.end_ms > duration for scene in parsed.scenes):
                raise ValueError("scene outside source interval")
            frames = tuple(
                FrameSample(
                    video.video_id,
                    frame.sequence,
                    frame.start_ms,
                    frame.end_ms,
                    workspace / f"frame_{frame.sequence:05d}.jpg",
                    FrameSource(frame.source),
                )
                for frame in parsed.frames
            )
            if any(frame.end_ms > duration for frame in frames):
                raise ValueError("frame outside source interval")
            kept = tuple(
                frame
                for frame, metadata in zip(frames, parsed.frames, strict=True)
                if metadata.kept
            )
            await asyncio.to_thread(source.unlink)
            return (
                scenes,
                frames,
                DeduplicatedFrameSet(kept, len(frames) - len(kept), "dhash", 4),
                {frame.sequence: frame.dhash for frame in parsed.frames},
            )
        except CommandTimedOut as exc:
            raise VisualProviderError(VisualFailureCode.TIMEOUT) from exc
        except (OSError, ValueError) as exc:
            raise VisualProviderError(VisualFailureCode.INVALID_OUTPUT) from exc

    def _source(self, workspace: Path) -> Path:
        sources = tuple(
            path
            for path in workspace.glob("source.*")
            if path.suffix not in {".part", ".ytdl"} and path.is_file()
        )
        if len(sources) != 1:
            raise ValueError("expected one bounded video file")
        source = sources[0].resolve()
        if (
            not source.is_relative_to(workspace)
            or not 0 < source.stat().st_size <= self._settings.media_max_download_bytes
        ):
            raise ValueError("invalid video download")
        return source

    @staticmethod
    def _result(path: Path) -> _Result:
        if path.stat().st_size > 262144:
            raise ValueError("frame manifest exceeds its byte budget")
        return _Result.model_validate_json(path.read_text(encoding="utf-8"))
