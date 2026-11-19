"""Per-chapter provider admission while preserving one provider per saved artifact."""

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from galaxy_frog.adapters.generation.gemini import GeminiJSON
from galaxy_frog.adapters.generation.study_gemini import GeminiStudyGenerator
from galaxy_frog.application.provider_policy import (
    ProviderAttempt,
    ProviderCode,
    ProviderFailure,
    ProviderRouting,
)
from galaxy_frog.application.provider_registry import (
    builtin_profiles,
    probe_ollama_models,
    profile_validation_error,
    provider_catalog,
)
from galaxy_frog.application.study_generation import DraftBatch, StudyGenerator
from galaxy_frog.config import Settings
from galaxy_frog.db.provider_profiles import PostgresProviderProfiles
from galaxy_frog.db.provider_runtime import PostgresProviderRuntime
from galaxy_frog.db.study_repository import ClaimedStudyRun
from galaxy_frog.domain.providers import Capability, ProviderOperation, ProviderProfile
from galaxy_frog.domain.study import StudyCitation, StudyError, StudyKind


class RoutedStudyGenerator:
    def __init__(
        self,
        settings: Settings,
        policy: ProviderProfile,
        runtime: PostgresProviderRuntime,
        local: StudyGenerator,
    ) -> None:
        self.settings, self.policy, self.runtime, self.local = settings, policy, runtime, local
        self.catalog = provider_catalog(settings)
        self.chosen: str | None = None
        self.usage: dict[str, int] | None = None
        self.revisions: dict[str, str] = {}
        strict_cloud = policy.candidates.get(Capability.STUDY) == ("gemini-study",)
        self.evidence_characters = (
            settings.gemini_study_evidence_characters if strict_cloud else 1400
        )
        self.excerpt_characters = settings.gemini_study_excerpt_characters if strict_cloud else 320

    async def draft(self, kind: StudyKind, evidence: tuple[StudyCitation, ...]) -> DraftBatch:
        async def local() -> DraftBatch:
            models = await probe_ollama_models(self.settings.ollama_base_url)
            revision = models.get(self.settings.generation_model) or models.get(
                f"{self.settings.generation_model}:latest"
            )
            if not revision or (
                self.settings.generation_model_revision
                and revision != self.settings.generation_model_revision
            ):
                raise ProviderFailure(ProviderCode.SETUP_REQUIRED)
            self.revisions["ollama-study"] = revision
            try:
                return await self.local.draft(kind, evidence)
            except StudyError:
                raise ProviderFailure(ProviderCode.UNAVAILABLE) from None

        async def cloud() -> DraftBatch:
            key, _project = self.settings.gemini_credentials("study")
            assert key is not None and self.settings.gemini_study_model is not None
            entry = next(item for item in self.catalog if item.provider_id == "gemini-study")

            async def verify(tokens: int) -> None:
                await self.runtime.verify_consent(entry, dispatch=True, input_tokens=tokens)

            provider = GeminiJSON(
                key.get_secret_value(),
                self.settings.gemini_study_model,
                revision=self.settings.gemini_study_revision,
                thinking=self.settings.gemini_study_thinking,
                max_output_tokens=self.settings.gemini_study_max_output_tokens,
                timeout_seconds=self.settings.gemini_timeout_seconds,
                before_dispatch=verify,
            )
            try:
                return await GeminiStudyGenerator(provider).draft(kind, evidence)
            finally:
                self.usage = provider.usage

        async def record(attempt: ProviderAttempt) -> None:
            entry = next(item for item in self.catalog if item.provider_id == attempt.provider_id)
            if attempt.status == "succeeded":
                self.chosen = attempt.provider_id
            revision = self.revisions.get(attempt.provider_id, attempt.revision)
            await self.runtime.record(
                Capability.STUDY,
                attempt.model_copy(
                    update={
                        "revision": revision,
                        "requested_candidates": self.policy.candidates.get(Capability.STUDY, ()),
                    }
                ),
                pricing=entry.pricing,
                usage=self.usage if attempt.provider_id == "gemini-study" else None,
            )

        policy = self.policy
        if self.chosen is not None:
            policy = policy.model_copy(update={"candidates": {Capability.STUDY: (self.chosen,)}})
        try:
            return await ProviderRouting(policy, self.catalog, self.runtime.admit, record).execute(
                Capability.STUDY,
                {"ollama-study": local, "gemini-study": cloud},
                timeout_seconds=90,
            )
        except ProviderFailure as failure:
            action = (
                f"Retry after {failure.retry_at.isoformat()}."
                if failure.retry_at
                else "Check provider settings and grant fresh cloud consent before retrying."
            )
            raise StudyError(
                failure.code.value,
                "The selected Study provider could not complete generation.",
                status=503,
                retryable=failure.code
                not in {ProviderCode.SAFETY_BLOCKED, ProviderCode.INVALID_INPUT},
                action=action,
            ) from None


async def selected_study_generator(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    claim: ClaimedStudyRun,
    local: StudyGenerator,
) -> RoutedStudyGenerator:
    selection = claim.run.provider_selection
    assert selection is not None
    async with sessions() as session:
        version = await PostgresProviderProfiles(session).get(selection.profile)
    if version is None:
        raise StudyError(
            "PROVIDER_PROFILE_NOT_FOUND",
            "The selected provider profile version does not exist.",
            status=422,
        )
    policy = version.policy
    if policy.evaluation:
        # Study must not reinterpret a complete evaluation pin as its current
        # text collection. The research/evaluation workflow is a later phase.
        raise StudyError(
            "PROVIDER_EVALUATION_UNVERIFIABLE",
            "Study generation does not support evaluation profiles.",
            status=422,
        )
    if profile_validation_error(policy, provider_catalog(settings)):
        raise StudyError(
            "PROVIDER_PROFILE_INVALID", "The selected provider profile is incompatible.", status=422
        )
    if Capability.STUDY not in policy.candidates:
        policy = policy.model_copy(
            update={
                "candidates": {
                    Capability.STUDY: builtin_profiles()["local"].candidates[Capability.STUDY]
                }
            }
        )
    runtime = PostgresProviderRuntime(
        sessions,
        engine=engine,
        profile=selection.profile,
        operation=ProviderOperation.STUDY,
        run_id=claim.run.run_id,
        source=str(claim.run.video_id),
        consent_id=selection.consent_id,
        study_lease_token=claim.token,
    )
    return RoutedStudyGenerator(settings, policy, runtime, local)
