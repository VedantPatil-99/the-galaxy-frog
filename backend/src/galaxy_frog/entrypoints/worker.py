"""Process wiring for the durable local ingestion worker."""

import asyncio
import os
import socket
from collections.abc import Callable
from contextlib import suppress
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from galaxy_frog.adapters.embeddings.ollama import OllamaBgeM3EmbeddingProvider
from galaxy_frog.adapters.video_sources.youtube import YouTubeSource
from galaxy_frog.application.ingestion import (
    IngestionJobRunner,
    IngestionWorker,
    caption_ingestion_handlers,
)
from galaxy_frog.config import Settings
from galaxy_frog.db.engine import create_database_engine
from galaxy_frog.db.ingestion_repository import PostgresIngestionRepository
from galaxy_frog.db.transcript_search import PgVectorTranscriptSearch
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository

EngineFactory = Callable[[Settings], AsyncEngine]


def resolve_worker_id(settings: Settings) -> str:
    """Return a stable explicit id or a unique local process identity."""

    return settings.ingestion_worker_id or f"{socket.gethostname()}-{os.getpid()}"


def build_worker(
    *,
    settings: Settings,
    session: AsyncSession,
    worker_id: str,
) -> IngestionWorker:
    """Compose one worker session without exposing providers to domain code."""

    ingestion = PostgresIngestionRepository(session)
    videos = SqlAlchemyVideoRepository(session)
    transcript_search = PgVectorTranscriptSearch(
        session=session,
        videos=videos,
        provider=OllamaBgeM3EmbeddingProvider(
            base_url=settings.ollama_base_url,
            model=settings.embedding_model,
            revision=settings.embedding_model_revision,
        ),
    )
    lease_duration = timedelta(seconds=settings.ingestion_lease_seconds)
    runner = IngestionJobRunner(
        repository=ingestion,
        handlers=caption_ingestion_handlers(
            sources=(YouTubeSource(),),
            videos=videos,
            transcript_search=transcript_search,
        ),
        worker_id=worker_id,
        lease_duration=lease_duration,
    )
    return IngestionWorker(
        repository=ingestion,
        runner=runner,
        worker_id=worker_id,
        lease_duration=lease_duration,
        poll_interval=timedelta(seconds=settings.ingestion_poll_seconds),
    )


async def run_worker(
    settings: Settings | None = None,
    stop_event: asyncio.Event | None = None,
    *,
    engine_factory: EngineFactory = create_database_engine,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Run the worker until cancellation while always releasing database resources."""

    resolved_settings = settings or Settings()
    engine = engine_factory(resolved_settings)
    sessions = session_factory or async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    try:
        async with sessions() as session:
            worker = build_worker(
                settings=resolved_settings,
                session=session,
                worker_id=resolve_worker_id(resolved_settings),
            )
            return await worker.run_until_stopped(stop_event or asyncio.Event())
    finally:
        await engine.dispose()


def main() -> None:
    """Run the worker module as a standalone local process."""

    with suppress(KeyboardInterrupt):
        asyncio.run(run_worker())


if __name__ == "__main__":  # pragma: no cover - exercised through the module CLI
    main()
