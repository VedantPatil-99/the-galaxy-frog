"""Unit coverage for collection-safe pgvector transcript retrieval."""

from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.db.models import (
    EmbeddingCollectionRow,
    RetrievalUnitCueRow,
    RetrievalUnitRow,
    TextEmbeddingRow,
)
from galaxy_frog.db.transcript_search import PgVectorTranscriptSearch, TranscriptIndexError
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec
from galaxy_frog.domain.retrieval.ports import TextEmbeddingProvider
from galaxy_frog.domain.transcripts.models import RetrievalUnit
from galaxy_frog.domain.videos.models import SafeVideoMetadata, SourceReference, VideoSourceKind
from galaxy_frog.domain.videos.records import TranscriptRecord, VideoRecord


class AllResult:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return self.values


class ExecuteResult(AllResult):
    pass


class FakeSession:
    def __init__(self) -> None:
        self.scalar_values: deque[object | None] = deque()
        self.scalars_values: deque[list[object]] = deque()
        self.execute_values: deque[list[object]] = deque()
        self.added: list[object] = []
        self.added_groups: list[tuple[object, ...]] = []
        self.flushes = 0
        self.commits = 0

    async def scalar(self, _statement: object) -> object | None:
        return self.scalar_values.popleft()

    async def scalars(self, _statement: object) -> AllResult:
        return AllResult(self.scalars_values.popleft())

    async def execute(self, _statement: object) -> ExecuteResult:
        return ExecuteResult(self.execute_values.popleft())

    def add(self, value: object) -> None:
        self.added.append(value)

    def add_all(self, values: Iterable[object]) -> None:
        self.added_groups.append(tuple(values))

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1


class FakeVideos:
    def __init__(self, transcripts: Iterable[TranscriptRecord | None]) -> None:
        self.transcripts = deque(transcripts)

    async def get_transcript(self, _video_id: UUID) -> TranscriptRecord | None:
        return self.transcripts.popleft()


class FakeProvider:
    spec = EmbeddingCollectionSpec("fixture", "bge-m3", "revision", 1024, "l2")

    def __init__(self, responses: Iterable[tuple[tuple[float, ...], ...]]) -> None:
        self.responses = deque(responses)
        self.inputs: list[tuple[str, ...]] = []

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.inputs.append(texts)
        return self.responses.popleft()


def transcript(*units: RetrievalUnit) -> TranscriptRecord:
    reference = SourceReference(
        VideoSourceKind.YOUTUBE,
        "dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    )
    video = VideoRecord(uuid4(), SafeVideoMetadata(reference, "Video", 3000))
    return TranscriptRecord(video, (), units)


def unit(identifier: str = "a", text: str = "Evidence.") -> RetrievalUnit:
    return RetrievalUnit(identifier * 64, 0, 1000, text, ((identifier.upper()) * 64,))


def collection() -> EmbeddingCollectionRow:
    return EmbeddingCollectionRow(
        id=uuid4(),
        provider="fixture",
        model="bge-m3",
        revision="revision",
        dimension=1024,
        normalization="l2",
        created_at=datetime.now(UTC),
    )


def search(
    session: FakeSession,
    videos: FakeVideos,
    provider: FakeProvider,
) -> PgVectorTranscriptSearch:
    return PgVectorTranscriptSearch(
        session=cast(AsyncSession, session),
        videos=cast(SqlAlchemyVideoRepository, videos),
        provider=cast(TextEmbeddingProvider, provider),
    )


@pytest.mark.asyncio
async def test_indexing_rejects_missing_video_and_incomplete_batch() -> None:
    video_id = uuid4()
    session = FakeSession()
    provider = FakeProvider(((),))
    adapter = search(session, FakeVideos((None,)), provider)
    with pytest.raises(TranscriptIndexError, match="does not exist"):
        await adapter.ensure_indexed(video_id)

    item = unit()
    session.scalar_values.append(None)
    session.scalars_values.append([])
    adapter = search(session, FakeVideos((transcript(item),)), provider)
    with pytest.raises(TranscriptIndexError, match="incomplete batch"):
        await adapter.ensure_indexed(video_id)


@pytest.mark.asyncio
async def test_indexing_creates_collection_embeddings_and_is_idempotent() -> None:
    item = unit()
    record = transcript(item)
    vector = (1.0, *([0.0] * 1023))
    provider = FakeProvider((((vector),),))
    session = FakeSession()
    session.scalar_values.append(None)
    session.scalars_values.append([])
    adapter = search(session, FakeVideos((record,)), provider)

    collection_id = await adapter.ensure_indexed(record.video.video_id)

    assert collection_id == cast(EmbeddingCollectionRow, session.added[0]).id
    assert session.flushes == 1
    assert session.commits == 1
    assert isinstance(session.added_groups[0][0], TextEmbeddingRow)
    assert provider.inputs == [(item.text,)]

    existing_collection = collection()
    session = FakeSession()
    session.scalar_values.append(existing_collection)
    session.scalars_values.append([item.unit_id])
    adapter = search(session, FakeVideos((record,)), FakeProvider(()))
    assert await adapter.ensure_indexed(record.video.video_id) == existing_collection.id
    assert session.commits == 0


@pytest.mark.asyncio
async def test_index_readiness_covers_absent_partial_and_complete_states() -> None:
    item = unit()
    empty = transcript()
    record = transcript(item)

    assert (
        await search(FakeSession(), FakeVideos((None,)), FakeProvider(())).is_indexed(uuid4())
        is False
    )
    assert (
        await search(FakeSession(), FakeVideos((empty,)), FakeProvider(())).is_indexed(uuid4())
        is False
    )

    session = FakeSession()
    session.scalar_values.append(None)
    assert (
        await search(session, FakeVideos((record,)), FakeProvider(())).is_indexed(
            record.video.video_id
        )
        is False
    )

    existing = collection()
    session = FakeSession()
    session.scalar_values.extend((existing, 0))
    assert (
        await search(session, FakeVideos((record,)), FakeProvider(())).is_indexed(
            record.video.video_id
        )
        is False
    )

    session = FakeSession()
    session.scalar_values.extend((existing, 1))
    assert (
        await search(session, FakeVideos((record,)), FakeProvider(())).is_indexed(
            record.video.video_id
        )
        is True
    )


@pytest.mark.parametrize(("query", "limit"), [(" ", 8), ("question", 0), ("question", 31)])
@pytest.mark.asyncio
async def test_search_rejects_invalid_inputs(query: str, limit: int) -> None:
    with pytest.raises(ValueError):
        await search(FakeSession(), FakeVideos(()), FakeProvider(())).search(
            uuid4(), query, limit=limit
        )


@pytest.mark.asyncio
async def test_search_returns_ranked_video_scoped_units_with_cues() -> None:
    units = (unit("a", "First"), unit("b", "Second"), unit("c", "Third"))
    record = transcript(*units)
    existing = collection()
    vector = (1.0, *([0.0] * 1023))
    provider = FakeProvider((((vector),),))
    session = FakeSession()
    session.scalar_values.append(existing)
    session.scalars_values.append([item.unit_id for item in units])
    rows = [
        RetrievalUnitRow(
            id=item.unit_id,
            video_id=record.video.video_id,
            start_ms=item.start_ms,
            end_ms=item.end_ms,
            text=item.text,
        )
        for item in units
    ]
    session.execute_values.append([(rows[0], -1.0), (rows[1], 0.25), (rows[2], 2.0)])
    session.scalars_values.append(
        [
            RetrievalUnitCueRow(
                retrieval_unit_id=item.unit_id,
                cue_id=item.cue_ids[0],
                cue_order=0,
            )
            for item in units
        ]
    )
    adapter = search(session, FakeVideos((record,)), provider)

    results = await adapter.search(record.video.video_id, "question", limit=3)

    assert [item.score for item in results] == [1.0, 0.75, 0.0]
    assert [item.unit.cue_ids for item in results] == [item.cue_ids for item in units]
    assert provider.inputs == [("question",)]


@pytest.mark.asyncio
async def test_search_handles_no_results_without_loading_links() -> None:
    item = unit()
    record = transcript(item)
    existing = collection()
    vector = (1.0, *([0.0] * 1023))
    session = FakeSession()
    session.scalar_values.append(existing)
    session.scalars_values.append([item.unit_id])
    session.execute_values.append([])
    adapter = search(session, FakeVideos((record,)), FakeProvider((((vector),),)))

    assert await adapter.search(record.video.video_id, "question") == ()
