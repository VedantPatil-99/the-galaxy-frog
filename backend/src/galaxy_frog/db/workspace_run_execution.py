"""Short, fenced transactions around the local worker's possible dispatch."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, update
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
        now = (await self.session.execute(select(func.now()))).scalar_one()
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
        evidence, reason = await self._authorized_evidence(run, turn, notebook_id)
        if reason is not None:
            settle_workspace_run(run, "failed", reason)
            return None
        assert evidence is not None
        run.evidence = evidence.model_dump(mode="json")
        run.model_revision_at_dispatch = model_revision
        run.dispatch_at = datetime.now(UTC)
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
            _, failure = await self._authorized_evidence(run, turn, notebook_id)
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

    async def _authorized_evidence(
        self,
        run: WorkspaceChatRunRow,
        turn: WorkspaceChatTurnRow,
        notebook_id: UUID,
    ) -> tuple[WorkspaceRunEvidence | None, RunFailure | None]:
        if (
            run.authorization_expires_at <= datetime.now(UTC)
            or run.creator_email not in self.allowed_creators
        ):
            return None, "access_changed"
        pin = WorkspacePassagePin.model_validate(turn.pin)
        try:
            evidence, digest = await load_pinned_evidence(
                self.session, run.owner_id, notebook_id, pin, lock=True
            )
        except WorkspaceTextNotFound:
            return None, "evidence_unavailable"
        if digest != turn.passage_digest:
            return None, "evidence_unavailable"
        return evidence, None
