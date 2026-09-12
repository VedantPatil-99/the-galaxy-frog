"""Behavior coverage for FFmpeg normalization and ffprobe verification."""

# pyright: reportPrivateUsage=false

import asyncio
import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from galaxy_frog.adapters.media.ffmpeg import FfmpegAudioNormalizer
from galaxy_frog.adapters.media.process import CommandResult, CommandTimedOut
from galaxy_frog.application.media import DownloadedAudio
from galaxy_frog.domain.media import (
    AudioAcquisitionError,
    AudioAcquisitionErrorCode,
    AudioAcquisitionLimits,
)

LIMITS = AudioAcquisitionLimits(
    max_duration_ms=10_000,
    max_download_bytes=100,
    max_output_bytes=100,
    timeout_seconds=15,
    max_concurrency=1,
)


def probe_payload(
    *,
    duration: object = "1.000000",
    size: object = "5",
    codec: object = "pcm_s16le",
    sample_rate: object = "16000",
    channels: object = 1,
) -> str:
    return json.dumps(
        {
            "streams": [
                {
                    "codec_name": codec,
                    "sample_rate": sample_rate,
                    "channels": channels,
                }
            ],
            "format": {"duration": duration, "size": size},
        }
    )


class MediaRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], Path, float]] = []
        self.source_probe = probe_payload(codec="opus", sample_rate="48000", channels=2)
        self.output_probe = probe_payload()
        self.version_result = CommandResult(
            (), 0, "ffmpeg version 9.0.1-full_build Copyright\n", ""
        )
        self.normalize_result = CommandResult((), 0, "", "")
        self.probe_return_code = 0
        self.failure_for: str | None = None
        self.failure: BaseException | None = None
        self.output_bytes = b"normalized"

    async def run(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> CommandResult:
        command = tuple(arguments)
        self.calls.append((command, cwd, timeout_seconds))
        executable = command[0]
        if self.failure_for == executable and self.failure is not None:
            raise self.failure
        if executable == "ffprobe":
            document = (
                self.output_probe if Path(command[-1]).name == "audio.wav" else self.source_probe
            )
            return CommandResult(command, self.probe_return_code, document, "probe failed")
        if command[1:] == ("-version",):
            return replace(self.version_result, arguments=command)
        await asyncio.to_thread(Path(command[-1]).write_bytes, self.output_bytes)
        return replace(self.normalize_result, arguments=command)


def source(tmp_path: Path, *, size_bytes: int = 5) -> DownloadedAudio:
    path = tmp_path / "source.webm"
    path.write_bytes(b"audio")
    return DownloadedAudio(path.resolve(), size_bytes, "yt-dlp", "test")


@pytest.mark.asyncio
async def test_normalize_probes_limits_and_produces_verified_pcm(tmp_path: Path) -> None:
    runner = MediaRunner()
    normalizer = FfmpegAudioNormalizer(runner=runner)

    result = await normalizer.normalize(source(tmp_path), tmp_path / "audio.wav", LIMITS)

    assert result.path == (tmp_path / "audio.wav").resolve()
    assert result.duration_ms == 1000
    assert result.size_bytes == len(runner.output_bytes)
    assert (result.codec, result.sample_rate_hz, result.channels) == ("pcm_s16le", 16_000, 1)
    assert result.normalizer_revision == "9.0.1-full_build"
    normalize_arguments = next(call[0] for call in runner.calls if "-c:a" in call[0])
    assert normalize_arguments[0] == "ffmpeg"
    assert normalize_arguments[normalize_arguments.index("-fs") + 1] == "100"
    assert normalize_arguments[-1] == str(tmp_path / "audio.wav")
    assert all(call[1] == tmp_path for call in runner.calls)
    assert all(call[2] == 15 for call in runner.calls)


@pytest.mark.parametrize(
    ("source_size", "source_probe", "code"),
    [
        (101, probe_payload(codec="opus"), AudioAcquisitionErrorCode.DOWNLOAD_SIZE_LIMIT_EXCEEDED),
        (
            5,
            probe_payload(size="101", codec="opus"),
            AudioAcquisitionErrorCode.DOWNLOAD_SIZE_LIMIT_EXCEEDED,
        ),
        (
            5,
            probe_payload(duration="11", codec="opus"),
            AudioAcquisitionErrorCode.DURATION_LIMIT_EXCEEDED,
        ),
    ],
)
@pytest.mark.asyncio
async def test_normalize_rechecks_downloaded_media_limits(
    tmp_path: Path,
    source_size: int,
    source_probe: str,
    code: AudioAcquisitionErrorCode,
) -> None:
    runner = MediaRunner()
    runner.source_probe = source_probe

    with pytest.raises(AudioAcquisitionError) as captured:
        await FfmpegAudioNormalizer(runner=runner).normalize(
            source(tmp_path, size_bytes=source_size),
            tmp_path / "audio.wav",
            LIMITS,
        )

    assert captured.value.code is code


@pytest.mark.parametrize(
    ("failure_for", "failure", "code", "retryable"),
    [
        ("ffprobe", CommandTimedOut("ffprobe"), AudioAcquisitionErrorCode.TIMEOUT, True),
        ("ffprobe", FileNotFoundError("ffprobe"), AudioAcquisitionErrorCode.PROBE_FAILED, False),
        ("ffmpeg", CommandTimedOut("ffmpeg"), AudioAcquisitionErrorCode.TIMEOUT, True),
        (
            "ffmpeg",
            FileNotFoundError("ffmpeg"),
            AudioAcquisitionErrorCode.NORMALIZATION_FAILED,
            False,
        ),
    ],
)
@pytest.mark.asyncio
async def test_normalize_translates_process_start_and_timeout_failures(
    tmp_path: Path,
    failure_for: str,
    failure: BaseException,
    code: AudioAcquisitionErrorCode,
    retryable: bool,
) -> None:
    runner = MediaRunner()
    runner.failure_for = failure_for
    runner.failure = failure

    with pytest.raises(AudioAcquisitionError) as captured:
        await FfmpegAudioNormalizer(runner=runner).normalize(
            source(tmp_path),
            tmp_path / "audio.wav",
            LIMITS,
        )

    assert captured.value.code is code
    assert captured.value.retryable is retryable


@pytest.mark.parametrize(
    ("version_result", "normalize_result", "code"),
    [
        (
            CommandResult((), 1, "", "missing"),
            CommandResult((), 0, "", ""),
            AudioAcquisitionErrorCode.NORMALIZATION_FAILED,
        ),
        (
            CommandResult((), 0, "unexpected", ""),
            CommandResult((), 0, "", ""),
            AudioAcquisitionErrorCode.NORMALIZATION_FAILED,
        ),
        (
            CommandResult((), 0, "ffmpeg version    ", ""),
            CommandResult((), 0, "", ""),
            AudioAcquisitionErrorCode.NORMALIZATION_FAILED,
        ),
        (
            CommandResult((), 0, "ffmpeg version test", ""),
            CommandResult((), 1, "", "invalid source"),
            AudioAcquisitionErrorCode.NORMALIZATION_FAILED,
        ),
    ],
)
@pytest.mark.asyncio
async def test_normalize_rejects_unsuccessful_tool_results(
    tmp_path: Path,
    version_result: CommandResult,
    normalize_result: CommandResult,
    code: AudioAcquisitionErrorCode,
) -> None:
    runner = MediaRunner()
    runner.version_result = version_result
    runner.normalize_result = normalize_result

    with pytest.raises(AudioAcquisitionError) as captured:
        await FfmpegAudioNormalizer(runner=runner).normalize(
            source(tmp_path),
            tmp_path / "audio.wav",
            LIMITS,
        )

    assert captured.value.code is code


@pytest.mark.asyncio
async def test_normalize_rejects_a_nonzero_probe_result(tmp_path: Path) -> None:
    runner = MediaRunner()
    runner.probe_return_code = 1

    with pytest.raises(AudioAcquisitionError) as captured:
        await FfmpegAudioNormalizer(runner=runner).normalize(
            source(tmp_path),
            tmp_path / "audio.wav",
            LIMITS,
        )

    assert captured.value.code is AudioAcquisitionErrorCode.PROBE_FAILED


@pytest.mark.parametrize(
    ("output_bytes", "max_output_bytes", "code"),
    [
        (b"", 100, AudioAcquisitionErrorCode.NORMALIZATION_FAILED),
        (b"x" * 101, 100, AudioAcquisitionErrorCode.OUTPUT_SIZE_LIMIT_EXCEEDED),
    ],
)
@pytest.mark.asyncio
async def test_normalize_validates_actual_output_size(
    tmp_path: Path,
    output_bytes: bytes,
    max_output_bytes: int,
    code: AudioAcquisitionErrorCode,
) -> None:
    runner = MediaRunner()
    runner.output_bytes = output_bytes

    with pytest.raises(AudioAcquisitionError) as captured:
        await FfmpegAudioNormalizer(runner=runner).normalize(
            source(tmp_path),
            tmp_path / "audio.wav",
            replace(LIMITS, max_output_bytes=max_output_bytes),
        )

    assert captured.value.code is code


@pytest.mark.asyncio
async def test_normalize_rejects_a_missing_output_file(tmp_path: Path) -> None:
    runner = MediaRunner()
    runner.output_bytes = b"normalized"

    async def run_without_output(
        arguments: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> CommandResult:
        command = tuple(arguments)
        if command[0] == "ffprobe":
            return CommandResult(command, 0, runner.source_probe, "")
        if command[1:] == ("-version",):
            return CommandResult(command, 0, "ffmpeg version test", "")
        return CommandResult(command, 0, "", "")

    runner.run = run_without_output  # type: ignore[method-assign]

    with pytest.raises(AudioAcquisitionError) as captured:
        await FfmpegAudioNormalizer(runner=runner).normalize(
            source(tmp_path),
            tmp_path / "audio.wav",
            LIMITS,
        )

    assert captured.value.code is AudioAcquisitionErrorCode.NORMALIZATION_FAILED


@pytest.mark.parametrize(
    "output_probe",
    [
        probe_payload(codec="opus"),
        probe_payload(sample_rate="48000"),
        probe_payload(channels=2),
    ],
)
@pytest.mark.asyncio
async def test_normalize_requires_the_exact_pcm_output_format(
    tmp_path: Path,
    output_probe: str,
) -> None:
    runner = MediaRunner()
    runner.output_probe = output_probe

    with pytest.raises(AudioAcquisitionError) as captured:
        await FfmpegAudioNormalizer(runner=runner).normalize(
            source(tmp_path),
            tmp_path / "audio.wav",
            LIMITS,
        )

    assert captured.value.code is AudioAcquisitionErrorCode.NORMALIZATION_FAILED


@pytest.mark.asyncio
async def test_normalize_rechecks_the_output_duration(tmp_path: Path) -> None:
    runner = MediaRunner()
    runner.output_probe = probe_payload(duration="11")

    with pytest.raises(AudioAcquisitionError) as captured:
        await FfmpegAudioNormalizer(runner=runner).normalize(
            source(tmp_path),
            tmp_path / "audio.wav",
            LIMITS,
        )

    assert captured.value.code is AudioAcquisitionErrorCode.DURATION_LIMIT_EXCEEDED


@pytest.mark.asyncio
async def test_normalize_rejects_a_materially_changed_source_interval(tmp_path: Path) -> None:
    runner = MediaRunner()
    runner.output_probe = probe_payload(duration="3")

    with pytest.raises(AudioAcquisitionError) as captured:
        await FfmpegAudioNormalizer(runner=runner).normalize(
            source(tmp_path),
            tmp_path / "audio.wav",
            LIMITS,
        )

    assert captured.value.code is AudioAcquisitionErrorCode.NORMALIZATION_FAILED


@pytest.mark.parametrize(
    "document",
    [
        "not json",
        "[]",
        json.dumps({"format": {}}),
        json.dumps({"streams": [], "format": {}}),
        json.dumps({"streams": ["invalid"], "format": {}}),
        probe_payload(duration="nan"),
        probe_payload(duration="0"),
        probe_payload(size="0"),
        probe_payload(codec=""),
        probe_payload(sample_rate=16_000),
        probe_payload(channels="1"),
        probe_payload(sample_rate="0"),
        probe_payload(channels=0),
    ],
)
def test_probe_parser_rejects_invalid_or_incomplete_metadata(document: str) -> None:
    with pytest.raises(AudioAcquisitionError) as captured:
        FfmpegAudioNormalizer._parse_probe(document)

    assert captured.value.code is AudioAcquisitionErrorCode.PROBE_FAILED
