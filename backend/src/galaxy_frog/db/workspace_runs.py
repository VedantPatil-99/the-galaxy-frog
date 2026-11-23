"""Owner-scoped submission and readback of one active run per chat."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from galaxy_frog.db.models import Base
from galaxy_frog.db.workspace import WorkspaceNotebookRow
from galaxy_frog.db.workspace_chats import WorkspaceChatRow
from galaxy_frog.db.workspace_text import WorkspaceTextRepository
from galaxy_frog.domain.generation.workspace_answer import (
    WORKSPACE_ANSWER_MAX_PASSAGE_CHARS,
    WorkspaceAnswerDraft,
)
from galaxy_frog.domain.retrieval.pipeline import QueryKind
from galaxy_frog.domain.retrieval.query import analyze_query
from galaxy_frog.domain.workspace import VerifiedWorkspaceIdentity
from galaxy_frog.domain.workspace_chat import WorkspaceChatNotFound, WorkspaceDraftConflict
from galaxy_frog.domain.workspace_run import (
    ACTIVE_RUN_STATUSES,
    RETRYABLE_RUN_STATUSES,
    RunFailure,
    RunStatus,
    TerminalRunStatus,
    WorkspaceLocalRunSettings,
    WorkspacePassagePin,
    WorkspaceRunEvidence,
    WorkspaceRunExecutionView,
    WorkspaceRunView,
    WorkspaceTurnsPage,
    WorkspaceTurnSubmit,
    WorkspaceTurnView,
)
from galaxy_frog.domain.workspace_text import WorkspaceTextNotFound, WorkspaceTextSource

AUTHORIZATION_LIFETIME = timedelta(minutes=10)


class WorkspaceSubmissionConflict(Exception):
    """An accepted key cannot be used for different intent."""


class WorkspaceActiveRun(Exception):
    """The chat already has an active run."""


class WorkspaceTemporalQuestion(Exception):
    """This first slice cannot resolve an event or time reference."""


class WorkspaceRunAuthorizationExpired(Exception):
    """The verified session cannot grant a bounded local run."""


class WorkspaceRunNotFound(Exception):
    """Unavailable runs have the same response across workspace scopes."""


class WorkspaceRunNotRetryable(Exception):
    """An active or completed run cannot become a retry."""


class WorkspaceChatTurnRow(Base):
    __tablename__ = "workspace_chat_turns"
    __table_args__ = (
        UniqueConstraint("chat_id", "submission_key", name="uq_workspace_turn_submission"),
        UniqueConstraint("chat_id", "sequence", name="uq_workspace_turn_sequence"),
        UniqueConstraint("chat_id", "id", name="uq_workspace_turn_chat_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    chat_id: Mapped[UUID] = mapped_column(ForeignKey("workspace_chats.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(BigInteger)
    submission_key: Mapped[UUID]
    fingerprint: Mapped[str] = mapped_column(String(64))
    question: Mapped[str] = mapped_column(String(1000))
    pin: Mapped[dict[str, object]] = mapped_column(JSONB)
    passage_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkspaceChatRunRow(Base):
    __tablename__ = "workspace_chat_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["chat_id", "turn_id"],
            ["workspace_chat_turns.chat_id", "workspace_chat_turns.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("chat_id", "id", name="uq_workspace_run_chat_id"),
        ForeignKeyConstraint(
            ["chat_id", "retry_of_run_id"],
            ["workspace_chat_runs.chat_id", "workspace_chat_runs.id"],
            name="fk_workspace_run_retry_in_chat",
        ),
        CheckConstraint(
            "status IN ('queued', 'preparing', 'running', 'completed', 'stopped', 'failed', 'uncertain')",
            name="ck_workspace_run_status",
        ),
        Index(
            "ux_workspace_chat_active_run",
            "chat_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'preparing', 'running')"),
        ),
        Index("ix_workspace_runs_queue", "status", "queued_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    turn_id: Mapped[UUID] = mapped_column(unique=True)
    chat_id: Mapped[UUID]
    retry_of_run_id: Mapped[UUID | None]
    owner_id: Mapped[UUID]
    creator_email: Mapped[str] = mapped_column(String(320))
    authorization_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    settings: Mapped[dict[str, object]] = mapped_column(JSONB)
    status: Mapped[RunStatus] = mapped_column(String(16))
    revision: Mapped[int]
    claim_count: Mapped[int] = mapped_column(server_default=text("0"))
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model_revision_at_dispatch: Mapped[str | None] = mapped_column(String(128))
    failure: Mapped[RunFailure | None] = mapped_column(String(24))
    evidence: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))
    answer: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))


def settle_workspace_run(
    run: WorkspaceChatRunRow, status: TerminalRunStatus, failure: RunFailure | None = None
) -> None:
    run.status = status
    run.failure = failure
    if status != "completed":
        run.evidence = None
        run.answer = None
    run.lease = None
    run.lease_until = None
    run.revision += 1


async def load_pinned_evidence(
    session: AsyncSession,
    owner_id: UUID,
    notebook_id: UUID,
    pin: WorkspacePassagePin,
    *,
    lock: bool = False,
) -> tuple[WorkspaceRunEvidence, str]:
    version = await WorkspaceTextRepository(session).get_version(
        owner_id, notebook_id, pin.source_id, pin.version_id, lock=lock
    )
    block = next((block for block in version.blocks if block.block_id == pin.block_id), None)
    if block is None:
        raise WorkspaceTextNotFound
    excerpt = block.text[:WORKSPACE_ANSWER_MAX_PASSAGE_CHARS]
    evidence = WorkspaceRunEvidence(
        source=WorkspaceTextSource.model_validate(version.model_dump()),
        block_id=block.block_id,
        start_char=block.start_char,
        end_char=block.start_char + len(excerpt),
        excerpt=excerpt,
    )
    return evidence, sha256(block.model_dump_json().encode("utf-8")).hexdigest()


class WorkspaceRunRepository:
    def __init__(
        self, session: AsyncSession, notebook_id: UUID, identity: VerifiedWorkspaceIdentity
    ) -> None:
        self.session = session
        self.notebook_id = notebook_id
        self.identity = identity

    async def get_run(self, chat_id: UUID, run_id: UUID) -> WorkspaceTurnView:
        turn, run = await self._run(chat_id, run_id)
        return await self._view(turn, run)

    async def stop_run(self, chat_id: UUID, run_id: UUID) -> WorkspaceTurnView:
        turn, run = await self._run(chat_id, run_id, lock=True)
        if run.status in ACTIVE_RUN_STATUSES:
            settle_workspace_run(run, "stopped")
        return await self._view(turn, run)

    async def retry_run(
        self, chat_id: UUID, run_id: UUID, submission_key: UUID
    ) -> WorkspaceTurnView:
        turn, run = await self._run(chat_id, run_id)
        if run.status not in RETRYABLE_RUN_STATUSES:
            raise WorkspaceRunNotRetryable
        return await self.submit(
            chat_id,
            WorkspaceTurnSubmit(
                submission_key=submission_key,
                question=turn.question,
                draft_revision=0,
                passage=WorkspacePassagePin.model_validate(turn.pin),
            ),
            WorkspaceLocalRunSettings.model_validate(run.settings),
            retry_source=(run.id, turn.passage_digest),
        )

    async def _run(
        self,
        chat_id: UUID,
        run_id: UUID,
        *,
        lock: bool = False,
    ) -> tuple[WorkspaceChatTurnRow, WorkspaceChatRunRow]:
        run, turn, chat, notebook = (
            WorkspaceChatRunRow,
            WorkspaceChatTurnRow,
            WorkspaceChatRow,
            WorkspaceNotebookRow,
        )
        query = (
            select(turn, run)
            .join(run, run.turn_id == turn.id)
            .join(chat, chat.id == run.chat_id)
            .join(notebook, notebook.id == chat.notebook_id)
            .where(
                run.id == run_id,
                chat.id == chat_id,
                notebook.id == self.notebook_id,
                run.owner_id == self.identity.account_id,
                notebook.owner_id == self.identity.account_id,
            )
        )
        pair = (
            await self.session.execute(query.with_for_update(of=run) if lock else query)
        ).one_or_none()
        if pair is None:
            raise WorkspaceRunNotFound
        saved_turn, saved_run = pair
        return saved_turn, saved_run

    async def submit(
        self,
        chat_id: UUID,
        body: WorkspaceTurnSubmit,
        settings: WorkspaceLocalRunSettings,
        *,
        retry_source: tuple[UUID, str] | None = None,
    ) -> WorkspaceTurnView:
        chat = await self._chat(chat_id, lock=True)
        turn = WorkspaceChatTurnRow
        run = WorkspaceChatRunRow
        fingerprint = (
            body.fingerprint()
            if retry_source is None
            else sha256(f"retry:{retry_source[0]}".encode()).hexdigest()
        )
        previous = (
            await self.session.execute(
                select(turn, run)
                .join(run, run.turn_id == turn.id)
                .where(turn.chat_id == chat_id, turn.submission_key == body.submission_key)
            )
        ).one_or_none()
        if previous is not None:
            if previous[1].owner_id != self.identity.account_id:
                raise WorkspaceChatNotFound
            if previous[0].fingerprint != fingerprint:
                raise WorkspaceSubmissionConflict
            return await self._view(previous[0], previous[1])
        if analyze_query(body.question).kind == QueryKind.TEMPORAL:
            raise WorkspaceTemporalQuestion
        if retry_source is None and (
            chat.draft_revision != body.draft_revision or chat.draft_text != body.question
        ):
            raise WorkspaceDraftConflict
        if (
            await self.session.scalar(
                select(run.id).where(run.chat_id == chat_id, run.status.in_(ACTIVE_RUN_STATUSES))
            )
            is not None
        ):
            raise WorkspaceActiveRun
        _, digest = await load_pinned_evidence(
            self.session, self.identity.account_id, self.notebook_id, body.passage, lock=True
        )
        if retry_source is not None and digest != retry_source[1]:
            raise WorkspaceTextNotFound
        sequence = await self.session.scalar(
            select(func.max(turn.sequence)).where(turn.chat_id == chat_id)
        )
        now = datetime.now(UTC)
        expiry = self.identity.token_expires_at
        if expiry is None or expiry <= now:
            raise WorkspaceRunAuthorizationExpired
        accepted = WorkspaceChatTurnRow(
            id=uuid4(),
            chat_id=chat_id,
            sequence=(sequence or 0) + 1,
            submission_key=body.submission_key,
            fingerprint=fingerprint,
            question=body.question,
            pin=body.passage.model_dump(mode="json"),
            passage_digest=digest,
            created_at=now,
        )
        queued = WorkspaceChatRunRow(
            id=uuid4(),
            turn_id=accepted.id,
            chat_id=chat_id,
            retry_of_run_id=retry_source[0] if retry_source else None,
            owner_id=self.identity.account_id,
            creator_email=self.identity.email.casefold(),
            authorization_expires_at=min(now + AUTHORIZATION_LIFETIME, expiry),
            settings=settings.model_dump(mode="json"),
            status="queued",
            revision=0,
            claim_count=0,
            queued_at=now,
        )
        self.session.add(accepted)
        await self.session.flush()
        self.session.add(queued)
        if retry_source is None:
            chat.draft_text = ""
            chat.draft_revision += 1
        chat.updated_at = now
        await self.session.flush()
        return await self._view(accepted, queued)

    async def list(self, chat_id: UUID, before: int | None, limit: int) -> WorkspaceTurnsPage:
        await self._chat(chat_id)
        turn = WorkspaceChatTurnRow
        run = WorkspaceChatRunRow
        query = (
            select(turn, run)
            .join(run, run.turn_id == turn.id)
            .where(
                turn.chat_id == chat_id,
                run.owner_id == self.identity.account_id,
            )
        )
        if before is not None:
            query = query.where(turn.sequence < before)
        rows = (
            await self.session.execute(query.order_by(turn.sequence.desc()).limit(limit + 1))
        ).all()
        return WorkspaceTurnsPage(
            turns=tuple(
                [await self._view(item, current) for item, current in reversed(rows[:limit])]
            ),
            next_before=rows[limit - 1][0].sequence if len(rows) > limit else None,
        )

    async def _chat(self, chat_id: UUID, *, lock: bool = False) -> WorkspaceChatRow:
        query = (
            select(WorkspaceChatRow)
            .join(
                WorkspaceNotebookRow,
                WorkspaceNotebookRow.id == WorkspaceChatRow.notebook_id,
            )
            .where(
                WorkspaceChatRow.id == chat_id,
                WorkspaceChatRow.notebook_id == self.notebook_id,
                WorkspaceNotebookRow.owner_id == self.identity.account_id,
            )
        )
        chat = await self.session.scalar(
            query.with_for_update(of=WorkspaceChatRow) if lock else query
        )
        if chat is None:
            raise WorkspaceChatNotFound
        return chat

    async def _view(
        self, turn: WorkspaceChatTurnRow, run: WorkspaceChatRunRow
    ) -> WorkspaceTurnView:
        pin = WorkspacePassagePin.model_validate(turn.pin)
        status, failure = run.status, run.failure
        evidence = answer = None
        if run.answer is not None and run.evidence is not None:
            try:
                current, digest = await load_pinned_evidence(
                    self.session, self.identity.account_id, self.notebook_id, pin, lock=True
                )
            except WorkspaceTextNotFound:
                digest = None
                current = None
            if digest == turn.passage_digest and current == WorkspaceRunEvidence.model_validate(
                run.evidence
            ):
                evidence = current
                answer = WorkspaceAnswerDraft.model_validate(run.answer)
            else:
                status, failure = "failed", "evidence_unavailable"
        return WorkspaceTurnView(
            turn_id=turn.id,
            sequence=turn.sequence,
            question=turn.question,
            passage=pin,
            created_at=turn.created_at,
            run=WorkspaceRunView(
                run_id=run.id,
                status=status,
                revision=run.revision,
                settings=WorkspaceLocalRunSettings.model_validate(run.settings),
                model_revision_at_dispatch=run.model_revision_at_dispatch,
                retry_of_run_id=run.retry_of_run_id,
                execution=WorkspaceRunExecutionView(
                    claims=run.claim_count, possible_dispatch_at=run.dispatch_at
                ),
                failure=failure,
                evidence=evidence,
                answer=answer,
            ),
        )
