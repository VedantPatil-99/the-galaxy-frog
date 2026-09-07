"""Opt-in integration coverage for the installed local FFmpeg toolchain."""

import asyncio
import os
from pathlib import Path

import pytest

from galaxy_frog.adapters.media.ffmpeg import FfmpegAudioNormalizer
from galaxy_frog.adapters.media.process import AsyncSubprocessRunner
from galaxy_frog.application.media import DownloadedAudio
from galaxy_frog.domain.media import AudioAcquisitionLimits

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_MEDIA_INTEGRATION") != "1",
    reason="set RUN_MEDIA_INTEGRATION=1 with FFmpeg and ffprobe configured",
)


@pytest.mark.asyncio
async def test_installed_ffmpeg_normalizes_and_verifies_local_audio(tmp_path: Path) -> None:
    ffmpeg = os.environ.get("FFMPEG_EXECUTABLE", "ffmpeg")
    ffprobe = os.environ.get("FFPROBE_EXECUTABLE", "ffprobe")
    source_path = tmp_path / "source.wav"
    runner = AsyncSubprocessRunner()
    generated = await runner.run(
        (
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-ac",
            "2",
            "-ar",
            "48000",
            str(source_path),
        ),
        cwd=tmp_path,
        timeout_seconds=30,
    )
    assert generated.return_code == 0
    source_size = await asyncio.to_thread(lambda: source_path.stat().st_size)
    limits = AudioAcquisitionLimits(
        max_duration_ms=5000,
        max_download_bytes=1_048_576,
        max_output_bytes=1_048_576,
        timeout_seconds=30,
        max_concurrency=1,
    )

    normalized = await FfmpegAudioNormalizer(
        runner=runner,
        ffmpeg_executable=ffmpeg,
        ffprobe_executable=ffprobe,
    ).normalize(
        DownloadedAudio(source_path.resolve(), source_size, "fixture", "1"),
        tmp_path / "audio.wav",
        limits,
    )

    assert normalized.duration_ms == 1000
    assert normalized.codec == "pcm_s16le"
    assert normalized.sample_rate_hz == 16_000
    assert normalized.channels == 1
    assert normalized.normalizer_revision.startswith("9.0.1")
