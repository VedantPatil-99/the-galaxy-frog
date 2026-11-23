"""Short, fenced transactions around the local worker's possible dispatch."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import DateTime, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.db.workspace_chats import WorkspaceChatRow
from galaxy_frog.db.workspace_runs import (
    WorkspaceChatRunRow,
    WorkspaceChatTurnRow,
    load_pinned_evidence,
    settle_workspace_run,
)
from galaxy_frog.domain.generation.workspace_answer import WorkspaceAnswerDraft
from galaxy_frog.domain.workspace_run import (
    RunFailure,
    WorkspaceLocalRunSettings,
    WorkspacePassagePin,
    WorkspaceRunEvidence,
)
from galaxy_frog.domain.workspace_text import WorkspaceTextNotFound

LOCAL_LEASE = timedelta(seconds=30)
MAX_PREPARATION_CLAIMS = 3


class WorkspaceRunLeaseExpired(Exception):
    """The claim expired while waiting for the evidence fence."""


@dataclass(frozen=True, slots=True)
class WorkspaceRunClaim:
    run_id: UUID
    lease: UUID
    question: str
    settings: WorkspaceLocalRunSettings


class WorkspaceRunExecution:
    def __init__(self, session: AsyncSession, allowed_creators: frozenset[str]) -> None:
        self.session = session
        self.allowed_creators = allowed_creators

    async def claim(self) -> WorkspaceRunClaim | None:
        run = WorkspaceChatRunRow
        turn = WorkspaceChatTurnRow
        row = (
            await self.session.execute(
                select(run, turn)
                .join(turn, turn.id == run.turn_id)
                .where(
                    or_(
                        run.status == "queued",
                        and_(
                            run.status.in_(("preparing", "running")),
                            run.lease_until <= func.clock_timestamp(),
                        ),
                    )
                )
                .order_by(run.queued_at, run.id)
                .limit(1)
                .with_for_update(of=run, skip_locked=True)
            )
        ).one_or_none()
        if row is None:
            return None
        current, saved = row
        if current.dispatch_at is not None:
            settle_workspace_run(current, "uncertain", "dispatch_unknown")
            return None
        if current.claim_count >= MAX_PREPARATION_CLAIMS:
            settle_workspace_run(current, "failed", "recovery_exhausted")
            return None
        lease = uuid4()
        now = await self._clock()
        current.status = "preparing"
        current.lease = lease
        current.lease_until = now + LOCAL_LEASE
        current.claim_count += 1
        current.revision += 1
        return WorkspaceRunClaim(
            current.id,
            lease,
            saved.question,
            WorkspaceLocalRunSettings.model_validate(current.settings),
        )

    async def renew(self, claim: WorkspaceRunClaim) -> bool:
        run = WorkspaceChatRunRow
        renewed = await self.session.scalar(
            update(run)
            .where(
                run.id == claim.run_id,
                run.lease == claim.lease,
                run.status.in_(("preparing", "running")),
                run.lease_until > func.clock_timestamp(),
            )
            .values(lease_until=func.clock_timestamp() + LOCAL_LEASE)
            .returning(run.id)
        )
        return renewed is not None

    async def prepare(
        self,
        claim: WorkspaceRunClaim,
        model_revision: str | None,
    ) -> WorkspaceRunEvidence | None:
        owned = await self._fenced(claim, dispatched=False)
        if owned is None:
            return None
        run, turn, notebook_id = owned
        if model_revision is None:
            settle_workspace_run(run, "failed", "model_unavailable")
            return None
        try:
            evidence, reason, now = await self._authorized_evidence(run, turn, notebook_id)
        except WorkspaceRunLeaseExpired:
            return None
        if reason is not None:
            settle_workspace_run(run, "failed", reason)
            return None
        assert evidence is not None
        run.evidence = evidence.model_dump(mode="json")
        run.model_revision_at_dispatch = model_revision
        run.dispatch_at = now
        run.status = "running"
        run.revision += 1
        return evidence

    async def finish(
        self,
        claim: WorkspaceRunClaim,
        answer: WorkspaceAnswerDraft | None,
        failure: RunFailure | None,
    ) -> None:
        owned = await self._fenced(claim, dispatched=True)
        if owned is None:
            return
        run, turn, notebook_id = owned
        if answer is not None:
            try:
                _, failure, _ = await self._authorized_evidence(run, turn, notebook_id)
            except WorkspaceRunLeaseExpired:
                return
            if failure is None:
                run.answer = answer.model_dump(mode="json")
        status = (
            "uncertain" if failure == "dispatch_unknown" else "failed" if failure else "completed"
        )
        settle_workspace_run(run, status, failure)

    async def _fenced(
        self,
        claim: WorkspaceRunClaim,
        *,
        dispatched: bool,
    ) -> tuple[WorkspaceChatRunRow, WorkspaceChatTurnRow, UUID] | None:
        run, turn, chat = WorkspaceChatRunRow, WorkspaceChatTurnRow, WorkspaceChatRow
        row = (
            await self.session.execute(
                select(run, turn, chat.notebook_id)
                .join(turn, turn.id == run.turn_id)
                .join(chat, chat.id == run.chat_id)
                .where(
                    run.id == claim.run_id,
                    run.status == ("running" if dispatched else "preparing"),
                    run.lease == claim.lease,
                    run.lease_until > func.clock_timestamp(),
                    run.dispatch_at.is_not(None) if dispatched else run.dispatch_at.is_(None),
                )
                .with_for_update(of=run)
            )
        ).one_or_none()
        if row is None:
            return None
        current, saved, notebook_id = row
        return current, saved, notebook_id

    async def _clock(self) -> datetime:
        return (
            await self.session.execute(select(func.clock_timestamp(type_=DateTime(timezone=True))))
        ).scalar_one()

    async def _authorized_evidence(
        self,
        run: WorkspaceChatRunRow,
        turn: WorkspaceChatTurnRow,
        notebook_id: UUID,
    ) -> tuple[WorkspaceRunEvidence | None, RunFailure | None, datetime]:
        evidence = None
        failure: RunFailure | None = None
        if run.creator_email not in self.allowed_creators:
            failure = "access_changed"
        else:
            try:
                evidence, digest = await load_pinned_evidence(
                    self.session,
                    run.owner_id,
                    notebook_id,
                    WorkspacePassagePin.model_validate(turn.pin),
                    lock=True,
                )
            except WorkspaceTextNotFound:
                failure = "evidence_unavailable"
            else:
                if digest != turn.passage_digest:
                    failure = "evidence_unavailable"
        # The evidence read can block on row locks. Fence both the lease and the grant
        # using the database clock after those locks are in hand.
        now = await self._clock()
        assert run.lease_until is not None  # The fenced query requires a live lease.
        if run.lease_until <= now:
            raise WorkspaceRunLeaseExpired
        if run.authorization_expires_at <= now:
            failure = "access_changed"
        return evidence, failure, now
