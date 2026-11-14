"""Selective reasoning with current privacy checks, verified frames, and durable cache ports."""

import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from functools import partial
from hashlib import sha256
from time import perf_counter
from uuid import UUID

from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure, ProviderRouting
from galaxy_frog.application.retrieval.temporal import RetrievalStatus, TemporalRetrievalResult
from galaxy_frog.application.retrieval.visual import VisualRetrievalResult, frame_lineage
from galaxy_frog.domain.providers import Capability, ProviderMode
from galaxy_frog.domain.retrieval.pipeline import RetrievalWarning
from galaxy_frog.domain.vision.policy import VisualDecision, external_allowed
from galaxy_frog.domain.vision.reasoning_cache import (
    ReasoningFrameVerifier,
    VisualReasoningCache,
    reasoning_cache_key,
)
from galaxy_frog.domain.vision.visual import (
    ExecutionLocation,
    VisualFailureCode,
    VisualFrame,
    VisualProviderError,
    VisualProviderIdentity,
    VisualReasoningRequest,
    VisualReasoningResult,
)
from galaxy_frog.domain.vision.visual_ports import VisualReasoningProvider


@dataclass(frozen=True, slots=True)
class ReasoningExecution:
    decision: VisualDecision
    reason: str
    provider: VisualProviderIdentity | None
    frames: tuple[VisualFrame, ...]
    result: VisualReasoningResult | None
    cache_hit: bool
    cache_key: str | None
    external_permitted: bool
    elapsed_ms: float
    warnings: tuple[RetrievalWarning, ...] = ()

    @property
    def degraded(self) -> bool:
        return bool(self.warnings)

    def trace_payload(self) -> dict[str, object]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "provider": asdict(self.provider) if self.provider else None,
            "frames_examined": [frame_lineage(frame) for frame in self.frames],
            "cache_hit": self.cache_hit,
            "cache_key": self.cache_key,
            "external_permitted": self.external_permitted,
            "elapsed_ms": self.elapsed_ms,
            "provider_elapsed_ms": self.result.elapsed_ms if self.result else None,
            "insufficient_evidence": self.result.insufficient_evidence if self.result else None,
            "cited_frame_ids": [str(i) for i in self.result.cited_frame_ids] if self.result else [],
            "warnings": [asdict(warning) for warning in self.warnings],
            "degraded": self.degraded,
        }


class AdaptiveVisualReasoning:
    def __init__(
        self,
        *,
        provider: VisualReasoningProvider | None,
        cache: VisualReasoningCache,
        verifier: ReasoningFrameVerifier,
        external_enabled: bool = False,
        routing: ProviderRouting | None = None,
        candidates: dict[str, VisualReasoningProvider] | None = None,
        check_consent: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._provider, self._cache, self._verifier, self._external_enabled = (
            provider,
            cache,
            verifier,
            external_enabled,
        )
        self._routing = routing
        self._candidates = candidates or {}
        self._check_consent = check_consent

    @property
    def configuration(self) -> dict[str, object]:
        return {
            "provider": asdict(self._provider.identity) if self._provider else None,
            "external_enabled": self._external_enabled,
            "prompt_version": "visual-grounding-v1",
            "schema_version": "visual-answer-v1",
            "local_frame_limit": 1,
        }

    async def execute(
        self,
        video_id: UUID,
        question: str,
        text: TemporalRetrievalResult,
        visual: VisualRetrievalResult,
        *,
        external_permission: bool = False,
        allow_fallback: bool = True,
    ) -> ReasoningExecution:
        if (
            text.status != RetrievalStatus.RESOLVED
            or visual.decisions[-1].decision != VisualDecision.REASON
        ):
            return ReasoningExecution(
                visual.decisions[-1].decision,
                "reasoning_not_required",
                self._provider.identity if self._provider else None,
                (),
                None,
                False,
                None,
                False,
                0,
            )
        context = "\n".join(
            [
                *(unit.text for group in text.groups for unit in group.units),
                *(item.block.text for item in visual.ocr),
            ]
        )
        lineage = {
            "units": [
                {
                    "unit_id": unit.unit_id,
                    "cue_ids": unit.cue_ids,
                    "start_ms": unit.start_ms,
                    "end_ms": unit.end_ms,
                }
                for group in text.groups
                for unit in group.units
            ],
            "ocr": [
                {
                    "block_id": str(item.block_id),
                    "frame_id": str(item.frame_id),
                    "capture_ms": item.capture_ms,
                    "provider": asdict(item.block.provider_spec),
                    "start_ms": item.block.start_ms,
                    "end_ms": item.block.end_ms,
                }
                for item in visual.ocr
            ],
            "visual_collections": [
                {"id": str(hit.collection_id), "spec": asdict(hit.collection)}
                for hit in visual.hits
            ],
        }
        return await self.reason_with_context(
            video_id,
            question,
            tuple(hit.frame for hit in visual.hits[:1]),
            context,
            evidence_fingerprint=sha256(json.dumps(lineage, sort_keys=True).encode()).hexdigest(),
            decision_reason=visual.decisions[-1].reason,
            external_permission=external_permission,
            allow_fallback=allow_fallback,
        )

    async def reason_with_context(
        self,
        video_id: UUID,
        question: str,
        frames: tuple[VisualFrame, ...],
        context: str,
        *,
        evidence_fingerprint: str,
        decision_reason: str,
        external_permission: bool,
        allow_fallback: bool = True,
    ) -> ReasoningExecution:
        if self._routing is not None:

            async def attempt(
                provider_id: str, provider: VisualReasoningProvider
            ) -> ReasoningExecution:
                try:
                    return await self._reason(
                        provider,
                        video_id,
                        question,
                        frames,
                        context,
                        evidence_fingerprint=evidence_fingerprint,
                        decision_reason=decision_reason,
                        external_permission=provider.identity.location
                        is ExecutionLocation.EXTERNAL,
                        allow_fallback=False,
                        provider_id=provider_id,
                    )
                except VisualProviderError as exc:
                    code = {
                        VisualFailureCode.OUTCOME_UNKNOWN: ProviderCode.OUTCOME_UNKNOWN,
                        VisualFailureCode.SAFETY_BLOCKED: ProviderCode.SAFETY_BLOCKED,
                        VisualFailureCode.RATE_LIMITED: ProviderCode.RATE_LIMITED,
                        VisualFailureCode.AUTH_FAILED: ProviderCode.AUTH_FAILED,
                        VisualFailureCode.INVALID_OUTPUT: ProviderCode.INVALID_OUTPUT,
                        VisualFailureCode.TIMEOUT: (
                            ProviderCode.OUTCOME_UNKNOWN
                            if provider.identity.location is ExecutionLocation.EXTERNAL
                            else ProviderCode.TIMEOUT
                        ),
                    }.get(exc.code, ProviderCode.UNAVAILABLE)
                    raise ProviderFailure(code) from exc

            try:
                return await self._routing.execute(
                    Capability.VISION,
                    {
                        provider_id: partial(attempt, provider_id, provider)
                        for provider_id, provider in self._candidates.items()
                    },
                    timeout_seconds=180,
                    fallback=allow_fallback,
                )
            except ProviderFailure as exc:
                if (
                    not allow_fallback
                    or (
                        self._routing.profile.mode is ProviderMode.MANUAL
                        and len(self._routing.profile.candidates.get(Capability.VISION, ())) == 1
                    )
                    or exc.code in {ProviderCode.OUTCOME_UNKNOWN, ProviderCode.SAFETY_BLOCKED}
                ):
                    raise
                return ReasoningExecution(
                    VisualDecision.UNAVAILABLE,
                    exc.code.value,
                    None,
                    (),
                    None,
                    False,
                    None,
                    False,
                    0,
                    (
                        RetrievalWarning(
                            exc.code.value,
                            "Visual reasoning was unavailable. Retrieved evidence was retained.",
                        ),
                    ),
                )
        return await self._reason(
            self._provider,
            video_id,
            question,
            frames,
            context,
            evidence_fingerprint=evidence_fingerprint,
            decision_reason=decision_reason,
            external_permission=external_permission,
            allow_fallback=allow_fallback,
        )

    async def _reason(
        self,
        provider: VisualReasoningProvider | None,
        video_id: UUID,
        question: str,
        frames: tuple[VisualFrame, ...],
        context: str,
        *,
        evidence_fingerprint: str,
        decision_reason: str,
        external_permission: bool,
        allow_fallback: bool,
        provider_id: str | None = None,
    ) -> ReasoningExecution:
        started = perf_counter()
        identity = provider.identity if provider else None
        selected: tuple[VisualFrame, ...] = ()
        key = None
        permitted = False
        context_warning: tuple[RetrievalWarning, ...] = ()
        try:
            if provider is None:
                raise VisualProviderError(VisualFailureCode.UNAVAILABLE)
            permitted = self._external_enabled and external_permission
            if not external_allowed(
                provider.identity.location,
                configured=self._external_enabled,
                permitted=external_permission,
            ):
                raise VisualProviderError(VisualFailureCode.PRIVACY_DENIED)
            selected = frames[:1]
            # The local 2048-token window must leave room for the image and answer.
            context_limit = 1200 if provider.identity.location == ExecutionLocation.LOCAL else 3000
            evidence = context[:context_limit]
            if len(context) > context_limit:
                context_warning = (
                    RetrievalWarning(
                        "visual_context_limit",
                        f"Only the first {context_limit} characters of retrieved text "
                        "were sent to visual reasoning.",
                    ),
                )
            request = VisualReasoningRequest(
                video_id,
                question,
                selected,
                evidence,
                external_permitted=external_permission,
                evidence_fingerprint=evidence_fingerprint,
            )
            await self._verifier.verify(selected)
            key = reasoning_cache_key(request, provider.identity)
            if (
                provider_id
                and self._check_consent
                and permitted
                and provider.identity.location is ExecutionLocation.EXTERNAL
            ):
                await self._check_consent(provider_id)
            result = await self._cache.get(video_id, key)
            cache_hit = result is not None
            if result is None:
                if (
                    provider_id
                    and self._check_consent
                    and permitted
                    and provider.identity.location is ExecutionLocation.EXTERNAL
                ):
                    await self._check_consent(provider_id)
                result = await provider.reason(request)
            if result.provider != provider.identity or not set(result.cited_frame_ids).issubset(
                {frame.frame_id for frame in selected}
            ):
                raise VisualProviderError(VisualFailureCode.INVALID_OUTPUT)
            if not cache_hit:
                await self._cache.put(video_id, key, result)
            return ReasoningExecution(
                VisualDecision.INSUFFICIENT
                if result.insufficient_evidence
                else VisualDecision.REASON,
                "visual_evidence_insufficient" if result.insufficient_evidence else decision_reason,
                identity,
                selected,
                result,
                cache_hit,
                key,
                permitted,
                (perf_counter() - started) * 1000,
                context_warning,
            )
        except VisualProviderError as exc:
            if not allow_fallback:
                raise
            warning = RetrievalWarning(
                exc.code.value,
                "Visual reasoning was unavailable or not permitted; retrieved evidence was retained.",
            )
            return ReasoningExecution(
                VisualDecision.PRIVACY_DENIED
                if exc.code == VisualFailureCode.PRIVACY_DENIED
                else VisualDecision.UNAVAILABLE,
                exc.code.value,
                identity,
                selected,
                None,
                False,
                key,
                permitted,
                (perf_counter() - started) * 1000,
                (*context_warning, warning),
            )
