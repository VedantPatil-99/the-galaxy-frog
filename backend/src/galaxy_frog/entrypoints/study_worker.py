"""Run the local study lane alongside ingestion without sharing sessions."""

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from time import perf_counter

import httpx
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from galaxy_frog.adapters.generation.study_ollama import OllamaStudyGenerator
from galaxy_frog.application.provider_policy import ProviderAttempt, ProviderCode
from galaxy_frog.application.provider_registry import probe_ollama_models, provider_catalog
from galaxy_frog.application.study_generation import StudyGenerator, generate_study
from galaxy_frog.config import Settings
from galaxy_frog.db.provider_runtime import PostgresProviderRuntime
from galaxy_frog.db.study_evidence import StudyEvidenceReader
from galaxy_frog.db.study_repository import ClaimedStudyRun, StudyLeaseLost, StudyRepository
from galaxy_frog.domain.providers import Capability, ProfileRef, ProviderMode, ProviderOperation
from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec
from galaxy_frog.domain.study import StudyError, StudyFailure, StudyKind

logger = logging.getLogger(__name__)


async def execute_study_run(
    claim: ClaimedStudyRun,
    sessions: async_sessionmaker[AsyncSession],
    spec: EmbeddingCollectionSpec,
    provider: StudyGenerator,
    *,
    evidence_characters: int = 1400,
    excerpt_characters: int = 320,
) -> None:
    async def progress(done: int, total: int, warnings: tuple[str, ...]) -> None:
        async with sessions() as session:
            await StudyRepository(session).progress(claim, done, total, warnings)

    async with asyncio.timeout(25 * 60):
        async with sessions() as session:
            await session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            repository = StudyRepository(session)
            pinned = (
                await repository.artifact(claim.run.video_id, claim.run.chapter_version_id)
                if claim.run.chapter_version_id is not None
                else None
            )
            timeline = await StudyEvidenceReader(session, spec).read(
                claim.run.video_id, chaptering=claim.run.kind == StudyKind.CHAPTERS
            )
        result = await generate_study(
            claim.run.kind,
            timeline,
            pinned,
            provider,
            progress,
            evidence_characters=evidence_characters,
            excerpt_characters=excerpt_characters,
        )
        if len(json.dumps(result.document.model_dump(mode="json")).encode()) > 524_288:
            raise StudyError(
                "STUDY_DOCUMENT_TOO_LARGE", "The generated document exceeds the saved size limit."
            )
        async with sessions() as session:
            await StudyRepository(session).publish(
                claim,
                result.document,
                result.fingerprint,
                result.provider,
                result.model,
                prompt_version=result.prompt_version,
            )


async def process_study(
    sessions: async_sessionmaker[AsyncSession],
    execute: Callable[[ClaimedStudyRun], Awaitable[None]],
) -> bool:
    async with sessions() as session:
        claim = await StudyRepository(session).claim()
    if claim is None:
        return False
    try:
        await execute(claim)
    except StudyLeaseLost:
        logger.info("Study generation lost its lease run_id=%s", claim.run.run_id)
    except StudyError as exc:
        await _record_failure(sessions, claim, exc.failure)
    except TimeoutError:
        await _record_failure(
            sessions,
            claim,
            StudyFailure(
                code="STUDY_RUN_TIMEOUT",
                message="Generation reached its time limit.",
                retryable=True,
                suggested_action="Check local model responsiveness and retry.",
            ),
        )
    except SQLAlchemyError:
        raise
    except Exception as exc:
        logger.error(
            "Study generation failed run_id=%s error_type=%s", claim.run.run_id, type(exc).__name__
        )
        await _record_failure(
            sessions,
            claim,
            StudyFailure(
                code="STUDY_GENERATION_FAILED",
                message="The worker could not complete generation.",
                retryable=True,
                suggested_action="Check the local worker log and retry.",
            ),
        )
    return True


async def _record_failure(
    sessions: async_sessionmaker[AsyncSession], claim: ClaimedStudyRun, failure: StudyFailure
) -> None:
    try:
        async with sessions() as session:
            await StudyRepository(session).fail(claim, failure)
    except StudyLeaseLost:
        logger.info("Study failure discarded after lease loss run_id=%s", claim.run.run_id)


async def run_study_worker(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    stop: asyncio.Event,
    *,
    engine: AsyncEngine | None = None,
) -> None:
    spec = EmbeddingCollectionSpec(
        provider="ollama",
        model=settings.embedding_model,
        revision=settings.embedding_model_revision,
        dimension=1024,
        normalization="l2",
    )
    async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
        provider = OllamaStudyGenerator(settings.ollama_base_url, settings.generation_model, client)

        async def execute(claim: ClaimedStudyRun) -> None:
            if claim.run.provider_selection is not None:
                if engine is None:
                    raise StudyError(
                        "DATABASE_UNAVAILABLE",
                        "Provider-selected Study generation requires PostgreSQL.",
                        status=503,
                    )
                from galaxy_frog.entrypoints.study_providers import selected_study_generator

                selected = await selected_study_generator(
                    settings, sessions, engine, claim, provider
                )
                await execute_study_run(
                    claim,
                    sessions,
                    spec,
                    selected,
                    evidence_characters=selected.evidence_characters,
                    excerpt_characters=selected.excerpt_characters,
                )
                return
            if engine is None:
                await execute_study_run(claim, sessions, spec, provider)
                return
            entry = next(
                item for item in provider_catalog(settings) if item.capability is Capability.STUDY
            )
            runtime = PostgresProviderRuntime(
                sessions,
                engine=engine,
                profile=ProfileRef(profile_id="local", version=1),
                operation=ProviderOperation.STUDY,
                run_id=claim.run.run_id,
                source=str(claim.run.video_id),
            )
            started = perf_counter()
            status = "failed"
            reason: ProviderCode | None = ProviderCode.UNAVAILABLE
            revision = entry.revision
            try:
                models = await probe_ollama_models(settings.ollama_base_url)
                observed = models.get(entry.model) or models.get(f"{entry.model}:latest")
                if not observed or (revision and revision != observed):
                    status = "skipped"
                    reason = ProviderCode.SETUP_REQUIRED
                    raise StudyError(
                        "STUDY_PROVIDER_UNAVAILABLE",
                        "The configured Ollama model is unavailable locally.",
                        retryable=True,
                        action="Check the installed model and configured revision, then retry.",
                    )
                revision = observed
                await execute_study_run(claim, sessions, spec, provider)
                status = "succeeded"
                reason = None
            finally:
                await runtime.record(
                    Capability.STUDY,
                    ProviderAttempt(
                        provider_id=entry.provider_id,
                        provider=entry.provider,
                        model=entry.model,
                        revision=revision,
                        device=entry.device,
                        status=status,
                        reason=reason,
                        processing_ms=round((perf_counter() - started) * 1000)
                        if status != "skipped"
                        else None,
                        requested_mode=ProviderMode.LOCAL_ONLY,
                        requested_candidates=(entry.provider_id,),
                    ),
                )

        while not stop.is_set():
            try:
                if await process_study(sessions, execute):
                    continue
            except SQLAlchemyError as exc:
                logger.warning(
                    "Study polling needs PostgreSQL and migration 0013 (%s).", type(exc).__name__
                )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.ingestion_poll_seconds)
