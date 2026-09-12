"""Metadata-and-caption-only YouTube source adapter."""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from functools import partial
from importlib import import_module
from typing import Literal, Protocol, cast
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from galaxy_frog.domain.transcripts.models import TranscriptCue
from galaxy_frog.domain.videos.models import (
    CaptionKind,
    CaptionTrack,
    SafeVideoMetadata,
    SourceCaptionCue,
    SourceReference,
    VideoSourceKind,
)
from galaxy_frog.domain.videos.source import VideoSourceError, VideoSourceErrorCode

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_SUPPORTED_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
    }
)
_VTT_TIMESTAMP = re.compile(
    r"^(?:(?P<hours>\d{2,}):)?(?P<minutes>\d{2}):(?P<seconds>\d{2})[.,](?P<millis>\d{3})$"
)


class _YoutubeDl(Protocol):
    def __enter__(self) -> _YoutubeDl: ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...

    def extract_info(self, url: str, *, download: bool) -> Mapping[str, object] | None: ...


InfoLoader = Callable[[str], Mapping[str, object]]
CaptionLoader = Callable[[str], Awaitable[str]]
YtDlpJsRuntime = Literal["node", "deno"]


def _default_info_loader(
    url: str,
    *,
    js_runtime: YtDlpJsRuntime = "node",
) -> Mapping[str, object]:
    """Load metadata with yt-dlp while explicitly disabling media download."""

    module = import_module("yt_dlp")
    youtube_dl = cast(
        Callable[[Mapping[str, object]], _YoutubeDl],
        module.YoutubeDL,
    )
    options: Mapping[str, object] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": False,
        "js_runtimes": {js_runtime: {}},
    }
    with youtube_dl(options) as client:
        result = client.extract_info(url, download=False)
    if result is None:
        raise RuntimeError("yt-dlp returned no video metadata")
    return result


async def _default_caption_loader(url: str) -> str:
    """Fetch a bounded caption document without accepting caller-controlled URLs."""

    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname is None:
        raise RuntimeError("caption URL must be absolute HTTPS")

    def read() -> str:
        request = Request(url, headers={"User-Agent": "GalaxyFrog/0.1"})
        with urlopen(request, timeout=20) as response:
            body = response.read(10_000_001)
        if len(body) > 10_000_000:
            raise RuntimeError("caption document exceeds 10 MB")
        return body.decode("utf-8")

    return await asyncio.to_thread(read)


class YouTubeSource:
    """Resolve public YouTube metadata and captions without retrieving media."""

    source_kind = VideoSourceKind.YOUTUBE

    def __init__(
        self,
        *,
        js_runtime: YtDlpJsRuntime = "node",
        info_loader: InfoLoader | None = None,
        caption_loader: CaptionLoader = _default_caption_loader,
    ) -> None:
        self._js_runtime: YtDlpJsRuntime = js_runtime
        self._info_loader = info_loader or partial(_default_info_loader, js_runtime=js_runtime)
        self._caption_loader = caption_loader

    @property
    def js_runtime(self) -> YtDlpJsRuntime:
        """Return the configured local runtime used for YouTube challenge scripts."""

        return self._js_runtime

    def canonicalize(self, locator: str) -> SourceReference:
        """Canonicalize a supported single-video YouTube URL."""

        try:
            parsed = urlsplit(locator.strip())
        except ValueError as exc:
            raise self._invalid_source() from exc
        host = parsed.hostname.lower() if parsed.hostname else None
        if parsed.scheme not in {"http", "https"} or host not in _SUPPORTED_HOSTS:
            raise self._invalid_source()

        query = parse_qs(parsed.query)
        if "list" in query or parsed.path.rstrip("/") == "/playlist":
            raise VideoSourceError(
                VideoSourceErrorCode.UNSUPPORTED_VIDEO,
                "YouTube playlists are not supported in Phase 1.",
            )

        video_id: str | None = None
        if host == "youtu.be":
            parts = [part for part in parsed.path.split("/") if part]
            video_id = parts[0] if len(parts) == 1 else None
        elif parsed.path.rstrip("/") == "/watch":
            values = query.get("v", [])
            video_id = values[0] if len(values) == 1 else None
        else:
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) == 2 and parts[0] in {"embed", "shorts", "live"}:
                video_id = parts[1]

        if video_id is None or _VIDEO_ID.fullmatch(video_id) is None:
            raise self._invalid_source()
        return SourceReference(
            kind=self.source_kind,
            external_id=video_id,
            canonical_url=f"https://www.youtube.com/watch?v={video_id}",
        )

    async def fetch_metadata(self, reference: SourceReference) -> SafeVideoMetadata:
        info = await self._extract(reference)
        title = self._required_string(info, "title")
        duration_seconds = info.get("duration")
        if not isinstance(duration_seconds, int | float) or duration_seconds <= 0:
            raise VideoSourceError(
                VideoSourceErrorCode.SOURCE_UNAVAILABLE,
                "YouTube did not return a valid video duration.",
            )
        channel = self._optional_string(info, "channel") or self._optional_string(info, "uploader")
        thumbnail = self._optional_string(info, "thumbnail")
        return SafeVideoMetadata(
            reference=reference,
            title=title,
            duration_ms=round(duration_seconds * 1000),
            channel_name=channel,
            thumbnail_url=thumbnail if thumbnail and thumbnail.startswith("https://") else None,
        )

    async def list_caption_tracks(self, reference: SourceReference) -> tuple[CaptionTrack, ...]:
        info = await self._extract(reference)
        tracks = [
            *self._tracks_from_mapping(info.get("subtitles"), CaptionKind.MANUAL),
            *self._tracks_from_mapping(info.get("automatic_captions"), CaptionKind.AUTOMATIC),
        ]
        return tuple(
            sorted(
                tracks,
                key=lambda item: (
                    0 if item.kind is CaptionKind.MANUAL else 1,
                    item.language_code,
                ),
            )
        )

    async def fetch_caption_cues(
        self,
        reference: SourceReference,
        track: CaptionTrack,
    ) -> tuple[SourceCaptionCue, ...]:
        info = await self._extract(reference)
        key = "subtitles" if track.kind is CaptionKind.MANUAL else "automatic_captions"
        formats = self._caption_formats(info.get(key), track.language_code)
        selected = next((item for item in formats if item.get("ext") == "json3"), None)
        selected = selected or next((item for item in formats if item.get("ext") == "vtt"), None)
        if selected is None or not isinstance(selected.get("url"), str):
            raise VideoSourceError(
                VideoSourceErrorCode.TRANSCRIPT_UNAVAILABLE,
                "The selected YouTube caption track has no supported timestamped format.",
            )
        try:
            document = await self._caption_loader(cast(str, selected["url"]))
            cues = (
                self._parse_json3(document)
                if selected.get("ext") == "json3"
                else self._parse_vtt(document)
            )
        except VideoSourceError:
            raise
        except Exception as exc:
            raise VideoSourceError(
                VideoSourceErrorCode.SOURCE_UNAVAILABLE,
                "The YouTube caption document could not be retrieved.",
                retryable=True,
            ) from exc
        if not cues:
            raise VideoSourceError(
                VideoSourceErrorCode.TRANSCRIPT_UNAVAILABLE,
                "The selected YouTube caption track contains no usable cues.",
            )
        return cues

    async def _extract(self, reference: SourceReference) -> Mapping[str, object]:
        if reference.kind is not self.source_kind:
            raise self._invalid_source()
        try:
            info = await asyncio.to_thread(self._info_loader, reference.canonical_url)
        except VideoSourceError:
            raise
        except Exception as exc:
            message = str(exc).lower()
            code = (
                VideoSourceErrorCode.SOURCE_AUTH_REQUIRED
                if any(word in message for word in ("private", "sign in", "login"))
                else VideoSourceErrorCode.SOURCE_UNAVAILABLE
            )
            raise VideoSourceError(
                code,
                "The YouTube video could not be inspected.",
                retryable=code is VideoSourceErrorCode.SOURCE_UNAVAILABLE,
            ) from exc
        extracted_id = info.get("id")
        if extracted_id != reference.external_id:
            raise VideoSourceError(
                VideoSourceErrorCode.SOURCE_UNAVAILABLE,
                "YouTube returned metadata for an unexpected video.",
            )
        if info.get("_type") in {"playlist", "multi_video"}:
            raise VideoSourceError(
                VideoSourceErrorCode.UNSUPPORTED_VIDEO,
                "YouTube playlists are not supported in Phase 1.",
            )
        return info

    @staticmethod
    def select_caption_track(
        tracks: Sequence[CaptionTrack],
        preferred_languages: Sequence[str] = ("en",),
    ) -> CaptionTrack:
        """Prefer requested manual tracks, then requested automatic tracks."""

        language_rank = {
            language.casefold(): index for index, language in enumerate(preferred_languages)
        }

        def rank(track: CaptionTrack) -> tuple[int, int, str]:
            preferred = language_rank.get(track.language_code.casefold())
            if preferred is not None:
                group = 0 if track.kind is CaptionKind.MANUAL else 1
                return (group, preferred, track.language_code)
            return (
                2 if track.kind is CaptionKind.MANUAL else 3,
                0,
                track.language_code,
            )

        if not tracks:
            raise VideoSourceError(
                VideoSourceErrorCode.TRANSCRIPT_UNAVAILABLE,
                "This YouTube video has no available captions.",
            )
        return min(tracks, key=rank)

    @staticmethod
    def normalize_cues(
        reference: SourceReference,
        track: CaptionTrack,
        cues: Sequence[SourceCaptionCue],
    ) -> tuple[TranscriptCue, ...]:
        """Create deterministic transcript cues from a selected source track."""

        return tuple(
            TranscriptCue.from_source(
                source=reference,
                track_id=track.track_id,
                language_code=track.language_code,
                caption_kind=track.kind,
                cue=cue,
            )
            for cue in sorted(cues, key=lambda item: item.source_order)
        )

    @staticmethod
    def _tracks_from_mapping(value: object, kind: CaptionKind) -> list[CaptionTrack]:
        if not isinstance(value, Mapping):
            return []
        mapping = cast(Mapping[object, object], value)
        tracks: list[CaptionTrack] = []
        for language, formats in mapping.items():
            if isinstance(language, str) and isinstance(formats, list) and formats:
                tracks.append(
                    CaptionTrack(
                        track_id=f"{kind}:{language}",
                        language_code=language,
                        kind=kind,
                    )
                )
        return tracks

    @staticmethod
    def _caption_formats(value: object, language: str) -> list[Mapping[str, object]]:
        if not isinstance(value, Mapping):
            return []
        mapping = cast(Mapping[object, object], value)
        candidates = mapping.get(language)
        if not isinstance(candidates, list):
            return []
        return [
            cast(Mapping[str, object], item)
            for item in cast(list[object], candidates)
            if isinstance(item, Mapping)
        ]

    @staticmethod
    def _parse_json3(document: str) -> tuple[SourceCaptionCue, ...]:
        payload_value: object = json.loads(document)
        if not isinstance(payload_value, Mapping):
            return ()
        payload = cast(Mapping[str, object], payload_value)
        events_value = payload.get("events")
        if not isinstance(events_value, list):
            return ()
        cues: list[SourceCaptionCue] = []
        for event_value in cast(list[object], events_value):
            if not isinstance(event_value, Mapping):
                continue
            event = cast(Mapping[str, object], event_value)
            start = event.get("tStartMs")
            duration = event.get("dDurationMs")
            segments = event.get("segs")
            if not isinstance(segments, list):
                continue
            if not isinstance(start, int) or not isinstance(duration, int) or duration <= 0:
                raise ValueError("invalid JSON3 caption interval")
            text_parts: list[str] = []
            for segment_value in cast(list[object], segments):
                if not isinstance(segment_value, Mapping):
                    continue
                segment = cast(Mapping[str, object], segment_value)
                text_value = segment.get("utf8")
                if isinstance(text_value, str):
                    text_parts.append(text_value)
            text = "".join(text_parts)
            normalized = " ".join(text.replace("\n", " ").split())
            if normalized:
                cues.append(
                    SourceCaptionCue(
                        source_order=len(cues),
                        start_ms=start,
                        end_ms=start + duration,
                        text=normalized,
                    )
                )
        return tuple(cues)

    @classmethod
    def _parse_vtt(cls, document: str) -> tuple[SourceCaptionCue, ...]:
        cues: list[SourceCaptionCue] = []
        blocks = re.split(r"\r?\n\r?\n", document.removeprefix("\ufeff").strip())
        for block in blocks:
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            timing_index = next((i for i, line in enumerate(lines) if " --> " in line), None)
            if timing_index is None:
                continue
            times = lines[timing_index].split(" --> ", maxsplit=1)
            end_value = times[1].split(maxsplit=1)[0]
            start_ms = cls._parse_vtt_time(times[0])
            end_ms = cls._parse_vtt_time(end_value)
            text = " ".join(lines[timing_index + 1 :])
            text = re.sub(r"<[^>]+>", "", text)
            text = " ".join(text.split())
            if text and end_ms > start_ms:
                cues.append(
                    SourceCaptionCue(
                        source_order=len(cues),
                        start_ms=start_ms,
                        end_ms=end_ms,
                        text=text,
                    )
                )
            elif text:
                raise ValueError("invalid WebVTT caption interval")
        return tuple(cues)

    @staticmethod
    def _parse_vtt_time(value: str) -> int:
        match = _VTT_TIMESTAMP.fullmatch(value)
        if match is None:
            raise ValueError("invalid WebVTT timestamp")
        hours = int(match.group("hours") or 0)
        minutes = int(match.group("minutes"))
        seconds = int(match.group("seconds"))
        millis = int(match.group("millis"))
        return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis

    @staticmethod
    def _required_string(info: Mapping[str, object], key: str) -> str:
        value = info.get(key)
        if not isinstance(value, str) or not value.strip():
            raise VideoSourceError(
                VideoSourceErrorCode.SOURCE_UNAVAILABLE,
                f"YouTube did not return a valid {key}.",
            )
        return value.strip()

    @staticmethod
    def _optional_string(info: Mapping[str, object], key: str) -> str | None:
        value = info.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _invalid_source() -> VideoSourceError:
        return VideoSourceError(
            VideoSourceErrorCode.INVALID_SOURCE,
            "Provide a supported single-video YouTube URL.",
        )
