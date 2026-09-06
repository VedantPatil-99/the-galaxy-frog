"""FFmpeg audio normalization with ffprobe verification and hard limits."""

import asyncio
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from galaxy_frog.adapters.media.process import CommandResult, CommandRunner, CommandTimedOut
from galaxy_frog.application.media import DownloadedAudio, NormalizedAudio
from galaxy_frog.domain.media import (
    AudioAcquisitionError,
    AudioAcquisitionErrorCode,
    AudioAcquisitionLimits,
)


@dataclass(frozen=True, slots=True)
class _ProbeResult:
    duration_ms: int
    size_bytes: int
    codec: str
    sample_rate_hz: int
    channels: int


class FfmpegAudioNormalizer:
    """Normalize one audio stream to verified mono 16 kHz PCM WAV."""

    def __init__(
        self,
        *,
        runner: CommandRunner,
        ffmpeg_executable: str = "ffmpeg",
        ffprobe_executable: str = "ffprobe",
    ) -> None:
        self._runner = runner
        self._ffmpeg_executable = ffmpeg_executable
        self._ffprobe_executable = ffprobe_executable

    async def normalize(
        self,
        source: DownloadedAudio,
        output_path: Path,
        limits: AudioAcquisitionLimits,
    ) -> NormalizedAudio:
        source_probe = await self._probe(source.path, limits)
        if max(source.size_bytes, source_probe.size_bytes) > limits.max_download_bytes:
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.DOWNLOAD_SIZE_LIMIT_EXCEEDED,
                "The source audio exceeds the configured download-size limit.",
            )
        if source_probe.duration_ms > limits.max_duration_ms:
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.DURATION_LIMIT_EXCEEDED,
                "The source duration exceeds the configured audio limit.",
            )

        revision = await self._revision(output_path.parent, limits)
        arguments = (
            self._ffmpeg_executable,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source.path),
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-fs",
            str(limits.max_output_bytes),
            str(output_path),
        )
        result = await self._run(
            arguments,
            cwd=output_path.parent,
            limits=limits,
            failure_code=AudioAcquisitionErrorCode.NORMALIZATION_FAILED,
            safe_message="The local audio normalizer could not be started.",
        )
        if result.return_code != 0:
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.NORMALIZATION_FAILED,
                "The source audio could not be normalized.",
            )

        try:
            resolved_output, output_size = await asyncio.to_thread(
                self._output_metadata,
                output_path,
            )
        except OSError as exc:
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.NORMALIZATION_FAILED,
                "The audio normalizer did not produce a readable output file.",
            ) from exc
        if output_size > limits.max_output_bytes:
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.OUTPUT_SIZE_LIMIT_EXCEEDED,
                "The normalized audio exceeds the configured output-size limit.",
            )
        if output_size <= 0:
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.NORMALIZATION_FAILED,
                "The audio normalizer produced an empty output file.",
            )

        output_probe = await self._probe(output_path, limits)
        if (
            output_probe.codec != "pcm_s16le"
            or output_probe.sample_rate_hz != 16_000
            or output_probe.channels != 1
        ):
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.NORMALIZATION_FAILED,
                "The normalized audio does not match the required PCM format.",
            )
        if output_probe.duration_ms > limits.max_duration_ms:
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.DURATION_LIMIT_EXCEEDED,
                "The normalized audio duration exceeds the configured limit.",
            )
        return NormalizedAudio(
            path=resolved_output,
            duration_ms=output_probe.duration_ms,
            size_bytes=output_size,
            media_type="audio/wav",
            codec=output_probe.codec,
            sample_rate_hz=output_probe.sample_rate_hz,
            channels=output_probe.channels,
            normalizer="ffmpeg",
            normalizer_revision=revision,
        )

    async def _probe(self, path: Path, limits: AudioAcquisitionLimits) -> _ProbeResult:
        arguments = (
            self._ffprobe_executable,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,sample_rate,channels:format=duration,size",
            "-of",
            "json",
            str(path),
        )
        result = await self._run(
            arguments,
            cwd=path.parent,
            limits=limits,
            failure_code=AudioAcquisitionErrorCode.PROBE_FAILED,
            safe_message="The local audio inspector could not be started.",
        )
        if result.return_code != 0:
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.PROBE_FAILED,
                "The source audio could not be inspected.",
            )
        return self._parse_probe(result.stdout)

    async def _revision(self, cwd: Path, limits: AudioAcquisitionLimits) -> str:
        result = await self._run(
            (self._ffmpeg_executable, "-version"),
            cwd=cwd,
            limits=limits,
            failure_code=AudioAcquisitionErrorCode.NORMALIZATION_FAILED,
            safe_message="The local audio normalizer could not be started.",
        )
        first_line = result.stdout.splitlines()[0].strip() if result.stdout.splitlines() else ""
        prefix = "ffmpeg version "
        if result.return_code != 0 or not first_line.startswith(prefix):
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.NORMALIZATION_FAILED,
                "The local audio normalizer version could not be identified.",
            )
        revision = first_line.removeprefix(prefix).split(maxsplit=1)[0]
        if not revision:
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.NORMALIZATION_FAILED,
                "The local audio normalizer version could not be identified.",
            )
        return revision

    async def _run(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path,
        limits: AudioAcquisitionLimits,
        failure_code: AudioAcquisitionErrorCode,
        safe_message: str,
    ) -> CommandResult:
        try:
            return await self._runner.run(
                arguments,
                cwd=cwd,
                timeout_seconds=limits.timeout_seconds,
            )
        except CommandTimedOut as exc:
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.TIMEOUT,
                "Audio processing exceeded its configured deadline.",
                retryable=True,
            ) from exc
        except OSError as exc:
            raise AudioAcquisitionError(failure_code, safe_message) from exc

    @staticmethod
    def _output_metadata(path: Path) -> tuple[Path, int]:
        return path.resolve(), path.stat().st_size

    @staticmethod
    def _parse_probe(document: str) -> _ProbeResult:
        try:
            payload_value: object = json.loads(document)
            if not isinstance(payload_value, Mapping):
                raise ValueError("probe payload is not an object")
            payload = cast(Mapping[str, object], payload_value)
            streams_value = payload.get("streams")
            format_value = payload.get("format")
            if not isinstance(streams_value, list):
                raise ValueError("probe payload must contain one audio stream")
            streams = cast(list[object], streams_value)
            if len(streams) != 1:
                raise ValueError("probe payload must contain one audio stream")
            stream_value = streams[0]
            if not isinstance(stream_value, Mapping) or not isinstance(format_value, Mapping):
                raise ValueError("probe payload fields are invalid")
            stream = cast(Mapping[str, object], stream_value)
            media_format = cast(Mapping[str, object], format_value)
            codec = stream.get("codec_name")
            sample_rate = stream.get("sample_rate")
            channels = stream.get("channels")
            duration = float(cast(str, media_format.get("duration")))
            size_bytes = int(cast(str, media_format.get("size")))
            if (
                not isinstance(codec, str)
                or not codec.strip()
                or not isinstance(sample_rate, str)
                or not isinstance(channels, int)
                or not math.isfinite(duration)
                or duration <= 0
                or size_bytes <= 0
            ):
                raise ValueError("probe values are invalid")
            sample_rate_hz = int(sample_rate)
            if sample_rate_hz <= 0 or channels <= 0:
                raise ValueError("probe audio format is invalid")
        except (json.JSONDecodeError, TypeError, ValueError, OverflowError) as exc:
            raise AudioAcquisitionError(
                AudioAcquisitionErrorCode.PROBE_FAILED,
                "The audio inspector returned invalid media metadata.",
            ) from exc
        return _ProbeResult(
            duration_ms=round(duration * 1000),
            size_bytes=size_bytes,
            codec=codec,
            sample_rate_hz=sample_rate_hz,
            channels=channels,
        )
