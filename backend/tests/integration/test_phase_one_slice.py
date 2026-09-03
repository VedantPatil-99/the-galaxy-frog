"""Opt-in PostgreSQL integration coverage for the Phase 1 vertical slice."""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from galaxy_frog.api.app import create_app
from galaxy_frog.config import Settings
from galaxy_frog.db.models import RetrievalUnitRow, TranscriptCueRow, VideoRow
from galaxy_frog.domain.generation.models import (
    AnswerConfidence,
    GenerationCitation,
    GenerationDraft,
)
from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec, RetrievedEvidence
from galaxy_frog.domain.videos.models import (
    CaptionKind,
    CaptionTrack,
    SafeVideoMetadata,
    SourceCaptionCue,
    SourceReference,
    VideoSourceKind,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_INTEGRATION") != "1",
    reason="set RUN_DATABASE_INTEGRATION=1 with the local pgvector database running",
)


class FixtureSource:
    """Deterministic caption-only source used through the real HTTP import path."""

    source_kind = VideoSourceKind.YOUTUBE

    def __init__(self) -> None:
        self.external_id = uuid4().hex[:11]

    def canonicalize(self, locator: str) -> SourceReference:
        assert locator == "https://youtu.be/integration"
        return SourceReference(
            self.source_kind,
            self.external_id,
            f"https://www.youtube.com/watch?v={self.external_id}",
        )

    async def fetch_metadata(self, reference: SourceReference) -> SafeVideoMetadata:
        return SafeVideoMetadata(reference, "Integration fixture", 30_000, "Galaxy Frog")

    async def list_caption_tracks(self, reference: SourceReference) -> tuple[CaptionTrack, ...]:
        del reference
        return (CaptionTrack("manual:en", "en", CaptionKind.MANUAL),)

    async def fetch_caption_cues(
        self,
        reference: SourceReference,
        track: CaptionTrack,
    ) -> tuple[SourceCaptionCue, ...]:
        del reference, track
        return (
            SourceCaptionCue(0, 0, 10_000, "Galaxy Frog preserves exact timestamps."),
            SourceCaptionCue(1, 10_000, 20_000, "Every unit retains ordered cue provenance."),
            SourceCaptionCue(2, 20_000, 30_000, "Citations seek back to source evidence."),
        )


class FixtureEmbeddings:
    """Stable 1,024-dimensional vectors without an external model service."""

    spec = EmbeddingCollectionSpec("fixture", "bge-m3", "integration", 1024, "l2")

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((1.0, *([0.0] * 1023)) for _text in texts)


class FixtureGeneration:
    """Ground a deterministic draft in the first retrieved unit."""

    async def generate(
        self,
        question: str,
        evidence: tuple[RetrievedEvidence, ...],
    ) -> GenerationDraft:
        assert question
        first = evidence[0]
        return GenerationDraft(
            answer="It preserves exact timestamps and cue provenance.",
            confidence=AnswerConfidence.HIGH,
            citations=(GenerationCitation(first.unit.unit_id, first.unit.text),),
        )


def _database_url() -> str:
    settings = Settings()
    if settings.database_url is None:
        pytest.fail("DATABASE_URL is required when RUN_DATABASE_INTEGRATION=1")
    return settings.database_url.get_secret_value()


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    """Apply the real Alembic chain before exercising repositories and pgvector."""

    backend_root = Path(__file__).resolve().parents[2]
    command.upgrade(Config(str(backend_root / "alembic.ini")), "head")


@pytest.mark.asyncio
async def test_import_retrieval_and_question_endpoints_are_idempotent() -> None:
    settings = Settings(app_env="test", database_url=SecretStr(_database_url()))
    application = create_app(settings)
    source = FixtureSource()
    application.state.video_sources = (source,)
    application.state.embedding_provider_factory = FixtureEmbeddings
    application.state.generation_provider_factory = FixtureGeneration

    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.post(
                "/v1/videos/import",
                json={"source_url": "https://youtu.be/integration"},
            )
            second = await client.post(
                "/v1/videos/import",
                json={"source_url": "https://youtu.be/integration"},
            )

            assert first.status_code == 200, first.text
            assert second.status_code == 200, second.text
            first_body = cast(Mapping[str, object], first.json())
            second_body = cast(Mapping[str, object], second.json())
            first_video = cast(Mapping[str, object], first_body["video"])
            second_video = cast(Mapping[str, object], second_body["video"])
            video_id = UUID(cast(str, first_video["video_id"]))
            assert first_body["reused"] is False
            assert second_body["reused"] is True
            assert second_video["video_id"] == first_video["video_id"]
            assert first_video["index_ready"] is True

            detail = await client.get(f"/v1/videos/{video_id}")
            transcript = await client.get(f"/v1/videos/{video_id}/transcript")
            answer = await client.post(
                f"/v1/videos/{video_id}/questions",
                json={"question": "What does Galaxy Frog preserve?"},
            )

            assert detail.status_code == 200
            assert detail.json()["index_ready"] is True
            assert transcript.status_code == 200
            assert len(transcript.json()["cues"]) == 3
            assert transcript.json()["retrieval_units"][0]["cue_ids"]
            assert answer.status_code == 200, answer.text
            assert answer.json()["evidence"][0]["video_id"] == str(video_id)
            assert answer.json()["evidence"][0]["modality"] == "transcript"

        engine = cast(AsyncEngine, application.state.database_engine)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(VideoRow).where(VideoRow.id == video_id)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(TranscriptCueRow)
                    .where(TranscriptCueRow.video_id == video_id)
                )
                == 3
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(RetrievalUnitRow)
                    .where(RetrievalUnitRow.video_id == video_id)
                )
                == 1
            )
            await session.execute(delete(VideoRow).where(VideoRow.id == video_id))
            await session.commit()
