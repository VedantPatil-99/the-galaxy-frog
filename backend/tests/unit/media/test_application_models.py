"""Behavior coverage for provider-neutral intermediate media values."""

from dataclasses import replace
from pathlib import Path

import pytest

from galaxy_frog.application.media import DownloadedAudio, NormalizedAudio

ABSOLUTE_TEST_MEDIA_ROOT = Path(__file__).resolve().parent


def downloaded() -> DownloadedAudio:
    return DownloadedAudio(
        ABSOLUTE_TEST_MEDIA_ROOT / "source.webm",
        5,
        "yt-dlp",
        "2026.08.19",
    )


def normalized() -> NormalizedAudio:
    return NormalizedAudio(
        path=ABSOLUTE_TEST_MEDIA_ROOT / "audio.wav",
        duration_ms=1000,
        size_bytes=32_078,
        media_type="audio/wav",
        codec="pcm_s16le",
        sample_rate_hz=16_000,
        channels=1,
        normalizer="ffmpeg",
        normalizer_revision="9.0.1",
    )


def test_intermediate_media_values_retain_tool_and_format_details() -> None:
    source = downloaded()
    output = normalized()

    assert source.downloader_revision == "2026.08.19"
    assert output.duration_ms == 1000
    assert (output.codec, output.sample_rate_hz, output.channels) == ("pcm_s16le", 16_000, 1)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("path", Path("relative.webm"), "absolute"),
        ("size_bytes", 0, "size_bytes"),
        ("downloader", "", "downloader"),
        ("downloader_revision", " ", "downloader_revision"),
    ],
)
def test_downloaded_audio_rejects_invalid_metadata(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(downloaded(), **{field: value})


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("path", Path("relative.wav"), "absolute"),
        ("size_bytes", 0, "size_bytes"),
        ("duration_ms", 0, "duration_ms"),
        ("sample_rate_hz", 0, "sample_rate_hz"),
        ("channels", 0, "channels"),
        ("media_type", "", "media_type"),
        ("codec", "", "codec"),
        ("normalizer", "", "normalizer"),
        ("normalizer_revision", "", "normalizer_revision"),
    ],
)
def test_normalized_audio_rejects_invalid_metadata(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(normalized(), **{field: value})
