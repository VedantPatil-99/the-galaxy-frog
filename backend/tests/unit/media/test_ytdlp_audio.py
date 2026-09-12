"""Behavior coverage for bounded yt-dlp audio downloads."""

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from galaxy_frog.adapters.media.process import CommandResult, CommandTimedOut
from galaxy_frog.adapters.media.yt_dlp import YtDlpAudioDownloader
from galaxy_frog.domain.media import (
    AudioAcquisitionError,
    AudioAcquisitionErrorCode,
    AudioAcquisitionLimits,
    AudioAcquisitionRequest,
    AudioFallbackReason,
)
from galaxy_frog.domain.videos.models import SourceReference, VideoSourceKind

REFERENCE = SourceReference(
    VideoSourceKind.YOUTUBE,
    "dQw4w9WgXcQ",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
)
LIMITS = AudioAcquisitionLimits(
    max_duration_ms=7_200_000,
    max_download_bytes=20,
    max_output_bytes=40,
    timeout_seconds=15,
    max_concurrency=1,
)


def request(reference: SourceReference = REFERENCE) -> AudioAcquisitionRequest:
    return AudioAcquisitionRequest(
        job_id=uuid4(),
        attempt=1,
        source=reference,
        expected_duration_ms=1000,
        fallback_reason=AudioFallbackReason.CAPTIONS_UNAVAILABLE,
    )


class RecordingRunner:
    def __init__(
        self,
        *,
        return_code: int = 0,
        stderr: str = "",
        files: tuple[tuple[str, bytes], ...] = (("source.webm", b"audio"),),
        failure: BaseException | None = None,
    ) -> None:
        self.return_code = return_code
        self.stderr = stderr
        self.files = files
        self.failure = failure
        self.calls: list[tuple[tuple[str, ...], Path, float]] = []

    async def run(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> CommandResult:
        command = tuple(arguments)
        self.calls.append((command, cwd, timeout_seconds))
        if self.failure is not None:
            raise self.failure
        for name, content in self.files:
            (cwd / name).write_bytes(content)
        return CommandResult(command, self.return_code, "", self.stderr)


@pytest.mark.asyncio
async def test_download_uses_bounded_argument_array_and_ignores_printed_paths(
    tmp_path: Path,
) -> None:
    runner = RecordingRunner()
    downloader = YtDlpAudioDownloader(
        runner=runner,
        python_executable="python",
        revision="2026.08.19",
    )

    result = await downloader.download(request(), tmp_path, LIMITS)

    arguments, cwd, timeout_seconds = runner.calls[0]
    assert arguments[:3] == ("python", "-m", "yt_dlp")
    assert "--no-playlist" in arguments
    assert arguments[arguments.index("--paths") + 1] == str(tmp_path)
    assert arguments[arguments.index("--max-filesize") + 1] == "20"
    assert arguments[arguments.index("--match-filters") + 1] == "duration <= 7200"
    assert arguments[-1] == REFERENCE.canonical_url
    assert cwd == tmp_path
    assert timeout_seconds == 15
    assert result.path == (tmp_path / "source.webm").resolve()
    assert result.size_bytes == 5
    assert result.downloader_revision == "2026.08.19"


@pytest.mark.asyncio
async def test_download_rejects_an_unsupported_source(tmp_path: Path) -> None:
    local = SourceReference(
        VideoSourceKind.LOCAL_FILE,
        "fixture",
        "file:///C:/fixture.mp4",
    )

    with pytest.raises(AudioAcquisitionError) as captured:
        await YtDlpAudioDownloader(
            runner=RecordingRunner(),
            revision="test",
        ).download(request(local), tmp_path, LIMITS)

    assert captured.value.code is AudioAcquisitionErrorCode.UNSUPPORTED_SOURCE


@pytest.mark.parametrize(
    ("runner", "code", "retryable"),
    [
        (
            RecordingRunner(failure=CommandTimedOut("yt-dlp")),
            AudioAcquisitionErrorCode.TIMEOUT,
            True,
        ),
        (
            RecordingRunner(failure=FileNotFoundError("python")),
            AudioAcquisitionErrorCode.DOWNLOAD_FAILED,
            False,
        ),
        (
            RecordingRunner(return_code=1, stderr="larger than max-filesize"),
            AudioAcquisitionErrorCode.DOWNLOAD_SIZE_LIMIT_EXCEEDED,
            False,
        ),
        (
            RecordingRunner(return_code=1, stderr="network unavailable"),
            AudioAcquisitionErrorCode.DOWNLOAD_FAILED,
            True,
        ),
    ],
)
@pytest.mark.asyncio
async def test_download_translates_tool_failures(
    tmp_path: Path,
    runner: RecordingRunner,
    code: AudioAcquisitionErrorCode,
    retryable: bool,
) -> None:
    with pytest.raises(AudioAcquisitionError) as captured:
        await YtDlpAudioDownloader(runner=runner, revision="test").download(
            request(),
            tmp_path,
            LIMITS,
        )

    assert captured.value.code is code
    assert captured.value.retryable is retryable


@pytest.mark.parametrize(
    ("files", "code"),
    [
        ((), AudioAcquisitionErrorCode.DOWNLOAD_FAILED),
        (
            (("source.webm", b"one"), ("source.m4a", b"two")),
            AudioAcquisitionErrorCode.DOWNLOAD_FAILED,
        ),
        (
            (("source.webm", b"a" * 21),),
            AudioAcquisitionErrorCode.DOWNLOAD_SIZE_LIMIT_EXCEEDED,
        ),
        ((("source.webm", b""),), AudioAcquisitionErrorCode.DOWNLOAD_FAILED),
    ],
)
@pytest.mark.asyncio
async def test_download_validates_the_actual_output(
    tmp_path: Path,
    files: tuple[tuple[str, bytes], ...],
    code: AudioAcquisitionErrorCode,
) -> None:
    with pytest.raises(AudioAcquisitionError) as captured:
        await YtDlpAudioDownloader(
            runner=RecordingRunner(files=files),
            revision="test",
        ).download(request(), tmp_path, LIMITS)

    assert captured.value.code is code


@pytest.mark.asyncio
async def test_download_ignores_partial_control_files(tmp_path: Path) -> None:
    runner = RecordingRunner(
        files=(("source.webm", b"audio"), ("source.webm.part", b"partial"), ("source.ytdl", b"x"))
    )

    result = await YtDlpAudioDownloader(runner=runner, revision="test").download(
        request(),
        tmp_path,
        replace(LIMITS, timeout_seconds=0.5),
    )

    assert result.path.name == "source.webm"
    arguments = runner.calls[0][0]
    assert arguments[arguments.index("--socket-timeout") + 1] == "1"
