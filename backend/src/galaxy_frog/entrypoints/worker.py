"""Process wiring for the durable local ingestion worker."""

import asyncio
import logging
import os
import socket
from collections.abc import Callable
from contextlib import suppress
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from galaxy_frog.adapters.embeddings.ollama import OllamaBgeM3EmbeddingProvider
from galaxy_frog.adapters.media import (
    AsyncSubprocessRunner,
    FfmpegAudioNormalizer,
    IsolatedMediaWorkspace,
    LocalAudioAcquirer,
    YtDlpAudioDownloader,
)
from galaxy_frog.adapters.transcription import FasterWhisperTranscriptionProvider
from galaxy_frog.adapters.video_sources.youtube import YouTubeSource
from galaxy_frog.application.ingestion import (
    IngestionJobRunner,
    IngestionWorker,
    caption_ingestion_handlers,
)
from galaxy_frog.config import Settings
from galaxy_frog.db.engine import create_database_engine
from galaxy_frog.db.ingestion_artifacts import (
    PostgresAudioAssetRepository,
    PostgresTranscriptionCheckpointRepository,
)
from galaxy_frog.db.ingestion_repository import PostgresIngestionRepository
from galaxy_frog.db.transcript_search import PgVectorTranscriptSearch
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.domain.media import AudioAcquirer, AudioAcquisitionLimits
from galaxy_frog.domain.transcription import (
    TranscriptionComputeType,
    TranscriptionDevice,
    TranscriptionProvider,
)

EngineFactory = Callable[[Settings], AsyncEngine]
logger = logging.getLogger(__name__)


def build_audio_acquirer(settings: Settings) -> AudioAcquirer:
    """Compose bounded local media providers for explicit caption fallback."""

    runner = AsyncSubprocessRunner()
    limits = AudioAcquisitionLimits(
        max_duration_ms=settings.media_max_duration_seconds * 1000,
        max_download_bytes=settings.media_max_download_bytes,
        max_output_bytes=settings.media_max_output_bytes,
        timeout_seconds=settings.media_timeout_seconds,
        max_concurrency=settings.media_max_concurrency,
    )
    return LocalAudioAcquirer(
        downloader=YtDlpAudioDownloader(runner=runner),
        normalizer=FfmpegAudioNormalizer(
            runner=runner,
            ffmpeg_executable=settings.ffmpeg_executable,
            ffprobe_executable=settings.ffprobe_executable,
        ),
        workspaces=IsolatedMediaWorkspace(settings.media_workspace_root),
        limits=limits,
        retain_on_success=settings.media_retain_on_success,
    )


def build_transcription_provider(settings: Settings) -> TranscriptionProvider:
    """Compose the exact configured local multilingual ASR provider."""

    return FasterWhisperTranscriptionProvider(
        model=settings.asr_model,
        model_revision=settings.asr_model_revision,
        device=TranscriptionDevice(settings.asr_device),
        compute_type=TranscriptionComputeType(settings.asr_compute_type),
        max_concurrency=settings.asr_max_concurrency,
    )


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
    audio_assets = PostgresAudioAssetRepository(session)
    transcription_checkpoints = PostgresTranscriptionCheckpointRepository(session)
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
            audio_acquirer=build_audio_acquirer(settings),
            audio_assets=audio_assets,
            transcription_checkpoints=transcription_checkpoints,
            transcription_provider=build_transcription_provider(settings),
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
    worker_id = resolve_worker_id(resolved_settings)
    engine = engine_factory(resolved_settings)
    sessions = session_factory or async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    processed_jobs: int | None = None
    logger.info(
        "Ingestion worker starting",
        extra={
            "event_name": "ingestion_worker_started",
            "worker_id": worker_id,
            "dispatcher": resolved_settings.job_dispatcher,
            "lease_seconds": resolved_settings.ingestion_lease_seconds,
            "poll_seconds": resolved_settings.ingestion_poll_seconds,
            "asr_provider": resolved_settings.asr_provider,
            "asr_model": resolved_settings.asr_model,
            "asr_model_revision": resolved_settings.asr_model_revision,
            "asr_device": resolved_settings.asr_device,
            "asr_compute_type": resolved_settings.asr_compute_type,
            "asr_max_concurrency": resolved_settings.asr_max_concurrency,
            "media_max_duration_seconds": resolved_settings.media_max_duration_seconds,
            "media_max_download_bytes": resolved_settings.media_max_download_bytes,
            "media_max_output_bytes": resolved_settings.media_max_output_bytes,
            "media_timeout_seconds": resolved_settings.media_timeout_seconds,
            "media_max_concurrency": resolved_settings.media_max_concurrency,
            "media_retain_on_success": resolved_settings.media_retain_on_success,
        },
    )
    try:
        async with sessions() as session:
            worker = build_worker(
                settings=resolved_settings,
                session=session,
                worker_id=worker_id,
            )
            processed_jobs = await worker.run_until_stopped(stop_event or asyncio.Event())
            return processed_jobs
    finally:
        await engine.dispose()
        logger.info(
            "Ingestion worker stopped",
            extra={
                "event_name": "ingestion_worker_stopped",
                "worker_id": worker_id,
                "processed_jobs": processed_jobs,
            },
        )


def main() -> None:
    """Run the worker module as a standalone local process."""

    with suppress(KeyboardInterrupt):
        asyncio.run(run_worker())


if __name__ == "__main__":  # pragma: no cover - exercised through the module CLI
    main()
