"""Verify worker failure isolation and run publication with a pinned chapter version."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from galaxy_frog.application.provider_policy import ProviderCode
from galaxy_frog.application.study_generation import GeneratedStudy
from galaxy_frog.config import Settings
from galaxy_frog.db.study_evidence import StudyEvidenceReader
from galaxy_frog.db.study_repository import ClaimedStudyRun, StudyLeaseLost, StudyRepository
from galaxy_frog.domain.providers import ProfileRef, ProviderSelection
from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec
from galaxy_frog.domain.study import (
    RunState,
    StudyDocument,
    StudyError,
    StudyKind,
    StudyRun,
    StudySource,
)
from galaxy_frog.domain.videos.models import VideoSourceKind
from galaxy_frog.entrypoints import study_worker


def claim(chapters: UUID | None = None) -> ClaimedStudyRun:
    now = datetime.now(UTC)
    return ClaimedStudyRun(
        StudyRun(
            run_id=uuid4(),
            video_id=uuid4(),
            kind=StudyKind.NOTES if chapters else StudyKind.CHAPTERS,
            state=RunState.RUNNING,
            chapter_version_id=chapters,
            result_id=None,
            completed_chapters=0,
            total_chapters=0,
            warnings=(),
            failure=None,
            created_at=now,
            updated_at=now,
        ),
        uuid4(),
    )


@pytest.mark.asyncio
async def test_worker_publishes_only_after_generation_and_size_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = async_sessionmaker(class_=AsyncSession)
    execute = AsyncMock()
    monkeypatch.setattr(AsyncSession, "execute", execute)
    artifact = AsyncMock(return_value=None)
    publish = AsyncMock()
    progress = AsyncMock()
    read = AsyncMock()
    monkeypatch.setattr(StudyRepository, "artifact", artifact)
    monkeypatch.setattr(StudyRepository, "publish", publish)
    monkeypatch.setattr(StudyRepository, "progress", progress)
    monkeypatch.setattr(StudyEvidenceReader, "read", read)
    claimed = claim(uuid4())
    source = StudySource(
        video_id=claimed.run.video_id,
        kind=VideoSourceKind.YOUTUBE,
        external_id="abcdefghijk",
        canonical_url="https://www.youtube.com/watch?v=abcdefghijk",
        title="Fixture",
        duration_ms=30_000,
    )
    result = GeneratedStudy(StudyDocument(source=source), "f" * 64, "ollama", "fixture")
    from galaxy_frog.application.study_generation import Progress, StudyGenerator, StudyTimeline
    from galaxy_frog.domain.study import StudyVersion

    async def generate(
        _kind: StudyKind,
        _timeline: StudyTimeline,
        _pinned: StudyVersion | None,
        _provider: StudyGenerator,
        report: Progress,
        *,
        evidence_characters: int = 1400,
        excerpt_characters: int = 320,
    ) -> GeneratedStudy:
        assert (evidence_characters, excerpt_characters) == (1400, 320)
        await report(1, 1, ())
        return result

    monkeypatch.setattr(study_worker, "generate_study", generate)
    spec = EmbeddingCollectionSpec("ollama", "bge-m3", "ollama", 1024, "l2")
    provider = AsyncMock()
    await study_worker.execute_study_run(claimed, sessions, spec, provider)
    artifact.assert_awaited_once_with(claimed.run.video_id, claimed.run.chapter_version_id)
    progress.assert_awaited_once_with(claimed, 1, 1, ())
    publish.assert_awaited_once_with(
        claimed,
        result.document,
        result.fingerprint,
        "ollama",
        "fixture",
        prompt_version=result.prompt_version,
    )

    source.title = "x" * 525_000
    with pytest.raises(StudyError, match="size limit"):
        await study_worker.execute_study_run(claim(), sessions, spec, provider)
    assert publish.await_count == 1


@pytest.mark.asyncio
async def test_worker_retries_transient_polling_and_classifies_run_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = async_sessionmaker(class_=AsyncSession)
    claimed = claim()
    mock_claim = AsyncMock(side_effect=[None, claimed, claimed, claimed, claimed, claimed, claimed])
    fail = AsyncMock(side_effect=[None, None, None, StudyLeaseLost()])
    monkeypatch.setattr(StudyRepository, "claim", mock_claim)
    monkeypatch.setattr(StudyRepository, "fail", fail)
    execute = AsyncMock(
        side_effect=[
            StudyLeaseLost(),
            StudyError("FIXTURE", "fixture"),
            TimeoutError(),
            RuntimeError(),
            SQLAlchemyError(),
            StudyError("FIXTURE", "fixture"),
        ]
    )
    assert not await study_worker.process_study(sessions, execute)
    for _ in range(4):
        assert await study_worker.process_study(sessions, execute)
    assert [call.args[1].code for call in fail.await_args_list] == [
        "FIXTURE",
        "STUDY_RUN_TIMEOUT",
        "STUDY_GENERATION_FAILED",
    ]
    with pytest.raises(SQLAlchemyError):
        await study_worker.process_study(sessions, execute)
    assert await study_worker.process_study(sessions, execute)

    stop = asyncio.Event()
    steps = 0
    from collections.abc import Awaitable, Callable

    async def poll(
        _sessions: async_sessionmaker[AsyncSession],
        run: Callable[[ClaimedStudyRun], Awaitable[None]],
    ) -> bool:
        nonlocal steps
        steps += 1
        if steps == 1:
            raise SQLAlchemyError
        if steps == 2:
            await run(claimed)
            return True
        stop.set()
        return False

    fake_execute = AsyncMock()
    monkeypatch.setattr(study_worker, "process_study", poll)
    monkeypatch.setattr(study_worker, "execute_study_run", fake_execute)
    await study_worker.run_study_worker(Settings(ingestion_poll_seconds=0.001), sessions, stop)
    assert steps == 3 and fake_execute.await_count == 1


@pytest.mark.parametrize("digest", [None, "different", "pinned"])
@pytest.mark.asyncio
async def test_study_worker_checks_and_records_the_local_model(
    monkeypatch: pytest.MonkeyPatch, digest: str | None
) -> None:
    settings = Settings(generation_model_revision="pinned", ingestion_poll_seconds=0.001)
    engine = create_async_engine("postgresql+asyncpg://localhost/db")
    sessions = async_sessionmaker(engine)
    stop = asyncio.Event()
    claimed = claim()
    execute = AsyncMock()
    record = AsyncMock()
    fail = AsyncMock()

    async def claim_once() -> ClaimedStudyRun:
        stop.set()
        return claimed

    monkeypatch.setattr(StudyRepository, "claim", AsyncMock(side_effect=claim_once))
    monkeypatch.setattr(StudyRepository, "fail", fail)
    monkeypatch.setattr(study_worker, "execute_study_run", execute)
    monkeypatch.setattr(study_worker.PostgresProviderRuntime, "record", record)
    monkeypatch.setattr(
        study_worker,
        "probe_ollama_models",
        AsyncMock(return_value={settings.generation_model: digest} if digest else {}),
    )
    try:
        await study_worker.run_study_worker(settings, sessions, stop, engine=engine)
    finally:
        await engine.dispose()
    record.assert_awaited_once()
    call = record.await_args
    assert call is not None
    attempt = call.args[1]
    if digest == "pinned":
        execute.assert_awaited_once()
        fail.assert_not_awaited()
        assert attempt.status == "succeeded" and attempt.revision == digest
    else:
        execute.assert_not_awaited()
        fail.assert_awaited_once()
        assert attempt.status == "skipped" and attempt.reason == ProviderCode.SETUP_REQUIRED


@pytest.mark.parametrize("database", [False, True])
@pytest.mark.asyncio
async def test_selected_study_worker_routes_with_bounded_evidence(
    monkeypatch: pytest.MonkeyPatch, database: bool
) -> None:
    from galaxy_frog.entrypoints import study_providers

    engine = create_async_engine("postgresql+asyncpg://localhost/db")
    sessions = async_sessionmaker(engine)
    stop = asyncio.Event()
    claimed = claim()
    claimed.run.provider_selection = ProviderSelection(
        profile=ProfileRef(profile_id="gemini", version=1)
    )

    async def claim_once() -> ClaimedStudyRun:
        stop.set()
        return claimed

    selected = AsyncMock()
    selected.evidence_characters = 12000
    selected.excerpt_characters = 1200
    factory = AsyncMock(return_value=selected)
    execute, fail = AsyncMock(), AsyncMock()
    monkeypatch.setattr(study_providers, "selected_study_generator", factory)
    monkeypatch.setattr(StudyRepository, "claim", AsyncMock(side_effect=claim_once))
    monkeypatch.setattr(StudyRepository, "fail", fail)
    monkeypatch.setattr(study_worker, "execute_study_run", execute)
    try:
        await study_worker.run_study_worker(
            Settings(), sessions, stop, engine=engine if database else None
        )
    finally:
        await engine.dispose()
    if database:
        factory.assert_awaited_once()
        assert execute.await_args is not None
        assert execute.await_args.args[3] is selected
        assert execute.await_args.kwargs == {
            "evidence_characters": 12000,
            "excerpt_characters": 1200,
        }
        fail.assert_not_awaited()
    else:
        factory.assert_not_awaited()
        execute.assert_not_awaited()
        assert fail.await_args is not None
        assert fail.await_args.args[1].code == "DATABASE_UNAVAILABLE"
