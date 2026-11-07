"""Verify worker failure isolation and run publication with a pinned chapter version."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from galaxy_frog.application.study_generation import GeneratedStudy
from galaxy_frog.config import Settings
from galaxy_frog.db.study_evidence import StudyEvidenceReader
from galaxy_frog.db.study_repository import ClaimedStudyRun, StudyLeaseLost, StudyRepository
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
    ) -> GeneratedStudy:
        await report(1, 1, ())
        return result

    monkeypatch.setattr(study_worker, "generate_study", generate)
    spec = EmbeddingCollectionSpec("ollama", "bge-m3", "ollama", 1024, "l2")
    provider = AsyncMock()
    await study_worker.execute_study_run(claimed, sessions, spec, provider)
    artifact.assert_awaited_once_with(claimed.run.video_id, claimed.run.chapter_version_id)
    progress.assert_awaited_once_with(claimed, 1, 1, ())
    publish.assert_awaited_once_with(
        claimed, result.document, result.fingerprint, "ollama", "fixture"
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
