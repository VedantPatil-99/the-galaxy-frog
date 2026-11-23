"""Execute one configured local Ollama target per durable chat run."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from galaxy_frog.adapters.generation.ollama import OllamaGenerationProvider
from galaxy_frog.application.provider_registry import probe_ollama_models
from galaxy_frog.config import Settings
from galaxy_frog.db.workspace_run_execution import WorkspaceRunClaim, WorkspaceRunExecution
from galaxy_frog.domain.generation.models import GenerationProviderError
from galaxy_frog.domain.generation.workspace_answer import (
    WorkspaceAnswerDraft,
    WorkspaceAnswerDraftInvalid,
    WorkspaceAnswerPassage,
    WorkspaceAnswerProvider,
    validate_workspace_citations,
)
from galaxy_frog.domain.workspace_run import RunFailure

logger = logging.getLogger(__name__)


class WorkspaceChatWorker:
    def __init__(
        self,
        settings: Settings,
        sessions: async_sessionmaker[AsyncSession],
        provider: WorkspaceAnswerProvider,
        probe: Callable[[str], Awaitable[dict[str, str]]] = probe_ollama_models,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.provider = provider
        self.probe = probe
        self.allowed_creators = frozenset(
            email.casefold()
            for email in settings.workspace_creator_emails
            if settings.supabase_url and settings.supabase_publishable_key
        )

    async def run_once(self) -> bool:
        async with self.sessions.begin() as session:
            claim = await WorkspaceRunExecution(session, self.allowed_creators).claim()
        if claim is None:
            return False

        revision = await self._local_revision(claim)
        async with self.sessions.begin() as session:
            evidence = await WorkspaceRunExecution(session, self.allowed_creators).prepare(
                claim, revision
            )
        if evidence is None:
            return True

        passages = (WorkspaceAnswerPassage("E1", "text", evidence.excerpt),)
        answer: WorkspaceAnswerDraft | None = None
        failure: RunFailure | None = None
        try:
            async with asyncio.timeout(90):
                generated = await self.provider.generate_workspace_answer(claim.question, passages)
            validate_workspace_citations(generated.draft, passages)
            if await self._local_revision(claim) == revision:
                answer = generated.draft
            else:
                failure = "model_unavailable"
        except GenerationProviderError, TimeoutError:
            failure = "dispatch_unknown"
        except WorkspaceAnswerDraftInvalid:
            failure = "answer_unverified"

        async with self.sessions.begin() as session:
            await WorkspaceRunExecution(session, self.allowed_creators).finish(
                claim, answer, failure
            )
        return True

    async def _local_revision(self, claim: WorkspaceRunClaim) -> str | None:
        model = claim.settings.model
        if model != self.settings.generation_model:
            return None
        try:
            async with asyncio.timeout(5):
                installed = await self.probe(self.settings.ollama_base_url)
        except TimeoutError:
            return None
        revision = installed.get(model) or installed.get(f"{model}:latest")
        required = {claim.settings.required_revision, self.settings.generation_model_revision} - {
            None
        }
        if not required.issubset({revision}):
            return None
        return revision


async def run_workspace_chat_worker(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    stop: asyncio.Event,
) -> None:
    if not settings.workspace_auth_required:
        return
    worker = WorkspaceChatWorker(
        settings,
        sessions,
        OllamaGenerationProvider(
            base_url=settings.ollama_base_url, model=settings.generation_model
        ),
    )
    while not stop.is_set():
        try:
            if await worker.run_once():
                continue
        except (SQLAlchemyError, OSError) as exc:
            logger.warning(
                "Workspace run polling needs PostgreSQL and migration 0028 (%s).",
                type(exc).__name__,
            )
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=settings.ingestion_poll_seconds)
