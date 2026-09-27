"""Opt-in real PostgreSQL lexical search and generated-column migration checks."""

import os
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from runpy import run_path
from typing import cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.application.retrieval.text import RetrieveTranscript
from galaxy_frog.config import Settings
from galaxy_frog.db.engine import create_database_engine
from galaxy_frog.db.models import EmbeddingCollectionRow, RetrievalUnitRow, VideoRow
from galaxy_frog.db.transcript_lexical_search import PostgresTranscriptLexicalSearch
from galaxy_frog.db.transcript_search import PgVectorTranscriptSearch
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.domain.retrieval.errors import EmbeddingProviderError
from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec
from galaxy_frog.domain.retrieval.pipeline import RetrievalMode
from galaxy_frog.domain.transcripts.models import RetrievalUnit, TranscriptCue
from galaxy_frog.domain.videos.models import (
    CaptionKind,
    SafeVideoMetadata,
    SourceCaptionCue,
    SourceReference,
    VideoSourceKind,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_INTEGRATION") != "1",
    reason="set RUN_DATABASE_INTEGRATION=1 with PostgreSQL/pgvector running",
)


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    command.upgrade(Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "head")


async def _seed(session: AsyncSession, passages: tuple[str, ...]) -> tuple[UUID, tuple[str, ...]]:
    external_id = uuid4().hex[:11]
    source = SourceReference(
        VideoSourceKind.YOUTUBE,
        external_id,
        f"https://www.youtube.com/watch?v={external_id}",
    )
    cues = tuple(
        TranscriptCue.from_source(
            source=source,
            track_id="manual:en",
            language_code="en",
            caption_kind=CaptionKind.MANUAL,
            cue=SourceCaptionCue(index, index * 10000, (index + 1) * 10000, passage),
        )
        for index, passage in enumerate(passages)
    )
    units = tuple(
        RetrievalUnit(
            sha256((source.external_id + str(index)).encode()).hexdigest(),
            cue.start_ms,
            cue.end_ms,
            cue.text,
            (cue.cue_id,),
        )
        for index, cue in enumerate(cues)
    )
    video = await SqlAlchemyVideoRepository(session).save_import(
        SafeVideoMetadata(source, "Lexical integration fixture", len(passages) * 10000), cues, units
    )
    return video.video_id, tuple(unit.unit_id for unit in units)


@pytest.mark.asyncio
async def test_real_lexical_search_is_scoped_and_preserves_existing_transcript() -> None:
    engine = create_database_engine(Settings())
    video_ids: list[UUID] = []
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            video_id, unit_ids = await _seed(
                session,
                (
                    "PostgreSQL preserves exact timestamps and cue provenance.",
                    "The timestamps are exact but appear in reverse order.",
                    "Kubernetes and München are searchable names.",
                ),
            )
            video_ids.append(video_id)
            other_id, _ = await _seed(session, ("exact timestamps exact timestamps",))
            video_ids.append(other_id)
            repository = SqlAlchemyVideoRepository(session)
            before = await repository.get_transcript(video_id)
            adapter = PostgresTranscriptLexicalSearch(session)

            phrase = await adapter.search(video_id, '"exact timestamps"', limit=30)
            assert len(phrase) == 1
            assert phrase[0].unit.unit_id == unit_ids[0]
            assert phrase[0].video_id == video_id
            assert (phrase[0].unit.start_ms, phrase[0].unit.end_ms) == (0, 10000)
            assert before is not None
            assert phrase[0].unit.cue_ids == before.units[0].cue_ids
            assert 0 < phrase[0].score < 1
            for query in ("KUBERNETES", "München", "Kubernetes OR nonexistent"):
                found = await adapter.search(video_id, query, limit=1)
                assert [item.unit.unit_id for item in found] == [unit_ids[2]]
            assert await adapter.search(video_id, "nonexistent") == ()
            assert await adapter.search(video_id, "!!!") == ()
            assert await adapter.search(uuid4(), "exact") == ()
            assert len(await adapter.search(video_id, "exact", limit=1)) == 1
            assert await repository.get_transcript(video_id) == before

            # A text update maintains the derived vector without a worker or reindex call.
            row = await session.get(RetrievalUnitRow, unit_ids[2])
            assert row is not None
            row.text = "Zebracorn is the updated term."
            await session.commit()
            assert await adapter.search(video_id, "Kubernetes") == ()
            updated = await adapter.search(video_id, "Zebracorn")
            assert updated[0].unit.unit_id == unit_ids[2]
            assert updated[0].unit.cue_ids == before.units[2].cue_ids
    finally:
        async with AsyncSession(engine) as cleanup:
            await cleanup.execute(delete(VideoRow).where(VideoRow.id.in_(video_ids)))
            await cleanup.commit()
        await engine.dispose()


def _check_migration(connection: Connection) -> None:
    """Shadow the real table with a transaction-local pre-FTS table; never downgrade public."""

    connection.execute(
        text(
            "CREATE TEMP TABLE retrieval_units (id text PRIMARY KEY, text text NOT NULL) ON COMMIT DROP"
        )
    )
    connection.execute(
        text("INSERT INTO retrieval_units VALUES ('existing', 'durable provenance')")
    )
    migration = run_path(
        str(
            Path(__file__).resolve().parents[2]
            / "migrations/versions/20260927_0006_transcript_fts.py"
        )
    )
    with Operations.context(MigrationContext.configure(connection)):
        cast(Callable[[], None], migration["upgrade"])()
        assert (
            connection.scalar(
                text(
                    "SELECT id FROM retrieval_units WHERE search_vector @@ plainto_tsquery('simple', 'provenance')"
                )
            )
            == "existing"
        )
        connection.execute(
            text("INSERT INTO retrieval_units (id, text) VALUES ('new', 'fresh timestamp')")
        )
        assert (
            connection.scalar(
                text(
                    "SELECT id FROM retrieval_units WHERE search_vector @@ plainto_tsquery('simple', 'timestamp')"
                )
            )
            == "new"
        )
        assert connection.scalar(text("SELECT count(*) FROM retrieval_units")) == 2
        cast(Callable[[], None], migration["downgrade"])()
        assert (
            connection.scalar(text("SELECT text FROM retrieval_units WHERE id = 'existing'"))
            == "durable provenance"
        )
        cast(Callable[[], None], migration["upgrade"])()
        assert (
            connection.scalar(
                text("SELECT count(*) FROM retrieval_units WHERE search_vector IS NOT NULL")
            )
            == 2
        )


@pytest.mark.asyncio
async def test_actual_migration_backfills_and_roundtrips_without_losing_text() -> None:
    engine = create_database_engine(Settings())
    try:
        async with engine.begin() as connection:
            await connection.run_sync(_check_migration)
        async with engine.connect() as connection:
            # The application table still has its own generated column after the temporary test.
            await connection.execute(select(RetrievalUnitRow.search_vector).limit(1))
    finally:
        await engine.dispose()


class FixtureEmbeddings:
    """Deliberately rank the semantic fixture ahead of the lexical fixture."""

    def __init__(self) -> None:
        self.spec = EmbeddingCollectionSpec("fixture", "hybrid", uuid4().hex, 1024, "l2")
        self.inputs: list[tuple[str, ...]] = []
        self.fail = False

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.inputs.append(texts)
        if self.fail:
            raise EmbeddingProviderError("fixture unavailable")
        return tuple(
            (1.0, 0.0, *([0.0] * 1022))
            if text.startswith("The quokka")
            else (0.0, 1.0, *([0.0] * 1022))
            for text in texts
        )


@pytest.mark.asyncio
async def test_shared_hybrid_reuses_real_index_and_preserves_lineage_on_fallback() -> None:
    engine = create_database_engine(Settings())
    provider = FixtureEmbeddings()
    video_ids: list[UUID] = []
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            video_id, unit_ids = await _seed(
                session, ("The quokka is an exact name.", "A semantic fixture about the animal.")
            )
            video_ids.append(video_id)
            other_id, _ = await _seed(session, ("The quokka belongs to another video.",))
            video_ids.append(other_id)
            videos = SqlAlchemyVideoRepository(session)
            before = await videos.get_transcript(video_id)
            dense = PgVectorTranscriptSearch(session=session, videos=videos, provider=provider)
            service = RetrieveTranscript(
                lexical=PostgresTranscriptLexicalSearch(session), dense=dense
            )
            result = await service.execute(video_id, 'Explain "quokka"', allow_fallback=False)
            assert result.stages[0].results[0].unit.unit_id == unit_ids[0]
            assert result.stages[1].results[0].unit.unit_id == unit_ids[1]
            assert result.evidence[0].unit.unit_id == unit_ids[0]
            assert len(result.evidence[0].stages) == 2
            assert all(row.video_id == video_id for row in result.rankings)
            assert len(provider.inputs) == 2  # Missing-unit batch, then query.
            repeated = await service.execute(video_id, 'Explain "quokka"')
            assert repeated.rankings == result.rankings
            assert len(provider.inputs) == 3  # Existing units were not embedded again.
            assert await videos.get_transcript(video_id) == before

            provider.fail = True
            fallback = await service.execute(video_id, 'Explain "quokka"')
            assert fallback.degraded
            assert fallback.evidence[0].unit == result.evidence[0].unit
            assert fallback.stages[1].failure_code == "embedding_provider_unavailable"
            lexical = await service.execute(video_id, "quokka", mode=RetrievalMode.LEXICAL)
            assert not lexical.degraded
            with pytest.raises(EmbeddingProviderError):
                await service.execute(video_id, "quokka", allow_fallback=False)
    finally:
        async with AsyncSession(engine) as cleanup:
            await cleanup.execute(delete(VideoRow).where(VideoRow.id.in_(video_ids)))
            await cleanup.execute(
                delete(EmbeddingCollectionRow).where(
                    EmbeddingCollectionRow.revision == provider.spec.revision
                )
            )
            await cleanup.commit()
        await engine.dispose()
