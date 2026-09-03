"""Local JSON sidecar source used for deterministic development and tests."""

import json
from pathlib import Path
from typing import cast
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from galaxy_frog.domain.videos.models import (
    CaptionKind,
    CaptionTrack,
    SafeVideoMetadata,
    SourceCaptionCue,
    SourceReference,
    VideoSourceKind,
)
from galaxy_frog.domain.videos.source import VideoSourceError, VideoSourceErrorCode


class LocalFileSource:
    """Read a trusted transcript sidecar below one configured root."""

    source_kind = VideoSourceKind.LOCAL_FILE

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def canonicalize(self, locator: str) -> SourceReference:
        parsed = urlsplit(locator)
        raw_path = (
            Path(url2pathname(unquote(parsed.path))) if parsed.scheme == "file" else Path(locator)
        )
        path = raw_path.resolve()
        if path.suffix.lower() != ".json" or not path.is_relative_to(self._root):
            raise VideoSourceError(
                VideoSourceErrorCode.INVALID_SOURCE,
                "Local transcript fixtures must be JSON files below the configured root.",
            )
        relative = path.relative_to(self._root).as_posix()
        return SourceReference(
            kind=self.source_kind,
            external_id=relative,
            canonical_url=path.as_uri(),
        )

    async def fetch_metadata(self, reference: SourceReference) -> SafeVideoMetadata:
        payload = self._load(reference)
        try:
            title = payload["title"]
            duration_ms = payload["duration_ms"]
            channel_name = payload.get("channel_name")
            if not isinstance(title, str) or not isinstance(duration_ms, int):
                raise TypeError
            if channel_name is not None and not isinstance(channel_name, str):
                raise TypeError
            return SafeVideoMetadata(
                reference=reference,
                title=title,
                duration_ms=duration_ms,
                channel_name=channel_name,
                thumbnail_url=None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise self._invalid_fixture() from exc

    async def list_caption_tracks(self, reference: SourceReference) -> tuple[CaptionTrack, ...]:
        payload = self._load(reference)
        language = payload.get("language_code", "en")
        if not isinstance(language, str):
            raise self._invalid_fixture()
        return (
            CaptionTrack(
                track_id="manual:fixture",
                language_code=language,
                kind=CaptionKind.MANUAL,
                label="Local fixture",
            ),
        )

    async def fetch_caption_cues(
        self,
        reference: SourceReference,
        track: CaptionTrack,
    ) -> tuple[SourceCaptionCue, ...]:
        if track.track_id != "manual:fixture":
            raise VideoSourceError(
                VideoSourceErrorCode.TRANSCRIPT_UNAVAILABLE,
                "The local transcript track does not exist.",
            )
        payload = self._load(reference)
        raw_cues_value = payload.get("cues")
        if not isinstance(raw_cues_value, list):
            raise self._invalid_fixture()
        raw_cues = cast(list[object], raw_cues_value)
        try:
            return tuple(
                SourceCaptionCue(
                    source_order=index,
                    start_ms=cast(int, item["start_ms"]),
                    end_ms=cast(int, item["end_ms"]),
                    text=cast(str, item["text"]),
                )
                for index, item in enumerate(raw_cues)
                if isinstance(item, dict)
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise self._invalid_fixture() from exc

    def _load(self, reference: SourceReference) -> dict[str, object]:
        if reference.kind is not self.source_kind:
            raise self._invalid_fixture()
        path = (self._root / reference.external_id).resolve()
        if not path.is_relative_to(self._root):
            raise self._invalid_fixture()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise self._invalid_fixture() from exc
        if not isinstance(payload, dict):
            raise self._invalid_fixture()
        return cast(dict[str, object], payload)

    @staticmethod
    def _invalid_fixture() -> VideoSourceError:
        return VideoSourceError(
            VideoSourceErrorCode.INVALID_SOURCE,
            "The local transcript fixture is invalid.",
        )
