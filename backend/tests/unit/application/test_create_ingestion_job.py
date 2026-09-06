"""Tests for canonical durable ingestion job creation."""

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest

from galaxy_frog.application.ingestion.create_job import (
    CreateIngestionJob,
    ingestion_input_fingerprint,
)
from galaxy_frog.application.ingestion.ports import IngestionRepository
from galaxy_frog.domain.ingestion.models import IngestionJob, IngestionJobStatus, IngestionStage
from galaxy_frog.domain.videos.models import (
    CaptionTrack,
    SafeVideoMetadata,
    SourceCaptionCue,
    SourceReference,
    VideoSourceKind,
)
from galaxy_frog.domain.videos.source import VideoSourceError, VideoSourceErrorCode

NOW = datetime(2026, 9, 5, tzinfo=UTC)
SOURCE = SourceReference(
    VideoSourceKind.YOUTUBE,
    "dQw4w9WgXcQ",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
)


class Source:
    source_kind = VideoSourceKind.YOUTUBE

    def __init__(self, error: VideoSourceError | None = None) -> None:
        self.error = error

    def canonicalize(self, locator: str) -> SourceReference:
        assert locator
        if self.error is not None:
            raise self.error
        return SOURCE

    async def fetch_metadata(self, reference: SourceReference) -> SafeVideoMetadata:
        raise AssertionError(reference)

    async def list_caption_tracks(self, reference: SourceReference) -> tuple[CaptionTrack, ...]:
        raise AssertionError(reference)

    async def fetch_caption_cues(
        self, reference: SourceReference, track: CaptionTrack
    ) -> tuple[SourceCaptionCue, ...]:
        raise AssertionError((reference, track))


class Repository:
    def __init__(self) -> None:
        self.arguments: tuple[SourceReference, str] | None = None
        self.job = IngestionJob(
            job_id=uuid4(),
            source=SOURCE,
            input_fingerprint="a" * 64,
            status=IngestionJobStatus.QUEUED,
            stage=IngestionStage.SOURCE_RESOLUTION,
            attempt=0,
            created_at=NOW,
            updated_at=NOW,
        )

    async def create_or_get(
        self, source: SourceReference, input_fingerprint: str, **kwargs: object
    ) -> tuple[IngestionJob, bool]:
        del kwargs
        self.arguments = (source, input_fingerprint)
        return self.job, True


def test_fingerprint_is_deterministic_normalized_and_input_sensitive() -> None:
    first = ingestion_input_fingerprint(
        SOURCE, pipeline_revision=" phase-2-v1 ", preferred_languages=("EN", "fr")
    )
    second = ingestion_input_fingerprint(
        SOURCE, pipeline_revision="phase-2-v1", preferred_languages=("en", "fr")
    )
    changed = ingestion_input_fingerprint(
        SOURCE, pipeline_revision="phase-2-v2", preferred_languages=("en", "fr")
    )
    assert first == second
    assert len(first) == 64
    assert changed != first


@pytest.mark.parametrize(
    ("revision", "languages"),
    [("", ("en",)), ("v1", ()), ("v1", (" ",))],
)
def test_fingerprint_rejects_missing_pipeline_inputs(
    revision: str, languages: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        ingestion_input_fingerprint(
            SOURCE,
            pipeline_revision=revision,
            preferred_languages=languages,
        )


@pytest.mark.asyncio
async def test_service_resolves_and_creates_the_canonical_job() -> None:
    repository = Repository()
    service = CreateIngestionJob(
        sources=(Source(),),
        repository=cast(IngestionRepository, repository),
    )
    result = await service.execute("https://youtu.be/dQw4w9WgXcQ")
    assert result.created is True
    assert result.job == repository.job
    assert repository.arguments is not None
    assert repository.arguments[0] == SOURCE
    assert len(repository.arguments[1]) == 64


@pytest.mark.asyncio
async def test_service_tries_other_sources_only_for_invalid_source() -> None:
    invalid = Source(VideoSourceError(VideoSourceErrorCode.INVALID_SOURCE, "invalid"))
    repository = Repository()
    result = await CreateIngestionJob(
        sources=(invalid, Source()),
        repository=cast(IngestionRepository, repository),
    ).execute("https://youtu.be/dQw4w9WgXcQ")
    assert result.job == repository.job


@pytest.mark.asyncio
async def test_service_preserves_source_failures_and_rejects_unsupported_locators() -> None:
    unavailable = VideoSourceError(
        VideoSourceErrorCode.SOURCE_UNAVAILABLE,
        "The source is unavailable.",
        retryable=True,
    )
    with pytest.raises(VideoSourceError, match="unavailable") as caught:
        await CreateIngestionJob(
            sources=(Source(unavailable),),
            repository=cast(IngestionRepository, Repository()),
        ).execute("https://youtu.be/dQw4w9WgXcQ")
    assert caught.value is unavailable

    invalid = Source(VideoSourceError(VideoSourceErrorCode.INVALID_SOURCE, "invalid"))
    with pytest.raises(VideoSourceError) as unsupported:
        await CreateIngestionJob(
            sources=(invalid,),
            repository=cast(IngestionRepository, Repository()),
        ).execute("https://example.com/video")
    assert unsupported.value.code is VideoSourceErrorCode.INVALID_SOURCE
