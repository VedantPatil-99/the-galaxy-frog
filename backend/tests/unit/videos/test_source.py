"""Behavior tests for provider-independent source contracts."""

from dataclasses import FrozenInstanceError

import pytest

from galaxy_frog.domain.videos.models import (
    CaptionKind,
    CaptionTrack,
    SafeVideoMetadata,
    SourceCaptionCue,
    SourceReference,
    VideoSourceKind,
)
from galaxy_frog.domain.videos.source import (
    VideoSource,
    VideoSourceError,
    VideoSourceErrorCode,
)


class FakeSource:
    source_kind = VideoSourceKind.YOUTUBE

    def canonicalize(self, locator: str) -> SourceReference:
        return SourceReference(self.source_kind, locator, f"https://example.com/{locator}")

    async def fetch_metadata(self, reference: SourceReference) -> SafeVideoMetadata:
        return SafeVideoMetadata(reference, "Example", 1000)

    async def list_caption_tracks(self, reference: SourceReference) -> tuple[CaptionTrack, ...]:
        del reference
        return (CaptionTrack("manual:en", "en", CaptionKind.MANUAL),)

    async def fetch_caption_cues(
        self,
        reference: SourceReference,
        track: CaptionTrack,
    ) -> tuple[SourceCaptionCue, ...]:
        del reference, track
        return (SourceCaptionCue(0, 0, 1000, "Hello."),)


def test_protocol_is_structural_and_values_are_immutable() -> None:
    source = FakeSource()
    assert isinstance(source, VideoSource)
    reference = source.canonicalize("video")

    with pytest.raises(FrozenInstanceError):
        reference.external_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: SourceReference(VideoSourceKind.YOUTUBE, "", "https://youtube.com"),
            "external_id",
        ),
        (lambda: SourceReference(VideoSourceKind.YOUTUBE, "abc", "http://youtube.com"), "https"),
        (lambda: SourceReference(VideoSourceKind.YOUTUBE, "abc", "https:///watch"), "host"),
        (
            lambda: SafeVideoMetadata(
                SourceReference(VideoSourceKind.YOUTUBE, "abc", "https://youtube.com/watch?v=abc"),
                "",
                1000,
            ),
            "title",
        ),
        (
            lambda: SafeVideoMetadata(
                SourceReference(VideoSourceKind.YOUTUBE, "abc", "https://youtube.com/watch?v=abc"),
                "Title",
                0,
            ),
            "duration_ms",
        ),
        (
            lambda: SafeVideoMetadata(
                SourceReference(VideoSourceKind.YOUTUBE, "abc", "https://youtube.com/watch?v=abc"),
                "Title",
                1000,
                thumbnail_url="http://example.com/image.jpg",
            ),
            "thumbnail_url",
        ),
        (lambda: CaptionTrack("", "en", CaptionKind.MANUAL), "track_id"),
        (lambda: CaptionTrack("manual", "", CaptionKind.MANUAL), "language_code"),
        (lambda: SourceCaptionCue(-1, 0, 1, "x"), "source_order"),
        (lambda: SourceCaptionCue(0, -1, 1, "x"), "start_ms"),
        (lambda: SourceCaptionCue(0, 1, 1, "x"), "end_ms"),
        (lambda: SourceCaptionCue(0, 0, 1, "  "), "text"),
    ],
)
def test_source_values_reject_invalid_data(factory: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        assert callable(factory)
        factory()


def test_source_error_exposes_stable_public_fields() -> None:
    error = VideoSourceError(
        VideoSourceErrorCode.SOURCE_UNAVAILABLE,
        "Unavailable.",
        retryable=True,
    )

    assert str(error) == "Unavailable."
    assert error.code is VideoSourceErrorCode.SOURCE_UNAVAILABLE
    assert error.message == "Unavailable."
    assert error.retryable is True
