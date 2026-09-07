"""Local bounded audio acquisition orchestration."""

import asyncio
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from galaxy_frog.adapters.media.workspace import IsolatedMediaWorkspace
from galaxy_frog.application.media import AudioDownloader, AudioNormalizer
from galaxy_frog.domain.media import (
    AcquiredAudio,
    AudioAcquisitionError,
    AudioAcquisitionErrorCode,
    AudioAcquisitionLimits,
    AudioAcquisitionRequest,
)

Clock = Callable[[], datetime]


class LocalAudioAcquirer:
    """Coordinate bounded download and normalization in one isolated workspace."""

    def __init__(
        self,
        *,
        downloader: AudioDownloader,
        normalizer: AudioNormalizer,
        workspaces: IsolatedMediaWorkspace,
        limits: AudioAcquisitionLimits,
        retain_on_success: bool,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._downloader = downloader
        self._normalizer = normalizer
        self._workspaces = workspaces
        self._limits = limits
        self._retain_on_success = retain_on_success
        self._clock = clock
        self._semaphore = asyncio.Semaphore(limits.max_concurrency)

    async def acquire(self, request: AudioAcquisitionRequest) -> AcquiredAudio:
        if request.expected_duration_ms > self._limits.max_duration_ms:
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.DURATION_LIMIT_EXCEEDED,
                "The source duration exceeds the configured audio limit.",
            )

        workspace = self._workspaces.path_for(request.job_id, request.attempt)
        try:
            async with asyncio.timeout(self._limits.timeout_seconds):
                async with self._semaphore:
                    workspace = await asyncio.to_thread(
                        self._workspaces.prepare,
                        request.job_id,
                        request.attempt,
                    )
                    downloaded = await self._downloader.download(
                        request,
                        workspace,
                        self._limits,
                    )
                    self._require_direct_child(downloaded.path, workspace)
                    normalized = await self._normalizer.normalize(
                        downloaded,
                        workspace / "audio.wav",
                        self._limits,
                    )
                    self._require_direct_child(normalized.path, workspace)
                    if downloaded.path != normalized.path:
                        await asyncio.to_thread(downloaded.path.unlink, missing_ok=True)
                    return AcquiredAudio(
                        job_id=request.job_id,
                        attempt=request.attempt,
                        source=request.source,
                        fallback_reason=request.fallback_reason,
                        path=normalized.path,
                        start_ms=0,
                        end_ms=normalized.duration_ms,
                        size_bytes=normalized.size_bytes,
                        media_type=normalized.media_type,
                        codec=normalized.codec,
                        sample_rate_hz=normalized.sample_rate_hz,
                        channels=normalized.channels,
                        downloader=downloaded.downloader,
                        downloader_revision=downloaded.downloader_revision,
                        normalizer=normalized.normalizer,
                        normalizer_revision=normalized.normalizer_revision,
                        acquired_at=self._clock(),
                    )
        except TimeoutError as exc:
            await self._cleanup_failure(workspace)
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.TIMEOUT,
                "Audio acquisition exceeded its configured deadline.",
                retryable=True,
            ) from exc
        except asyncio.CancelledError:
            await self._cleanup_failure(workspace)
            raise
        except AudioAcquisitionError:
            await self._cleanup_failure(workspace)
            raise
        except OSError as exc:
            await self._cleanup_failure(workspace)
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.WORKSPACE_ERROR,
                "The isolated media workspace could not be updated.",
            ) from exc

    async def cleanup(self, artifact: AcquiredAudio) -> bool:
        """Delete the attempt workspace after processing unless retention is enabled."""

        if self._retain_on_success:
            return False
        workspace = self._workspaces.path_for(artifact.job_id, artifact.attempt)
        self._require_direct_child(artifact.path, workspace)
        return await asyncio.to_thread(self._workspaces.remove, workspace)

    async def _cleanup_failure(self, workspace: Path) -> None:
        with suppress(AudioAcquisitionError):
            await asyncio.to_thread(self._workspaces.remove, workspace)

    @staticmethod
    def _require_direct_child(path: Path, workspace: Path) -> None:
        if path.resolve().parent != workspace.resolve():
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.WORKSPACE_ERROR,
                "A media provider returned a file outside its isolated workspace.",
            )
