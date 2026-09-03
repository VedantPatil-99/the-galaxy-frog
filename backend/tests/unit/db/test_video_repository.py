"""Unit coverage for SQLAlchemy transcript persistence projections."""

from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.db.models import (
    RetrievalUnitCueRow,
    RetrievalUnitRow,
    TranscriptCueRow,
    VideoRow,
)
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.domain.transcripts.models import RetrievalUnit, TranscriptCue
from galaxy_frog.domain.videos.models import (
    CaptionKind,
    SafeVideoMetadata,
    SourceCaptionCue,
    SourceReference,
    VideoSourceKind,
)


class AllResult:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return self.values


class FakeSession:
    def __init__(self) -> None:
        self.scalar_values: deque[object | None] = deque()
        self.get_values: deque[object | None] = deque()
        self.scalars_values: deque[list[object]] = deque()
        self.added: list[object] = []
        self.added_groups: list[tuple[object, ...]] = []
        self.flushes = 0
        self.commits = 0

    async def scalar(self, _statement: object) -> object | None:
        return self.scalar_values.popleft()

    async def get(self, _model: object, _identity: UUID) -> object | None:
        return self.get_values.popleft()

    async def scalars(self, _statement: object) -> AllResult:
        return AllResult(self.scalars_values.popleft())

    def add(self, value: object) -> None:
        self.added.append(value)

    def add_all(self, values: Iterable[object]) -> None:
        self.added_groups.append(tuple(values))

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1


def values() -> tuple[SafeVideoMetadata, TranscriptCue, RetrievalUnit]:
    reference = SourceReference(
        VideoSourceKind.YOUTUBE,
        "dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    )
    metadata = SafeVideoMetadata(reference, "Video", 1000, "Channel")
    cue = TranscriptCue.from_source(
        source=reference,
        track_id="manual:en",
        language_code="en",
        caption_kind=CaptionKind.MANUAL,
        cue=SourceCaptionCue(0, 0, 1000, "Evidence."),
    )
    unit = RetrievalUnit("a" * 64, 0, 1000, "Evidence.", (cue.cue_id,))
    return metadata, cue, unit


def video_row(metadata: SafeVideoMetadata, video_id: UUID | None = None) -> VideoRow:
    now = datetime.now(UTC)
    return VideoRow(
        id=video_id or uuid4(),
        source_kind=metadata.reference.kind,
        external_id=metadata.reference.external_id,
        canonical_url=metadata.reference.canonical_url,
        title=metadata.title,
        duration_ms=metadata.duration_ms,
        channel_name=metadata.channel_name,
        thumbnail_url=metadata.thumbnail_url,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_find_get_and_existing_save_paths() -> None:
    metadata, cue, unit = values()
    row = video_row(metadata)
    session = FakeSession()
    session.scalar_values.extend((None, row, row))
    session.get_values.extend((None, row))
    repository = SqlAlchemyVideoRepository(cast(AsyncSession, session))

    assert repository.session is cast(object, session)
    assert await repository.find_by_source(metadata.reference) is None
    found = await repository.find_by_source(metadata.reference)
    assert found is not None and found.video_id == row.id
    existing = await repository.save_import(metadata, (cue,), (unit,))
    assert existing.video_id == row.id
    assert await repository.get_video(uuid4()) is None
    assert (await repository.get_video(row.id)) == found


@pytest.mark.asyncio
async def test_save_builds_all_provenance_rows_and_commits() -> None:
    metadata, cue, unit = values()
    session = FakeSession()
    session.scalar_values.append(None)
    repository = SqlAlchemyVideoRepository(cast(AsyncSession, session))

    saved = await repository.save_import(metadata, (cue,), (unit,))

    assert saved.metadata == metadata
    assert len(session.added) == 1
    assert isinstance(session.added[0], VideoRow)
    assert [type(group[0]) for group in session.added_groups] == [
        TranscriptCueRow,
        RetrievalUnitRow,
        RetrievalUnitCueRow,
    ]
    assert session.commits == 1
    assert session.flushes == 2


@pytest.mark.asyncio
async def test_get_transcript_reconstructs_ordered_provenance() -> None:
    metadata, cue, unit = values()
    row = video_row(metadata)
    cue_row = TranscriptCueRow(
        id=cue.cue_id,
        video_id=row.id,
        track_id=cue.track_id,
        language_code=cue.language_code,
        caption_kind=cue.caption_kind,
        source_order=cue.source_order,
        start_ms=cue.start_ms,
        end_ms=cue.end_ms,
        text=cue.text,
    )
    unit_row = RetrievalUnitRow(
        id=unit.unit_id,
        video_id=row.id,
        start_ms=unit.start_ms,
        end_ms=unit.end_ms,
        text=unit.text,
    )
    link = RetrievalUnitCueRow(
        retrieval_unit_id=unit.unit_id,
        cue_id=cue.cue_id,
        cue_order=0,
    )
    session = FakeSession()
    session.get_values.extend((None, row))
    session.scalars_values.extend(([cue_row], [unit_row], [link]))
    repository = SqlAlchemyVideoRepository(cast(AsyncSession, session))

    assert await repository.get_transcript(uuid4()) is None
    transcript = await repository.get_transcript(row.id)

    assert transcript is not None
    assert transcript.video.video_id == row.id
    assert transcript.cues == (cue,)
    assert transcript.units == (unit,)
