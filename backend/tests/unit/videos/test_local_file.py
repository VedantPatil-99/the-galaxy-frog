"""Tests for the bounded local transcript fixture adapter."""

import json
from pathlib import Path

import pytest

from galaxy_frog.adapters.video_sources.local_file import LocalFileSource
from galaxy_frog.domain.videos.models import (
    CaptionKind,
    CaptionTrack,
    SourceReference,
    VideoSourceKind,
)
from galaxy_frog.domain.videos.source import VideoSourceError, VideoSourceErrorCode


@pytest.mark.asyncio
async def test_reads_metadata_and_cues_from_a_bounded_json_sidecar(tmp_path: Path) -> None:
    fixture = tmp_path / "video.json"
    fixture.write_text(
        json.dumps(
            {
                "title": "Fixture video",
                "duration_ms": 2000,
                "channel_name": "Fixture channel",
                "language_code": "en",
                "cues": [
                    {"start_ms": 0, "end_ms": 1000, "text": "First."},
                    {"start_ms": 1000, "end_ms": 2000, "text": "Second."},
                ],
            }
        ),
        encoding="utf-8",
    )
    source = LocalFileSource(tmp_path)
    reference = source.canonicalize(fixture.as_uri())

    metadata = await source.fetch_metadata(reference)
    tracks = await source.list_caption_tracks(reference)
    cues = await source.fetch_caption_cues(reference, tracks[0])

    assert metadata.title == "Fixture video"
    assert tracks[0].language_code == "en"
    assert [cue.text for cue in cues] == ["First.", "Second."]


def test_rejects_files_outside_root_and_non_json_files(tmp_path: Path) -> None:
    source = LocalFileSource(tmp_path / "allowed")

    with pytest.raises(VideoSourceError) as captured:
        source.canonicalize(str(tmp_path / "outside.json"))

    assert captured.value.code is VideoSourceErrorCode.INVALID_SOURCE

    with pytest.raises(VideoSourceError):
        source.canonicalize(str(tmp_path / "allowed" / "fixture.txt"))


@pytest.mark.asyncio
async def test_rejects_invalid_fixture_and_unknown_track(tmp_path: Path) -> None:
    fixture = tmp_path / "invalid.json"
    fixture.write_text("{}", encoding="utf-8")
    source = LocalFileSource(tmp_path)
    reference = source.canonicalize(str(fixture))

    with pytest.raises(VideoSourceError) as metadata_error:
        await source.fetch_metadata(reference)

    assert metadata_error.value.code is VideoSourceErrorCode.INVALID_SOURCE

    track = (await source.list_caption_tracks(reference))[0]
    unknown = type(track)("manual:missing", "en", track.kind)
    with pytest.raises(VideoSourceError) as captured:
        await source.fetch_caption_cues(reference, unknown)

    assert captured.value.code is VideoSourceErrorCode.TRANSCRIPT_UNAVAILABLE


@pytest.mark.parametrize(
    "payload",
    [
        {"title": 1, "duration_ms": 1000},
        {"title": "Title", "duration_ms": "1000"},
        {"title": "Title", "duration_ms": 1000, "channel_name": 1},
        {"title": "Title", "duration_ms": 0},
    ],
)
@pytest.mark.asyncio
async def test_rejects_invalid_fixture_metadata(tmp_path: Path, payload: object) -> None:
    fixture = tmp_path / "invalid-metadata.json"
    fixture.write_text(json.dumps(payload), encoding="utf-8")
    source = LocalFileSource(tmp_path)

    with pytest.raises(VideoSourceError):
        await source.fetch_metadata(source.canonicalize(str(fixture)))


@pytest.mark.asyncio
async def test_rejects_invalid_language_and_cue_documents(tmp_path: Path) -> None:
    fixture = tmp_path / "invalid-content.json"
    source = LocalFileSource(tmp_path)

    fixture.write_text(json.dumps({"language_code": 1}), encoding="utf-8")
    reference = source.canonicalize(str(fixture))
    with pytest.raises(VideoSourceError):
        await source.list_caption_tracks(reference)

    fixture.write_text(json.dumps({"cues": "invalid"}), encoding="utf-8")
    with pytest.raises(VideoSourceError):
        await source.fetch_caption_cues(
            reference,
            CaptionTrack("manual:fixture", "en", CaptionKind.MANUAL),
        )

    fixture.write_text(
        json.dumps({"cues": [{"start_ms": 1, "end_ms": 1, "text": "broken"}]}),
        encoding="utf-8",
    )
    track = (await source.list_caption_tracks(reference))[0]
    with pytest.raises(VideoSourceError):
        await source.fetch_caption_cues(reference, track)

    fixture.write_text(json.dumps({"cues": ["ignored"]}), encoding="utf-8")
    assert await source.fetch_caption_cues(reference, track) == ()


@pytest.mark.parametrize("contents", ["not-json", "[]"])
@pytest.mark.asyncio
async def test_rejects_unreadable_or_non_object_documents(tmp_path: Path, contents: str) -> None:
    fixture = tmp_path / "broken.json"
    fixture.write_text(contents, encoding="utf-8")
    source = LocalFileSource(tmp_path)
    reference = source.canonicalize(str(fixture))

    with pytest.raises(VideoSourceError):
        await source.list_caption_tracks(reference)


@pytest.mark.asyncio
async def test_rejects_wrong_kind_missing_and_escaping_references(tmp_path: Path) -> None:
    source = LocalFileSource(tmp_path)
    wrong_kind = SourceReference(
        VideoSourceKind.YOUTUBE,
        "fixture.json",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    )
    with pytest.raises(VideoSourceError):
        await source.list_caption_tracks(wrong_kind)

    missing = SourceReference(
        VideoSourceKind.LOCAL_FILE,
        "missing.json",
        (tmp_path / "missing.json").as_uri(),
    )
    with pytest.raises(VideoSourceError):
        await source.list_caption_tracks(missing)

    escaping = SourceReference(
        VideoSourceKind.LOCAL_FILE,
        "../outside.json",
        (tmp_path / "outside.json").as_uri(),
    )
    with pytest.raises(VideoSourceError):
        await source.list_caption_tracks(escaping)
