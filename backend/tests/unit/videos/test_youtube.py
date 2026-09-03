"""Tests for safe YouTube identity, metadata, and caption behavior."""

# pyright: reportPrivateUsage=false

import json
from collections.abc import Mapping
from types import SimpleNamespace

import pytest

from galaxy_frog.adapters.video_sources import youtube
from galaxy_frog.adapters.video_sources.youtube import YouTubeSource
from galaxy_frog.domain.videos.models import (
    CaptionKind,
    CaptionTrack,
    SourceReference,
    VideoSourceKind,
)
from galaxy_frog.domain.videos.source import (
    VideoSourceError,
    VideoSourceErrorCode,
)

VIDEO_ID = "dQw4w9WgXcQ"


def info_payload() -> dict[str, object]:
    return {
        "id": VIDEO_ID,
        "title": "Example video",
        "duration": 12.345,
        "channel": "Example channel",
        "thumbnail": "https://example.com/thumb.jpg",
        "subtitles": {"en": [{"ext": "json3", "url": "https://example.com/en.json3"}]},
        "automatic_captions": {"es": [{"ext": "vtt", "url": "https://example.com/es.vtt"}]},
    }


@pytest.mark.parametrize(
    "url",
    [
        f"https://youtu.be/{VIDEO_ID}",
        f"https://www.youtube.com/watch?v={VIDEO_ID}",
        f"https://m.youtube.com/shorts/{VIDEO_ID}",
        f"https://youtube.com/embed/{VIDEO_ID}",
        f"http://music.youtube.com/live/{VIDEO_ID}",
    ],
)
def test_canonicalizes_supported_single_video_urls(url: str) -> None:
    reference = YouTubeSource().canonicalize(url)

    assert reference.external_id == VIDEO_ID
    assert reference.canonical_url == f"https://www.youtube.com/watch?v={VIDEO_ID}"


@pytest.mark.parametrize(
    "url",
    [
        "not a url",
        "https://example.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=short",
        "https://youtu.be/dQw4w9WgXcQ/extra",
        "https://youtube.com/channel/dQw4w9WgXcQ",
    ],
)
def test_rejects_unsupported_or_malformed_urls(url: str) -> None:
    with pytest.raises(VideoSourceError) as captured:
        YouTubeSource().canonicalize(url)

    assert captured.value.code is VideoSourceErrorCode.INVALID_SOURCE


def test_rejects_a_url_with_invalid_authority_syntax() -> None:
    with pytest.raises(VideoSourceError):
        YouTubeSource().canonicalize("https://[invalid/watch")


@pytest.mark.parametrize(
    "url",
    [
        f"https://youtube.com/watch?v={VIDEO_ID}&list=PL123",
        "https://youtube.com/playlist?list=PL123",
    ],
)
def test_rejects_playlists(url: str) -> None:
    with pytest.raises(VideoSourceError) as captured:
        YouTubeSource().canonicalize(url)

    assert captured.value.code is VideoSourceErrorCode.UNSUPPORTED_VIDEO


@pytest.mark.asyncio
async def test_fetches_safe_metadata_and_lists_manual_before_automatic_tracks() -> None:
    source = YouTubeSource(info_loader=lambda _url: info_payload())
    reference = source.canonicalize(f"https://youtu.be/{VIDEO_ID}")

    metadata = await source.fetch_metadata(reference)
    tracks = await source.list_caption_tracks(reference)

    assert metadata.title == "Example video"
    assert metadata.duration_ms == 12_345
    assert metadata.channel_name == "Example channel"
    assert metadata.thumbnail_url == "https://example.com/thumb.jpg"
    assert [(track.language_code, track.kind) for track in tracks] == [
        ("en", CaptionKind.MANUAL),
        ("es", CaptionKind.AUTOMATIC),
    ]


@pytest.mark.asyncio
async def test_metadata_uses_uploader_and_ignores_unsafe_thumbnail() -> None:
    payload = info_payload()
    payload.pop("channel")
    payload["uploader"] = "Uploader"
    payload["thumbnail"] = "http://example.com/thumb.jpg"
    source = YouTubeSource(info_loader=lambda _url: payload)
    reference = source.canonicalize(f"https://youtu.be/{VIDEO_ID}")

    metadata = await source.fetch_metadata(reference)

    assert metadata.channel_name == "Uploader"
    assert metadata.thumbnail_url is None


@pytest.mark.parametrize("field", ["title", "duration"])
@pytest.mark.asyncio
async def test_rejects_missing_required_metadata(field: str) -> None:
    payload = info_payload()
    payload[field] = None
    source = YouTubeSource(info_loader=lambda _url: payload)
    reference = source.canonicalize(f"https://youtu.be/{VIDEO_ID}")

    with pytest.raises(VideoSourceError) as captured:
        await source.fetch_metadata(reference)

    assert captured.value.code is VideoSourceErrorCode.SOURCE_UNAVAILABLE


@pytest.mark.asyncio
async def test_fetches_and_normalizes_json3_captions() -> None:
    document = json.dumps(
        {
            "events": [
                {
                    "tStartMs": 0,
                    "dDurationMs": 1200,
                    "segs": [{"utf8": "Hello\n"}, {"utf8": "world."}],
                },
                {"tStartMs": 1200, "dDurationMs": 800, "segs": [{"utf8": "Next cue."}]},
            ]
        }
    )

    async def caption_loader(_url: str) -> str:
        return document

    source = YouTubeSource(info_loader=lambda _url: info_payload(), caption_loader=caption_loader)
    reference = source.canonicalize(f"https://youtu.be/{VIDEO_ID}")
    track = (await source.list_caption_tracks(reference))[0]

    source_cues = await source.fetch_caption_cues(reference, track)
    cues = source.normalize_cues(reference, track, source_cues)

    assert [(cue.start_ms, cue.end_ms, cue.text) for cue in cues] == [
        (0, 1200, "Hello world."),
        (1200, 2000, "Next cue."),
    ]
    assert cues[0].cue_id != cues[1].cue_id
    assert cues[0].track_id == "manual:en"


@pytest.mark.asyncio
async def test_fetches_webvtt_when_json3_is_unavailable() -> None:
    payload = info_payload()
    payload["subtitles"] = {"en": [{"ext": "vtt", "url": "https://example.com/en.vtt"}]}

    async def caption_loader(_url: str) -> str:
        return "WEBVTT\n\n00:00:00.000 --> 00:00:01.500\n<c>First</c> cue.\n\n00:01.500 --> 00:03.000\nSecond cue."

    source = YouTubeSource(info_loader=lambda _url: payload, caption_loader=caption_loader)
    reference = source.canonicalize(f"https://youtu.be/{VIDEO_ID}")
    track = (await source.list_caption_tracks(reference))[0]

    cues = await source.fetch_caption_cues(reference, track)

    assert [(cue.start_ms, cue.end_ms, cue.text) for cue in cues] == [
        (0, 1500, "First cue."),
        (1500, 3000, "Second cue."),
    ]


def test_caption_selection_prefers_requested_manual_then_automatic() -> None:
    tracks = (
        CaptionTrack("automatic:en", "en", CaptionKind.AUTOMATIC),
        CaptionTrack("manual:fr", "fr", CaptionKind.MANUAL),
        CaptionTrack("manual:en", "en", CaptionKind.MANUAL),
    )

    assert YouTubeSource.select_caption_track(tracks, ("en",)).track_id == "manual:en"
    assert YouTubeSource.select_caption_track(tracks, ("fr",)).track_id == "manual:fr"

    without_requested_manual = tuple(track for track in tracks if track.track_id != "manual:en")
    assert (
        YouTubeSource.select_caption_track(without_requested_manual, ("en",)).track_id
        == "automatic:en"
    )


def test_caption_selection_rejects_empty_tracks() -> None:
    with pytest.raises(VideoSourceError) as captured:
        YouTubeSource.select_caption_track(())

    assert captured.value.code is VideoSourceErrorCode.TRANSCRIPT_UNAVAILABLE


@pytest.mark.asyncio
async def test_source_loader_failures_are_safe_and_classified() -> None:
    def unavailable(_url: str) -> Mapping[str, object]:
        raise RuntimeError("temporary outage")

    source = YouTubeSource(info_loader=unavailable)
    reference = source.canonicalize(f"https://youtu.be/{VIDEO_ID}")

    with pytest.raises(VideoSourceError) as captured:
        await source.fetch_metadata(reference)

    assert captured.value.code is VideoSourceErrorCode.SOURCE_UNAVAILABLE
    assert captured.value.retryable is True


@pytest.mark.parametrize(
    "document",
    [
        json.dumps({"events": [{"tStartMs": 0, "dDurationMs": 0, "segs": [{"utf8": "Invalid."}]}]}),
        "WEBVTT\n\n00:00:02.000 --> 00:00:01.000\nInvalid.",
    ],
)
@pytest.mark.asyncio
async def test_rejects_malformed_caption_intervals(document: str) -> None:
    payload = info_payload()
    extension = "json3" if document.startswith("{") else "vtt"
    payload["subtitles"] = {
        "en": [{"ext": extension, "url": f"https://example.com/en.{extension}"}]
    }

    async def caption_loader(_url: str) -> str:
        return document

    source = YouTubeSource(info_loader=lambda _url: payload, caption_loader=caption_loader)
    reference = source.canonicalize(f"https://youtu.be/{VIDEO_ID}")
    track = (await source.list_caption_tracks(reference))[0]

    with pytest.raises(VideoSourceError) as captured:
        await source.fetch_caption_cues(reference, track)

    assert captured.value.code is VideoSourceErrorCode.SOURCE_UNAVAILABLE


@pytest.mark.asyncio
async def test_private_source_failure_requires_authentication() -> None:
    def private(_url: str) -> Mapping[str, object]:
        raise RuntimeError("Sign in to view this private video")

    source = YouTubeSource(info_loader=private)
    reference = source.canonicalize(f"https://youtu.be/{VIDEO_ID}")

    with pytest.raises(VideoSourceError) as captured:
        await source.fetch_metadata(reference)

    assert captured.value.code is VideoSourceErrorCode.SOURCE_AUTH_REQUIRED
    assert captured.value.retryable is False


def test_default_info_loader_disables_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    class FakeYoutubeDl:
        def __init__(self, options: Mapping[str, object]) -> None:
            observed.update(options)

        def __enter__(self) -> FakeYoutubeDl:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def extract_info(self, url: str, *, download: bool) -> Mapping[str, object]:
            assert url == "https://example.test/video"
            assert download is False
            return {"id": VIDEO_ID}

    def import_fake_module(_name: str) -> object:
        return SimpleNamespace(YoutubeDL=FakeYoutubeDl)

    monkeypatch.setattr(youtube, "import_module", import_fake_module)

    assert youtube._default_info_loader("https://example.test/video") == {"id": VIDEO_ID}
    assert observed["skip_download"] is True
    assert observed["noplaylist"] is True


def test_default_info_loader_rejects_empty_result(monkeypatch: pytest.MonkeyPatch) -> None:
    class EmptyYoutubeDl:
        def __init__(self, _options: Mapping[str, object]) -> None:
            pass

        def __enter__(self) -> EmptyYoutubeDl:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def extract_info(self, _url: str, *, download: bool) -> None:
            assert download is False
            return None

    def import_empty_module(_name: str) -> object:
        return SimpleNamespace(YoutubeDL=EmptyYoutubeDl)

    monkeypatch.setattr(youtube, "import_module", import_empty_module)
    with pytest.raises(RuntimeError, match="no video metadata"):
        youtube._default_info_loader("https://example.test/video")


@pytest.mark.asyncio
async def test_default_caption_loader_is_https_and_size_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match="HTTPS"):
        await youtube._default_caption_loader("http://example.test/captions")

    class Response:
        def __init__(self, body: bytes) -> None:
            self.body = body

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            assert limit == 10_000_001
            return self.body

    def open_small(*_args: object, **_kwargs: object) -> Response:
        return Response(b"WEBVTT")

    monkeypatch.setattr(youtube, "urlopen", open_small)
    assert await youtube._default_caption_loader("https://example.test/captions") == "WEBVTT"

    def open_large(*_args: object, **_kwargs: object) -> Response:
        return Response(b"x" * 10_000_001)

    monkeypatch.setattr(youtube, "urlopen", open_large)
    with pytest.raises(RuntimeError, match="10 MB"):
        await youtube._default_caption_loader("https://example.test/captions")


@pytest.mark.asyncio
async def test_rejects_unexpected_identity_and_playlist_metadata() -> None:
    reference = YouTubeSource().canonicalize(f"https://youtu.be/{VIDEO_ID}")
    for payload, code in [
        ({"id": "aaaaaaaaaaa"}, VideoSourceErrorCode.SOURCE_UNAVAILABLE),
        ({"id": VIDEO_ID, "_type": "playlist"}, VideoSourceErrorCode.UNSUPPORTED_VIDEO),
    ]:
        source = YouTubeSource(info_loader=lambda _url, value=payload: value)
        with pytest.raises(VideoSourceError) as captured:
            await source.fetch_metadata(reference)
        assert captured.value.code is code

    wrong_kind = SourceReference(
        VideoSourceKind.LOCAL_FILE,
        "fixture.json",
        "file:///fixture.json",
    )
    with pytest.raises(VideoSourceError):
        await YouTubeSource(info_loader=lambda _url: info_payload()).fetch_metadata(wrong_kind)

    def deliberate(_url: str) -> Mapping[str, object]:
        raise VideoSourceError(VideoSourceErrorCode.UNSUPPORTED_VIDEO, "Unsupported")

    with pytest.raises(VideoSourceError) as captured:
        await YouTubeSource(info_loader=deliberate).fetch_metadata(reference)
    assert captured.value.code is VideoSourceErrorCode.UNSUPPORTED_VIDEO


@pytest.mark.asyncio
async def test_caption_failures_remain_stable() -> None:
    reference = YouTubeSource().canonicalize(f"https://youtu.be/{VIDEO_ID}")
    unsupported = info_payload()
    unsupported["subtitles"] = {"en": [{"ext": "srt", "url": "https://example.test"}]}
    source = YouTubeSource(info_loader=lambda _url: unsupported)
    track = (await source.list_caption_tracks(reference))[0]
    with pytest.raises(VideoSourceError) as captured:
        await source.fetch_caption_cues(reference, track)
    assert captured.value.code is VideoSourceErrorCode.TRANSCRIPT_UNAVAILABLE

    async def deliberate(_url: str) -> str:
        raise VideoSourceError(VideoSourceErrorCode.TRANSCRIPT_UNAVAILABLE, "No captions")

    source = YouTubeSource(info_loader=lambda _url: info_payload(), caption_loader=deliberate)
    track = (await source.list_caption_tracks(reference))[0]
    with pytest.raises(VideoSourceError) as captured:
        await source.fetch_caption_cues(reference, track)
    assert captured.value.message == "No captions"

    async def empty(_url: str) -> str:
        return json.dumps({"events": []})

    source = YouTubeSource(info_loader=lambda _url: info_payload(), caption_loader=empty)
    track = (await source.list_caption_tracks(reference))[0]
    with pytest.raises(VideoSourceError) as captured:
        await source.fetch_caption_cues(reference, track)
    assert captured.value.code is VideoSourceErrorCode.TRANSCRIPT_UNAVAILABLE


def test_caption_parser_ignores_non_cue_events_and_rejects_bad_vtt_time() -> None:
    document = json.dumps(
        {
            "events": [
                "not-an-event",
                {"tStartMs": 0, "dDurationMs": 100, "window": 1},
                {
                    "tStartMs": 0,
                    "dDurationMs": 100,
                    "segs": ["ignored", {"utf8": 1}, {"utf8": "  "}],
                },
            ]
        }
    )
    assert YouTubeSource._parse_json3("[]") == ()
    assert YouTubeSource._parse_json3(json.dumps({"events": "invalid"})) == ()
    assert YouTubeSource._parse_json3(document) == ()
    assert YouTubeSource._parse_vtt("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n  ") == ()
    with pytest.raises(ValueError, match="WebVTT"):
        YouTubeSource._parse_vtt("WEBVTT\n\nbad --> 00:00:01.000\nText")


def test_caption_helpers_handle_missing_maps_and_fallbacks() -> None:
    assert YouTubeSource._tracks_from_mapping([], CaptionKind.MANUAL) == []
    assert YouTubeSource._tracks_from_mapping({1: [], "en": []}, CaptionKind.MANUAL) == []
    assert YouTubeSource._caption_formats([], "en") == []
    assert YouTubeSource._caption_formats({"en": "invalid"}, "en") == []
    assert YouTubeSource._required_string({"title": " Title "}, "title") == "Title"
    assert YouTubeSource._optional_string({"channel": 1}, "channel") is None
    assert YouTubeSource._optional_string({"channel": " "}, "channel") is None

    tracks = (
        CaptionTrack("manual:fr", "fr", CaptionKind.MANUAL),
        CaptionTrack("automatic:de", "de", CaptionKind.AUTOMATIC),
    )
    assert YouTubeSource.select_caption_track(tracks, ("en",)).track_id == "manual:fr"
