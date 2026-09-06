"""Bounded yt-dlp audio downloader behind a provider-neutral interface."""

import asyncio
import sys
from importlib.metadata import version
from pathlib import Path

from galaxy_frog.adapters.media.process import CommandRunner, CommandTimedOut
from galaxy_frog.application.media import DownloadedAudio
from galaxy_frog.domain.media import (
    AudioAcquisitionError,
    AudioAcquisitionErrorCode,
    AudioAcquisitionLimits,
    AudioAcquisitionRequest,
)
from galaxy_frog.domain.videos.models import VideoSourceKind


class YtDlpAudioDownloader:
    """Download one best-audio stream without invoking a shell or a playlist."""

    def __init__(
        self,
        *,
        runner: CommandRunner,
        python_executable: str = sys.executable,
        revision: str | None = None,
    ) -> None:
        self._runner = runner
        self._python_executable = python_executable
        self._revision = revision or version("yt-dlp")

    async def download(
        self,
        request: AudioAcquisitionRequest,
        workspace: Path,
        limits: AudioAcquisitionLimits,
    ) -> DownloadedAudio:
        if request.source.kind is not VideoSourceKind.YOUTUBE:
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.UNSUPPORTED_SOURCE,
                "The configured audio downloader does not support this video source.",
            )

        socket_timeout = max(1, min(30, int(limits.timeout_seconds)))
        arguments = (
            self._python_executable,
            "-m",
            "yt_dlp",
            "--no-playlist",
            "--quiet",
            "--no-warnings",
            "--no-progress",
            "--format",
            "bestaudio/best",
            "--paths",
            str(workspace),
            "--output",
            "source.%(ext)s",
            "--max-filesize",
            str(limits.max_download_bytes),
            "--match-filters",
            f"duration <= {limits.max_duration_ms / 1000:g}",
            "--socket-timeout",
            str(socket_timeout),
            "--retries",
            "3",
            "--print",
            "after_move:filepath",
            request.source.canonical_url,
        )
        try:
            result = await self._runner.run(
                arguments,
                cwd=workspace,
                timeout_seconds=limits.timeout_seconds,
            )
        except CommandTimedOut as exc:
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.TIMEOUT,
                "Audio download exceeded its configured deadline.",
                retryable=True,
            ) from exc
        except OSError as exc:
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.DOWNLOAD_FAILED,
                "The local audio downloader could not be started.",
            ) from exc

        if result.return_code != 0:
            if self._reports_size_limit(result.stderr):
                raise AudioAcquisitionError(
                    AudioAcquisitionErrorCode.DOWNLOAD_SIZE_LIMIT_EXCEEDED,
                    "The source audio exceeds the configured download-size limit.",
                )
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.DOWNLOAD_FAILED,
                "The source audio could not be downloaded.",
                retryable=True,
            )

        return await asyncio.to_thread(self._locate_source, workspace, limits)

    def _locate_source(
        self,
        workspace: Path,
        limits: AudioAcquisitionLimits,
    ) -> DownloadedAudio:
        candidates = tuple(
            path
            for path in workspace.glob("source.*")
            if path.is_file() and path.suffix not in {".part", ".ytdl"}
        )
        if len(candidates) != 1:
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.DOWNLOAD_FAILED,
                "The audio downloader did not produce one source media file.",
            )
        source_path = candidates[0].resolve()
        size_bytes = source_path.stat().st_size
        if size_bytes > limits.max_download_bytes:
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.DOWNLOAD_SIZE_LIMIT_EXCEEDED,
                "The source audio exceeds the configured download-size limit.",
            )
        if size_bytes <= 0:
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.DOWNLOAD_FAILED,
                "The audio downloader produced an empty source media file.",
            )
        return DownloadedAudio(
            path=source_path,
            size_bytes=size_bytes,
            downloader="yt-dlp",
            downloader_revision=self._revision,
        )

    @staticmethod
    def _reports_size_limit(stderr: str) -> bool:
        message = stderr.casefold()
        return "max-filesize" in message or "larger than max" in message
