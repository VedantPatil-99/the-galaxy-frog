"""Behavior coverage for bounded local audio acquisition orchestration."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from galaxy_frog.adapters.media.local import LocalAudioAcquirer
from galaxy_frog.adapters.media.workspace import IsolatedMediaWorkspace
from galaxy_frog.application.media import DownloadedAudio, NormalizedAudio
from galaxy_frog.domain.media import (
    AudioAcquisitionError,
    AudioAcquisitionErrorCode,
    AudioAcquisitionLimits,
    AudioAcquisitionRequest,
    AudioFallbackReason,
)
from galaxy_frog.domain.videos.models import SourceReference, VideoSourceKind

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)
REFERENCE = SourceReference(
    VideoSourceKind.YOUTUBE,
    "dQw4w9WgXcQ",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
)
LIMITS = AudioAcquisitionLimits(
    max_duration_ms=10_000,
    max_download_bytes=100,
    max_output_bytes=100,
    timeout_seconds=1,
    max_concurrency=1,
)


def request(*, duration_ms: int = 1000) -> AudioAcquisitionRequest:
    return AudioAcquisitionRequest(
        job_id=uuid4(),
        attempt=1,
        source=REFERENCE,
        expected_duration_ms=duration_ms,
        fallback_reason=AudioFallbackReason.CAPTIONS_UNUSABLE,
    )


class FixtureDownloader:
    def __init__(self) -> None:
        self.failure: AudioAcquisitionError | None = None
        self.delay = 0.0
        self.outside_path: Path | None = None
        self.active = 0
        self.max_active = 0

    async def download(
        self,
        request: AudioAcquisitionRequest,
        workspace: Path,
        limits: AudioAcquisitionLimits,
    ) -> DownloadedAudio:
        del request, limits
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.failure is not None:
                raise self.failure
            if self.delay:
                await asyncio.sleep(self.delay)
            path = self.outside_path or workspace / "source.webm"
            await asyncio.to_thread(path.write_bytes, b"source")
            return DownloadedAudio(path.resolve(), 6, "yt-dlp", "2026.08.19")
        finally:
            self.active -= 1


class FixtureNormalizer:
    def __init__(self) -> None:
        self.outside_path: Path | None = None
        self.reuse_source_path = False

    async def normalize(
        self,
        source: DownloadedAudio,
        output_path: Path,
        limits: AudioAcquisitionLimits,
    ) -> NormalizedAudio:
        del limits
        path = source.path if self.reuse_source_path else self.outside_path or output_path
        await asyncio.to_thread(path.write_bytes, b"normalized")
        return NormalizedAudio(
            path=path.resolve(),
            duration_ms=1000,
            size_bytes=10,
            media_type="audio/wav",
            codec="pcm_s16le",
            sample_rate_hz=16_000,
            channels=1,
            normalizer="ffmpeg",
            normalizer_revision="9.0.1",
        )


def acquirer(
    tmp_path: Path,
    downloader: FixtureDownloader | None = None,
    normalizer: FixtureNormalizer | None = None,
    *,
    limits: AudioAcquisitionLimits = LIMITS,
    retain_on_success: bool = False,
) -> LocalAudioAcquirer:
    return LocalAudioAcquirer(
        downloader=downloader or FixtureDownloader(),
        normalizer=normalizer or FixtureNormalizer(),
        workspaces=IsolatedMediaWorkspace(tmp_path / "media"),
        limits=limits,
        retain_on_success=retain_on_success,
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_acquire_preserves_interval_fallback_and_tool_provenance(tmp_path: Path) -> None:
    audio_request = request()
    service = acquirer(tmp_path)

    artifact = await service.acquire(audio_request)

    assert artifact.job_id == audio_request.job_id
    assert artifact.source == REFERENCE
    assert artifact.fallback_reason is AudioFallbackReason.CAPTIONS_UNUSABLE
    assert (artifact.start_ms, artifact.end_ms) == (0, 1000)
    assert artifact.acquired_at == NOW
    assert (artifact.downloader_revision, artifact.normalizer_revision) == (
        "2026.08.19",
        "9.0.1",
    )
    assert artifact.path.read_bytes() == b"normalized"
    assert not (artifact.path.parent / "source.webm").exists()

    assert await service.cleanup(artifact) is True
    assert not artifact.path.parent.exists()


@pytest.mark.asyncio
async def test_cleanup_retains_successful_audio_when_configured(tmp_path: Path) -> None:
    service = acquirer(tmp_path, retain_on_success=True)
    artifact = await service.acquire(request())

    assert await service.cleanup(artifact) is False
    assert artifact.path.exists()


@pytest.mark.asyncio
async def test_acquire_keeps_a_normalizer_output_that_reuses_the_source_path(
    tmp_path: Path,
) -> None:
    normalizer = FixtureNormalizer()
    normalizer.reuse_source_path = True

    artifact = await acquirer(tmp_path, normalizer=normalizer).acquire(request())

    assert artifact.path.name == "source.webm"
    assert artifact.path.exists()


@pytest.mark.asyncio
async def test_acquire_rejects_metadata_beyond_the_duration_limit(tmp_path: Path) -> None:
    service = acquirer(tmp_path)

    with pytest.raises(AudioAcquisitionError) as captured:
        await service.acquire(request(duration_ms=10_001))

    assert captured.value.code is AudioAcquisitionErrorCode.DURATION_LIMIT_EXCEEDED
    assert not (tmp_path / "media").exists()


@pytest.mark.parametrize("provider", ["downloader", "normalizer"])
@pytest.mark.asyncio
async def test_acquire_rejects_provider_paths_outside_the_workspace(
    tmp_path: Path,
    provider: str,
) -> None:
    downloader = FixtureDownloader()
    normalizer = FixtureNormalizer()
    outside = tmp_path / "outside.webm"
    if provider == "downloader":
        downloader.outside_path = outside
    else:
        normalizer.outside_path = outside
    audio_request = request()
    service = acquirer(tmp_path, downloader, normalizer)

    with pytest.raises(AudioAcquisitionError) as captured:
        await service.acquire(audio_request)

    assert captured.value.code is AudioAcquisitionErrorCode.WORKSPACE_ERROR
    workspace = tmp_path / "media" / str(audio_request.job_id) / "attempt-1"
    assert not workspace.exists()
    assert outside.exists()


@pytest.mark.asyncio
async def test_acquire_cleans_the_workspace_after_a_provider_failure(tmp_path: Path) -> None:
    downloader = FixtureDownloader()
    downloader.failure = AudioAcquisitionError(
        AudioAcquisitionErrorCode.DOWNLOAD_FAILED,
        "Download failed.",
        retryable=True,
    )
    audio_request = request()

    with pytest.raises(AudioAcquisitionError) as captured:
        await acquirer(tmp_path, downloader).acquire(audio_request)

    assert captured.value is downloader.failure
    assert not (tmp_path / "media" / str(audio_request.job_id) / "attempt-1").exists()


@pytest.mark.asyncio
async def test_acquire_has_one_deadline_for_the_complete_operation(tmp_path: Path) -> None:
    downloader = FixtureDownloader()
    downloader.delay = 1
    limits = AudioAcquisitionLimits(10_000, 100, 100, 0.05, 1)
    audio_request = request()

    with pytest.raises(AudioAcquisitionError) as captured:
        await acquirer(tmp_path, downloader, limits=limits).acquire(audio_request)

    assert captured.value.code is AudioAcquisitionErrorCode.TIMEOUT
    assert captured.value.retryable is True
    assert not (tmp_path / "media" / str(audio_request.job_id) / "attempt-1").exists()


@pytest.mark.asyncio
async def test_acquire_cleans_the_workspace_when_cancelled(tmp_path: Path) -> None:
    downloader = FixtureDownloader()
    downloader.delay = 1
    audio_request = request()
    task = asyncio.create_task(acquirer(tmp_path, downloader).acquire(audio_request))
    await asyncio.sleep(0.05)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (tmp_path / "media" / str(audio_request.job_id) / "attempt-1").exists()


@pytest.mark.asyncio
async def test_acquire_enforces_the_configured_concurrency_limit(tmp_path: Path) -> None:
    downloader = FixtureDownloader()
    downloader.delay = 0.05
    service = acquirer(tmp_path, downloader)

    first, second = await asyncio.gather(service.acquire(request()), service.acquire(request()))

    assert downloader.max_active == 1
    assert first.job_id != second.job_id


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_hide_the_provider_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    downloader = FixtureDownloader()
    downloader.failure = AudioAcquisitionError(
        AudioAcquisitionErrorCode.DOWNLOAD_FAILED,
        "Download failed.",
    )
    workspaces = IsolatedMediaWorkspace(tmp_path / "media")
    service = LocalAudioAcquirer(
        downloader=downloader,
        normalizer=FixtureNormalizer(),
        workspaces=workspaces,
        limits=LIMITS,
        retain_on_success=False,
    )

    def fail_cleanup(_path: Path) -> bool:
        raise AudioAcquisitionError(
            AudioAcquisitionErrorCode.WORKSPACE_ERROR,
            "Cleanup failed.",
        )

    monkeypatch.setattr(workspaces, "remove", fail_cleanup)

    with pytest.raises(AudioAcquisitionError) as captured:
        await service.acquire(request())

    assert captured.value is downloader.failure


@pytest.mark.asyncio
async def test_acquire_translates_a_source_cleanup_filesystem_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_unlink = Path.unlink

    def fail_source_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path.name == "source.webm":
            raise OSError("locked")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_source_unlink)

    with pytest.raises(AudioAcquisitionError) as captured:
        await acquirer(tmp_path).acquire(request())

    assert captured.value.code is AudioAcquisitionErrorCode.WORKSPACE_ERROR
