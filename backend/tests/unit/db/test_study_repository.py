"""Verify request and lease transitions independently of the PostgreSQL integration tests."""

from collections import deque
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.db.models import VideoRow
from galaxy_frog.db.study_repository import (
    StudyArtifactRow,
    StudyLeaseLost,
    StudyRepository,
    StudyRunRow,
)
from galaxy_frog.domain.study import (
    RunState,
    StudyDocument,
    StudyError,
    StudyFailure,
    StudyKind,
    StudySource,
)
from galaxy_frog.domain.videos.models import VideoSourceKind


class Session:
    def __init__(self, *responses: object | None) -> None:
        self.responses = deque(responses)
        self.rows: deque[object | None] = deque()
        self.lists: deque[list[object]] = deque()
        self.added: list[object] = []
        self.commits = 0
        self.flushed = 0
        self.repository = StudyRepository(cast(AsyncSession, self))

    async def scalar(self, _query: object) -> object | None:
        return self.responses.popleft()

    async def scalars(self, _query: object) -> list[object]:
        return self.lists.popleft()

    async def get(self, _model: object, _identity: object) -> object | None:
        return self.rows.popleft()

    def add(self, row: object) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        self.commits += 1

    async def flush(self) -> None:
        self.flushed += 1


def video(duration: int = 50_000) -> VideoRow:
    return VideoRow(id=uuid4(), duration_ms=duration)


def document(owner: VideoRow) -> StudyDocument:
    return StudyDocument(
        source=StudySource(
            video_id=owner.id,
            kind=VideoSourceKind.YOUTUBE,
            external_id="abcdefghijk",
            canonical_url="https://www.youtube.com/watch?v=abcdefghijk",
            title="Fixture",
            duration_ms=owner.duration_ms,
        )
    )


def artifact(
    owner: VideoRow, number: int = 1, kind: StudyKind = StudyKind.CHAPTERS
) -> StudyArtifactRow:
    return StudyArtifactRow(
        id=uuid4(),
        video_id=owner.id,
        kind=kind,
        version=number,
        chapter_version_id=None,
        created_at=datetime.now(UTC),
        schema_version=1,
        prompt_version="study-v1",
        evidence_fingerprint="a" * 64,
        provider="ollama",
        model="fixture",
        document=document(owner).model_dump(mode="json"),
    )


@pytest.mark.asyncio
async def test_enqueue_replays_and_rejects_different_or_active_requests() -> None:
    owner = video()
    session = Session(owner, None, None, None)
    request = uuid4()
    created = await session.repository.enqueue(owner.id, StudyKind.CHAPTERS, request)
    row = session.added[0]
    assert isinstance(row, StudyRunRow)
    assert created.state == RunState.QUEUED and session.commits == 1
    replay = await Session(owner, row).repository.enqueue(owner.id, StudyKind.CHAPTERS, request)
    assert created == replay
    with pytest.raises(StudyError, match="different inputs"):
        await Session(owner, row).repository.enqueue(owner.id, StudyKind.NOTES, request)
    with pytest.raises(StudyError, match="different inputs"):
        await Session(owner, row).repository.enqueue(owner.id, StudyKind.CHAPTERS, request, uuid4())
    with pytest.raises(StudyError, match="different inputs"):
        await Session(owner, row).repository.enqueue(
            owner.id, StudyKind.CHAPTERS, request, parent_id=uuid4()
        )
    with pytest.raises(StudyError, match="already running"):
        await Session(owner, None, row.id).repository.enqueue(owner.id, StudyKind.CHAPTERS, uuid4())
    with pytest.raises(StudyError, match="Use Regenerate"):
        await Session(owner, None, None, uuid4()).repository.enqueue(
            owner.id, StudyKind.CHAPTERS, uuid4()
        )


@pytest.mark.asyncio
async def test_enqueue_checks_video_limits_and_chapter_prerequisites() -> None:
    owner = video()
    with pytest.raises(StudyError, match="does not exist"):
        await Session(None).repository.require_video(owner.id)
    with pytest.raises(StudyError, match="two hours"):
        await Session(video(7_200_001), None).repository.enqueue(
            owner.id, StudyKind.CHAPTERS, uuid4()
        )
    with pytest.raises(StudyError, match="cannot depend"):
        await Session(owner, None).repository.enqueue(
            owner.id, StudyKind.CHAPTERS, uuid4(), uuid4()
        )
    with pytest.raises(StudyError, match="Choose a saved chapter"):
        await Session(owner, None, None).repository.enqueue(
            owner.id, StudyKind.NOTES, uuid4(), uuid4()
        )

    chapter = artifact(owner)
    first = await Session(owner, None, chapter, None, None).repository.enqueue(
        owner.id, StudyKind.NOTES, uuid4(), chapter.id
    )
    assert first.chapter_version_id == chapter.id
    parent = artifact(owner, kind=StudyKind.NOTES)
    second = Session(owner, None, chapter, None)
    second.rows.append(parent)
    regenerated = await second.repository.regenerate(parent.id, uuid4(), None)
    assert regenerated.chapter_version_id == chapter.id and regenerated.kind == StudyKind.NOTES


@pytest.mark.asyncio
async def test_read_is_video_scoped_and_history_uses_an_exclusive_cursor() -> None:
    owner = video()
    first, second = artifact(owner), artifact(owner, 2)
    session = Session(owner)
    session.rows.extend([None, first, first, None])
    assert await session.repository.require_video(owner.id) is owner
    with pytest.raises(StudyError, match="saved version does not exist"):
        await session.repository.artifact(owner.id, first.id)
    with pytest.raises(StudyError, match="saved version does not exist"):
        await session.repository.artifact(uuid4(), first.id)
    assert (await session.repository.artifact(owner.id, first.id)).version == 1
    with pytest.raises(StudyError, match="generation run does not exist"):
        await session.repository.run(owner.id, uuid4())

    session.lists.extend([[second], [second, first], [first], []])
    assert (await session.repository.latest_versions(owner.id))[0].artifact_id == second.id
    page, cursor = await session.repository.history(owner.id, StudyKind.CHAPTERS, None, 1)
    assert cursor == 2 and [item.version for item in page] == [2]
    page, cursor = await session.repository.history(owner.id, StudyKind.CHAPTERS, 2, 1)
    assert cursor is None and [item.version for item in page] == [1]
    assert await session.repository.latest_runs(owner.id) == ()


@pytest.mark.asyncio
async def test_lease_claim_progress_finish_and_fencing() -> None:
    owner = video()
    enqueued = Session(owner, None, None, None)
    await enqueued.repository.enqueue(owner.id, StudyKind.CHAPTERS, uuid4())
    row = enqueued.added[0]
    assert isinstance(row, StudyRunRow)
    assert await Session(None).repository.claim() is None
    claimed = await Session(row).repository.claim()
    assert claimed is not None and row.state == RunState.RUNNING
    await Session(row).repository.progress(claimed, 1, 1, ("warning",))
    assert row.completed_chapters == 1 and row.warnings == ["warning"]
    published_session = Session(row, 1)
    saved = await published_session.repository.publish(
        claimed, document(owner), "f" * 64, "ollama", "local"
    )
    assert saved.version == 2 and published_session.flushed == published_session.commits == 1
    assert row.result_id == saved.artifact_id and row.state == RunState.SUCCEEDED
    with pytest.raises(StudyLeaseLost):
        await Session(None).repository.progress(claimed, 1, 1, ())

    failure = StudyFailure(
        code="TEST", message="Unavailable", retryable=True, suggested_action="Retry"
    )
    await Session(row).repository.fail(claimed, failure)
    failed = Session()
    failed.rows.extend([row, row])
    assert (await failed.repository.run(owner.id, row.id)).failure == failure
    with pytest.raises(StudyError, match="generation run does not exist"):
        await failed.repository.run(uuid4(), row.id)

    row.attempts = 2
    row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert await Session(row).repository.claim() is None
    assert row.failure is not None and row.failure["code"] == "STUDY_WORKER_INTERRUPTED"


@pytest.mark.asyncio
async def test_first_publish_assigns_version_one() -> None:
    owner = video()
    enqueued = Session(owner, None, None, None)
    await enqueued.repository.enqueue(owner.id, StudyKind.CHAPTERS, uuid4())
    row = enqueued.added[0]
    claimed = await Session(row).repository.claim()
    assert claimed is not None
    saved = await Session(row, None).repository.publish(
        claimed, document(owner), "f" * 64, "ollama", "local"
    )
    assert saved.version == 1
