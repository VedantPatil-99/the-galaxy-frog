"""Unit tests for PostgresOCRLexicalSearch and PgVectorOCRSearch."""

from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from galaxy_frog.db.models import (
    EmbeddingCollectionRow,
    OCRBlockRow,
    VideoFrameRow,
)
from galaxy_frog.db.ocr_search import PgVectorOCRSearch, PostgresOCRLexicalSearch
from galaxy_frog.domain.retrieval.errors import OCRIndexError
from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec
from galaxy_frog.domain.retrieval.temporal import TimeWindow


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
        self.commits = 0
        self.flushes = 0
        self.statements: list[object] = []

    async def scalar(self, statement: object) -> object | None:
        self.statements.append(statement)
        return self.scalar_values.popleft()

    async def scalars(self, statement: object) -> AllResult:
        self.statements.append(statement)
        return AllResult(self.scalars_values.popleft())

    async def execute(self, statement: object) -> ExecuteResult:
        self.statements.append(statement)
        return ExecuteResult(self.execute_values.popleft())

    def add(self, value: object) -> None:
        self.added.append(value)

    def add_all(self, values: Iterable[object]) -> None:
        self.added_groups.append(tuple(values))

    async def flush(self) -> None:
        self.flushes += 1

    async def commit(self) -> None:
        self.commits += 1


class FakeEmbedder:
    def __init__(
        self,
        spec: EmbeddingCollectionSpec,
        vectors: Iterable[tuple[float, ...]] = (),
    ) -> None:
        self.spec = spec
        self.vectors = deque(vectors)
        self.embedded: list[tuple[str, ...]] = []

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.embedded.append(texts)
        return tuple(self.vectors.popleft() for _ in texts)


_SPEC = EmbeddingCollectionSpec(
    provider="fastembed",
    model="bge-m3",
    revision="1.0.0",
    dimension=1024,
    normalization="l2",
)


# ---------------------------------------------------------------------------
# Lexical Search Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("bad_query", "limit"), [("   ", 10), ("", 10)])
@pytest.mark.asyncio
async def test_lexical_empty_query_rejected(bad_query: str, limit: int) -> None:
    session = FakeSession()
    searcher = PostgresOCRLexicalSearch(session)  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValueError, match="query must not be empty"):
        await searcher.search(uuid4(), bad_query, limit=limit)


@pytest.mark.parametrize("bad_limit", [0, 31, -5])
@pytest.mark.asyncio
async def test_lexical_invalid_limit_rejected(bad_limit: int) -> None:
    session = FakeSession()
    searcher = PostgresOCRLexicalSearch(session)  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValueError, match="limit must be between 1 and 30"):
        await searcher.search(uuid4(), "query", limit=bad_limit)


@pytest.mark.asyncio
async def test_lexical_empty_window_returns_empty() -> None:
    session = FakeSession()
    searcher = PostgresOCRLexicalSearch(session)  # pyright: ignore[reportArgumentType]
    result = await searcher.search(uuid4(), "query", window=TimeWindow(100, 100))
    assert result == ()
    assert len(session.statements) == 0


@pytest.mark.asyncio
async def test_lexical_no_matches_returns_empty() -> None:
    session = FakeSession()
    session.execute_values.append([])
    searcher = PostgresOCRLexicalSearch(session)  # pyright: ignore[reportArgumentType]
    result = await searcher.search(uuid4(), "nonexistent")
    assert result == ()
    assert len(session.statements) == 1


@pytest.mark.asyncio
async def test_lexical_matches_with_and_without_window() -> None:
    video_id = uuid4()
    frame_id = uuid4()
    block_id = uuid4()
    now = datetime.now(UTC)

    block_row = OCRBlockRow(
        id=block_id,
        video_id=video_id,
        frame_id=frame_id,
        text="Deep learning slide",
        start_ms=1000,
        end_ms=4000,
        confidence=0.95,
        confidence_method="average",
        provider="llamaparse",
        provider_revision="1",
        created_at=now,
    )
    frame_row = VideoFrameRow(
        id=frame_id,
        video_id=video_id,
        scene_id=None,
        sequence=3,
        start_ms=1000,
        end_ms=4000,
        source="scene_boundary",
        dhash="0123456789abcdef",
        is_duplicate=False,
        image_path="C:/tmp/frame_003.jpg",
        created_at=now,
    )

    session = FakeSession()
    session.execute_values.append([(block_row, frame_row, 0.42)])
    searcher = PostgresOCRLexicalSearch(session)  # pyright: ignore[reportArgumentType]

    results = await searcher.search(
        video_id,
        "deep learning",
        limit=5,
        window=TimeWindow(500, 5000),
    )

    assert len(results) == 1
    evidence = results[0]
    assert evidence.video_id == video_id
    assert evidence.block_id == block_id
    assert evidence.frame_id == frame_id
    assert evidence.score == 0.42
    assert evidence.block.text == "Deep learning slide"
    assert evidence.block.start_ms == 1000
    assert evidence.block.end_ms == 4000
    assert evidence.block.source_frame_sequence == 3
    assert evidence.block.provider_spec.provider == "llamaparse"
    assert evidence.block.confidence == 0.95
    assert evidence.block.confidence_method == "average"
    assert evidence.image_path == Path("C:/tmp/frame_003.jpg")

    stmt = str(session.statements[-1])
    assert "ocr_blocks.end_ms >" in stmt
    assert "ocr_blocks.start_ms <" in stmt


@pytest.mark.asyncio
async def test_lexical_open_ended_window() -> None:
    video_id = uuid4()
    session = FakeSession()
    session.execute_values.append([])
    searcher = PostgresOCRLexicalSearch(session)  # pyright: ignore[reportArgumentType]

    await searcher.search(
        video_id,
        "term",
        window=TimeWindow(1000),
    )

    stmt = str(session.statements[-1])
    assert "ocr_blocks.end_ms >" in stmt
    assert "ocr_blocks.start_ms <" not in stmt


# ---------------------------------------------------------------------------
# Dense Vector Search Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("bad_query", "limit"), [("   ", 10), ("", 10)])
@pytest.mark.asyncio
async def test_vector_empty_query_rejected(bad_query: str, limit: int) -> None:
    session = FakeSession()
    embedder = FakeEmbedder(_SPEC)
    searcher = PgVectorOCRSearch(session=session, provider=embedder)  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValueError, match="query must not be empty"):
        await searcher.search(uuid4(), bad_query, limit=limit)


@pytest.mark.parametrize("bad_limit", [0, 31, -1])
@pytest.mark.asyncio
async def test_vector_invalid_limit_rejected(bad_limit: int) -> None:
    session = FakeSession()
    embedder = FakeEmbedder(_SPEC)
    searcher = PgVectorOCRSearch(session=session, provider=embedder)  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValueError, match="limit must be between 1 and 30"):
        await searcher.search(uuid4(), "query", limit=bad_limit)


@pytest.mark.asyncio
async def test_vector_empty_window_returns_empty() -> None:
    session = FakeSession()
    embedder = FakeEmbedder(_SPEC)
    searcher = PgVectorOCRSearch(session=session, provider=embedder)  # pyright: ignore[reportArgumentType]
    result = await searcher.search(uuid4(), "query", window=TimeWindow(200, 200))
    assert result == ()


@pytest.mark.asyncio
async def test_ensure_indexed_no_blocks_returns_collection_id() -> None:
    session = FakeSession()
    # 1. _find_collection returns existing collection
    coll_id = uuid4()
    coll = EmbeddingCollectionRow(
        id=coll_id,
        provider=_SPEC.provider,
        model=_SPEC.model,
        revision=_SPEC.revision,
        dimension=_SPEC.dimension,
        normalization=_SPEC.normalization,
        created_at=datetime.now(UTC),
    )
    session.scalar_values.append(coll)
    # 2. scalars query for OCRBlockRow returns empty
    session.scalars_values.append([])

    embedder = FakeEmbedder(_SPEC)
    searcher = PgVectorOCRSearch(session=session, provider=embedder)  # pyright: ignore[reportArgumentType]
    res_id = await searcher.ensure_indexed(uuid4())
    assert res_id == coll_id
    assert len(embedder.embedded) == 0


@pytest.mark.asyncio
async def test_ensure_indexed_creates_collection_and_embeds_missing() -> None:
    session = FakeSession()
    # 1. _find_collection returns None (new collection)
    session.scalar_values.append(None)
    # 2. query blocks for video: returns 2 blocks
    b1_id = uuid4()
    b2_id = uuid4()
    b1 = OCRBlockRow(
        id=b1_id,
        video_id=uuid4(),
        frame_id=uuid4(),
        text="First block",
        start_ms=0,
        end_ms=1000,
        confidence=None,
        confidence_method=None,
        provider="test",
        provider_revision="1",
        created_at=datetime.now(UTC),
    )
    b2 = OCRBlockRow(
        id=b2_id,
        video_id=uuid4(),
        frame_id=uuid4(),
        text="Second block",
        start_ms=1000,
        end_ms=2000,
        confidence=None,
        confidence_method=None,
        provider="test",
        provider_revision="1",
        created_at=datetime.now(UTC),
    )
    session.scalars_values.append([b1, b2])
    # 3. query existing embeddings: b1 already embedded
    session.scalars_values.append([b1_id])

    vector2 = tuple(0.1 for _ in range(1024))
    embedder = FakeEmbedder(_SPEC, vectors=[vector2])
    searcher = PgVectorOCRSearch(session=session, provider=embedder)  # pyright: ignore[reportArgumentType]

    coll_id = await searcher.ensure_indexed(uuid4())
    assert isinstance(coll_id, UUID)
    assert session.flushes == 1
    assert session.commits == 1
    assert len(session.added) == 1  # EmbeddingCollectionRow
    assert len(session.added_groups) == 1  # OCREmbeddingRow group
    assert embedder.embedded == [("Second block",)]


@pytest.mark.asyncio
async def test_ensure_indexed_incomplete_batch_raises() -> None:
    session = FakeSession()
    # 1. _find_collection returns existing
    coll = EmbeddingCollectionRow(
        id=uuid4(),
        provider=_SPEC.provider,
        model=_SPEC.model,
        revision=_SPEC.revision,
        dimension=_SPEC.dimension,
        normalization=_SPEC.normalization,
        created_at=datetime.now(UTC),
    )
    session.scalar_values.append(coll)
    # 2. query blocks: 1 block
    b1 = OCRBlockRow(
        id=uuid4(),
        video_id=uuid4(),
        frame_id=uuid4(),
        text="Block",
        start_ms=0,
        end_ms=1000,
        confidence=None,
        confidence_method=None,
        provider="test",
        provider_revision="1",
        created_at=datetime.now(UTC),
    )
    session.scalars_values.append([b1])
    # 3. existing embeddings: none
    session.scalars_values.append([])

    # embedder returns 0 vectors instead of 1
    class IncompleteEmbedder:
        spec = _SPEC

        async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            return ()

    searcher = PgVectorOCRSearch(session=session, provider=IncompleteEmbedder())  # pyright: ignore[reportArgumentType]
    with pytest.raises(OCRIndexError, match="incomplete batch"):
        await searcher.ensure_indexed(uuid4())


@pytest.mark.asyncio
async def test_is_indexed() -> None:
    session = FakeSession()
    embedder = FakeEmbedder(_SPEC)
    searcher = PgVectorOCRSearch(session=session, provider=embedder)  # pyright: ignore[reportArgumentType]

    # Case 1: collection does not exist -> False
    session.scalar_values.append(None)
    assert await searcher.is_indexed(uuid4()) is False

    # Case 2: collection exists, total_blocks is 0 -> False
    coll = EmbeddingCollectionRow(
        id=uuid4(),
        provider=_SPEC.provider,
        model=_SPEC.model,
        revision=_SPEC.revision,
        dimension=_SPEC.dimension,
        normalization=_SPEC.normalization,
        created_at=datetime.now(UTC),
    )
    session.scalar_values.append(coll)
    session.scalar_values.append(0)
    assert await searcher.is_indexed(uuid4()) is False

    # Case 3: collection exists, total_blocks=5, embedded=3 -> False
    session.scalar_values.append(coll)
    session.scalar_values.append(5)
    session.scalar_values.append(3)
    assert await searcher.is_indexed(uuid4()) is False

    # Case 4: collection exists, total_blocks=5, embedded=5 -> True
    session.scalar_values.append(coll)
    session.scalar_values.append(5)
    session.scalar_values.append(5)
    assert await searcher.is_indexed(uuid4()) is True


@pytest.mark.asyncio
async def test_vector_search_success() -> None:
    video_id = uuid4()
    coll_id = uuid4()
    block_id = uuid4()
    frame_id = uuid4()
    now = datetime.now(UTC)

    session = FakeSession()
    # ensure_indexed:
    # 1. _find_collection -> coll
    coll = EmbeddingCollectionRow(
        id=coll_id,
        provider=_SPEC.provider,
        model=_SPEC.model,
        revision=_SPEC.revision,
        dimension=_SPEC.dimension,
        normalization=_SPEC.normalization,
        created_at=now,
    )
    session.scalar_values.append(coll)
    # 2. query blocks -> empty (already indexed)
    session.scalars_values.append([])

    # search query execution:
    block_row = OCRBlockRow(
        id=block_id,
        video_id=video_id,
        frame_id=frame_id,
        text="Matching slide text",
        start_ms=2000,
        end_ms=6000,
        confidence=None,
        confidence_method=None,
        provider="llamaparse",
        provider_revision="1",
        created_at=now,
    )
    frame_row = VideoFrameRow(
        id=frame_id,
        video_id=video_id,
        scene_id=None,
        sequence=2,
        start_ms=2000,
        end_ms=6000,
        source="periodic",
        dhash=None,
        is_duplicate=False,
        image_path="C:/tmp/frame_002.jpg",
        created_at=now,
    )
    # distance = 0.2, score = 1.0 - 0.2 = 0.8
    session.execute_values.append([(block_row, frame_row, 0.2)])

    query_vec = tuple(0.05 for _ in range(1024))
    embedder = FakeEmbedder(_SPEC, vectors=[query_vec])
    searcher = PgVectorOCRSearch(session=session, provider=embedder)  # pyright: ignore[reportArgumentType]

    results = await searcher.search(
        video_id,
        "query term",
        limit=5,
        window=TimeWindow(1500, 7000),
    )

    assert len(results) == 1
    res = results[0]
    assert res.video_id == video_id
    assert res.block_id == block_id
    assert res.frame_id == frame_id
    assert abs(res.score - 0.8) < 1e-5
    assert res.block.text == "Matching slide text"
    assert res.block.source_frame_sequence == 2
    assert res.image_path == Path("C:/tmp/frame_002.jpg")

    stmt = str(session.statements[-1])
    assert "ocr_blocks.end_ms >" in stmt
    assert "ocr_blocks.start_ms <" in stmt


@pytest.mark.asyncio
async def test_ensure_indexed_all_already_indexed() -> None:
    session = FakeSession()
    coll = EmbeddingCollectionRow(
        id=uuid4(),
        provider=_SPEC.provider,
        model=_SPEC.model,
        revision=_SPEC.revision,
        dimension=_SPEC.dimension,
        normalization=_SPEC.normalization,
        created_at=datetime.now(UTC),
    )
    session.scalar_values.append(coll)
    b_id = uuid4()
    b = OCRBlockRow(
        id=b_id,
        video_id=uuid4(),
        frame_id=uuid4(),
        text="Block",
        start_ms=0,
        end_ms=1000,
        confidence=None,
        confidence_method=None,
        provider="test",
        provider_revision="1",
        created_at=datetime.now(UTC),
    )
    session.scalars_values.append([b])
    session.scalars_values.append([b_id])

    embedder = FakeEmbedder(_SPEC)
    searcher = PgVectorOCRSearch(session=session, provider=embedder)  # pyright: ignore[reportArgumentType]
    await searcher.ensure_indexed(uuid4())
    assert len(embedder.embedded) == 0
    assert session.commits == 0


@pytest.mark.asyncio
async def test_vector_search_without_window_and_open_ended_window() -> None:
    video_id = uuid4()
    now = datetime.now(UTC)
    coll = EmbeddingCollectionRow(
        id=uuid4(),
        provider=_SPEC.provider,
        model=_SPEC.model,
        revision=_SPEC.revision,
        dimension=_SPEC.dimension,
        normalization=_SPEC.normalization,
        created_at=now,
    )
    block_row = OCRBlockRow(
        id=uuid4(),
        video_id=video_id,
        frame_id=uuid4(),
        text="Slide",
        start_ms=0,
        end_ms=1000,
        confidence=None,
        confidence_method=None,
        provider="test",
        provider_revision="1",
        created_at=now,
    )
    frame_row = VideoFrameRow(
        id=uuid4(),
        video_id=video_id,
        scene_id=None,
        sequence=0,
        start_ms=0,
        end_ms=1000,
        source="periodic",
        dhash=None,
        is_duplicate=False,
        image_path="C:/tmp/frame_000.jpg",
        created_at=now,
    )

    # 1. search without window
    session = FakeSession()
    session.scalar_values.append(coll)
    session.scalars_values.append([])
    session.execute_values.append([(block_row, frame_row, 0.1)])

    embedder = FakeEmbedder(_SPEC, vectors=[tuple(0.01 for _ in range(1024))])
    searcher = PgVectorOCRSearch(session=session, provider=embedder)  # pyright: ignore[reportArgumentType]
    res = await searcher.search(video_id, "test", window=None)
    assert len(res) == 1
    stmt_no_win = str(session.statements[-1])
    assert "ocr_blocks.end_ms >" not in stmt_no_win
    assert "ocr_blocks.start_ms <" not in stmt_no_win

    # 2. search with open-ended window
    session = FakeSession()
    session.scalar_values.append(coll)
    session.scalars_values.append([])
    session.execute_values.append([(block_row, frame_row, 0.1)])

    embedder2 = FakeEmbedder(_SPEC, vectors=[tuple(0.01 for _ in range(1024))])
    searcher2 = PgVectorOCRSearch(session=session, provider=embedder2)  # pyright: ignore[reportArgumentType]
    res2 = await searcher2.search(video_id, "test", window=TimeWindow(500))
    assert len(res2) == 1
    stmt_open = str(session.statements[-1])
    assert "ocr_blocks.end_ms >" in stmt_open
    assert "ocr_blocks.start_ms <" not in stmt_open
