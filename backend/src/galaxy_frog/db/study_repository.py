"""Short, lease-fenced transactions for immutable study material."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    String,
    and_,
    func,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from galaxy_frog.db.models import Base, VideoRow
from galaxy_frog.domain.study import (
    MAX_VIDEO_MS,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    RunState,
    StudyDocument,
    StudyError,
    StudyFailure,
    StudyKind,
    StudyRun,
    StudyVersion,
    StudyVersionSummary,
)

_ACTIVE = (RunState.QUEUED, RunState.RUNNING)
_LEASE = timedelta(seconds=120)


class StudyArtifactRow(Base):
    __tablename__ = "generated_artifacts"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    video_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer)
    chapter_version_id: Mapped[UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    schema_version: Mapped[int]
    prompt_version: Mapped[str] = mapped_column(String(64))
    evidence_fingerprint: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(255))
    document: Mapped[dict[str, JsonValue]] = mapped_column(JSON)


class StudyRunRow(Base):
    __tablename__ = "artifact_generation_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    video_id: Mapped[UUID]
    request_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(16))
    chapter_version_id: Mapped[UUID | None]
    parent_id: Mapped[UUID | None]
    result_id: Mapped[UUID | None]
    state: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int]
    completed_chapters: Mapped[int]
    total_chapters: Mapped[int]
    warnings: Mapped[list[str]] = mapped_column(JSON)
    failure: Mapped[dict[str, JsonValue] | None] = mapped_column(JSON)


@dataclass(frozen=True, slots=True)
class ClaimedStudyRun:
    run: StudyRun
    token: UUID


class StudyLeaseLost(Exception):
    """An expired or replaced worker cannot mutate a run."""


def _run(row: StudyRunRow) -> StudyRun:
    return StudyRun(
        run_id=row.id,
        video_id=row.video_id,
        kind=StudyKind(row.kind),
        state=RunState(row.state),
        chapter_version_id=row.chapter_version_id,
        result_id=row.result_id,
        completed_chapters=row.completed_chapters,
        total_chapters=row.total_chapters,
        warnings=tuple(row.warnings),
        failure=StudyFailure.model_validate(row.failure) if row.failure is not None else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _summary(row: StudyArtifactRow) -> StudyVersionSummary:
    return StudyVersionSummary(
        artifact_id=row.id,
        kind=StudyKind(row.kind),
        version=row.version,
        chapter_version_id=row.chapter_version_id,
        created_at=row.created_at,
    )


def _version(row: StudyArtifactRow) -> StudyVersion:
    return StudyVersion(
        **_summary(row).model_dump(),
        schema_version=row.schema_version,
        prompt_version=row.prompt_version,
        evidence_fingerprint=row.evidence_fingerprint,
        provider=row.provider,
        model=row.model,
        document=StudyDocument.model_validate(row.document),
    )


class StudyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def require_video(self, video_id: UUID, *, lock: bool = False) -> VideoRow:
        query = select(VideoRow).where(VideoRow.id == video_id)
        row = await self.session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise StudyError("VIDEO_NOT_FOUND", "The requested video does not exist.", status=404)
        return row

    async def _artifact(self, artifact_id: UUID) -> StudyArtifactRow:
        row = await self.session.get(StudyArtifactRow, artifact_id)
        if row is None:
            raise StudyError(
                "STUDY_ARTIFACT_NOT_FOUND", "This saved version does not exist.", status=404
            )
        return row

    async def artifact(self, video_id: UUID, artifact_id: UUID) -> StudyVersion:
        row = await self._artifact(artifact_id)
        if row.video_id != video_id:
            raise StudyError(
                "STUDY_ARTIFACT_NOT_FOUND", "This saved version does not exist.", status=404
            )
        return _version(row)

    async def run(self, video_id: UUID, run_id: UUID) -> StudyRun:
        row = await self.session.get(StudyRunRow, run_id)
        if row is None or row.video_id != video_id:
            raise StudyError(
                "STUDY_RUN_NOT_FOUND", "This generation run does not exist.", status=404
            )
        return _run(row)

    async def latest_versions(self, video_id: UUID) -> tuple[StudyVersionSummary, ...]:
        rows = await self.session.scalars(
            select(StudyArtifactRow)
            .where(StudyArtifactRow.video_id == video_id)
            .distinct(StudyArtifactRow.kind)
            .order_by(StudyArtifactRow.kind, StudyArtifactRow.version.desc())
        )
        return tuple(map(_summary, rows))

    async def history(
        self, video_id: UUID, kind: StudyKind, before_version: int | None, limit: int
    ) -> tuple[tuple[StudyVersionSummary, ...], int | None]:
        query = select(StudyArtifactRow).where(
            StudyArtifactRow.video_id == video_id, StudyArtifactRow.kind == kind
        )
        if before_version is not None:
            query = query.where(StudyArtifactRow.version < before_version)
        rows = tuple(
            await self.session.scalars(
                query.order_by(StudyArtifactRow.version.desc()).limit(limit + 1)
            )
        )
        return tuple(map(_summary, rows[:limit])), rows[limit - 1].version if len(
            rows
        ) > limit else None

    async def latest_runs(self, video_id: UUID) -> tuple[StudyRun, ...]:
        rows = await self.session.scalars(
            select(StudyRunRow)
            .where(StudyRunRow.video_id == video_id)
            .distinct(StudyRunRow.kind)
            .order_by(StudyRunRow.kind, StudyRunRow.created_at.desc(), StudyRunRow.id.desc())
        )
        return tuple(map(_run, rows))

    async def regenerate(
        self, artifact_id: UUID, request_id: UUID, chapter_version_id: UUID | None
    ) -> StudyRun:
        parent = await self._artifact(artifact_id)
        return await self.enqueue(
            parent.video_id,
            StudyKind(parent.kind),
            request_id,
            chapter_version_id,
            parent_id=artifact_id,
        )

    async def enqueue(
        self,
        video_id: UUID,
        kind: StudyKind,
        request_id: UUID,
        chapter_version_id: UUID | None = None,
        *,
        parent_id: UUID | None = None,
    ) -> StudyRun:
        video = await self.require_video(video_id, lock=True)
        prior = await self.session.scalar(
            select(StudyRunRow).where(
                StudyRunRow.video_id == video_id, StudyRunRow.request_id == request_id
            )
        )
        if prior is not None:
            if (
                prior.kind != kind
                or prior.parent_id != parent_id
                or (
                    chapter_version_id is not None
                    and prior.chapter_version_id != chapter_version_id
                )
            ):
                raise StudyError(
                    "STUDY_REQUEST_CONFLICT", "This request ID was used for different inputs."
                )
            return _run(prior)
        if not 0 < video.duration_ms <= MAX_VIDEO_MS:
            raise StudyError(
                "STUDY_VIDEO_TOO_LONG",
                "Study generation supports videos up to two hours.",
                status=422,
            )
        if kind == StudyKind.CHAPTERS:
            if chapter_version_id is not None:
                raise StudyError(
                    "STUDY_CHAPTER_VERSION_INVALID", "Chapters cannot depend on a chapter version."
                )
        else:
            chapters = await self.session.scalar(
                select(StudyArtifactRow)
                .where(
                    StudyArtifactRow.video_id == video_id,
                    StudyArtifactRow.kind == StudyKind.CHAPTERS,
                    *(
                        ()
                        if chapter_version_id is None
                        else (StudyArtifactRow.id == chapter_version_id,)
                    ),
                )
                .order_by(StudyArtifactRow.version.desc())
                .limit(1)
            )
            if chapters is None:
                raise StudyError(
                    "STUDY_CHAPTERS_REQUIRED",
                    "Choose a saved chapter version from this video.",
                    action="Generate chapters for this video first.",
                )
            chapter_version_id = chapters.id

        active = await self.session.scalar(
            select(StudyRunRow.id).where(
                StudyRunRow.video_id == video_id,
                StudyRunRow.kind == kind,
                StudyRunRow.state.in_(_ACTIVE),
            )
        )
        if active is not None:
            raise StudyError(
                "STUDY_RUN_ACTIVE",
                "Generation for this material is already running.",
                action="Refresh the Study area to see its progress.",
            )
        if parent_id is None:
            saved = await self.session.scalar(
                select(StudyArtifactRow.id)
                .where(StudyArtifactRow.video_id == video_id, StudyArtifactRow.kind == kind)
                .limit(1)
            )
            if saved is not None:
                raise StudyError("STUDY_ALREADY_SAVED", "Use Regenerate to create another version.")

        now = datetime.now(UTC)
        row = StudyRunRow(
            id=uuid4(),
            video_id=video_id,
            request_id=request_id,
            kind=kind,
            chapter_version_id=chapter_version_id,
            parent_id=parent_id,
            result_id=None,
            state=RunState.QUEUED,
            created_at=now,
            updated_at=now,
            lease_token=None,
            lease_expires_at=None,
            attempts=0,
            completed_chapters=0,
            total_chapters=0,
            warnings=[],
            failure=None,
        )
        self.session.add(row)
        result = _run(row)
        await self.session.commit()
        return result

    async def claim(self) -> ClaimedStudyRun | None:
        now = datetime.now(UTC)
        row = await self.session.scalar(
            select(StudyRunRow)
            .where(
                or_(
                    StudyRunRow.state == RunState.QUEUED,
                    and_(
                        StudyRunRow.state == RunState.RUNNING, StudyRunRow.lease_expires_at <= now
                    ),
                )
            )
            .order_by(StudyRunRow.created_at, StudyRunRow.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        if row.attempts >= 2:
            self._finish(row, now, RunState.FAILED)
            row.failure = StudyFailure(
                code="STUDY_WORKER_INTERRUPTED",
                message="The study worker stopped twice before saving this material.",
                retryable=True,
                suggested_action="Start the local worker, then retry generation.",
            ).model_dump(mode="json")
            await self.session.commit()
            return None
        token = uuid4()
        row.state = RunState.RUNNING
        row.attempts += 1
        row.completed_chapters = row.total_chapters = 0
        row.warnings = []
        row.lease_token = token
        row.lease_expires_at = now + _LEASE
        row.updated_at = now
        claimed = ClaimedStudyRun(_run(row), token)
        await self.session.commit()
        return claimed

    async def _owned(self, claim: ClaimedStudyRun, now: datetime) -> StudyRunRow:
        row = await self.session.scalar(
            select(StudyRunRow)
            .where(
                StudyRunRow.id == claim.run.run_id,
                StudyRunRow.state == RunState.RUNNING,
                StudyRunRow.lease_token == claim.token,
                StudyRunRow.lease_expires_at > now,
            )
            .with_for_update()
        )
        if row is None:
            raise StudyLeaseLost
        return row

    async def progress(
        self, claim: ClaimedStudyRun, done: int, total: int, warnings: tuple[str, ...]
    ) -> None:
        now = datetime.now(UTC)
        row = await self._owned(claim, now)
        row.completed_chapters = done
        row.total_chapters = total
        row.warnings = list(warnings)
        row.lease_expires_at = now + _LEASE
        row.updated_at = now
        await self.session.commit()

    @staticmethod
    def _finish(row: StudyRunRow, now: datetime, state: RunState) -> None:
        row.state = state
        row.lease_token = None
        row.lease_expires_at = None
        row.updated_at = now

    async def fail(self, claim: ClaimedStudyRun, failure: StudyFailure) -> None:
        now = datetime.now(UTC)
        row = await self._owned(claim, now)
        self._finish(row, now, RunState.FAILED)
        row.failure = failure.model_dump(mode="json")
        await self.session.commit()

    async def publish(
        self,
        claim: ClaimedStudyRun,
        document: StudyDocument,
        fingerprint: str,
        provider: str,
        model: str,
    ) -> StudyVersion:
        now = datetime.now(UTC)
        row = await self._owned(claim, now)
        version = await self.session.scalar(
            select(func.max(StudyArtifactRow.version)).where(
                StudyArtifactRow.video_id == row.video_id, StudyArtifactRow.kind == row.kind
            )
        )
        artifact = StudyArtifactRow(
            id=uuid4(),
            video_id=row.video_id,
            kind=row.kind,
            version=(version or 0) + 1,
            chapter_version_id=row.chapter_version_id,
            created_at=now,
            schema_version=SCHEMA_VERSION,
            prompt_version=PROMPT_VERSION,
            evidence_fingerprint=fingerprint,
            provider=provider,
            model=model,
            document=document.model_dump(mode="json"),
        )
        self.session.add(artifact)
        await self.session.flush()
        self._finish(row, now, RunState.SUCCEEDED)
        row.result_id = artifact.id
        row.warnings = list(document.warnings)
        result = _version(artifact)
        await self.session.commit()
        return result
