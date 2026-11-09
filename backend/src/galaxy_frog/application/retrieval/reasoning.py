"""Selective reasoning with current privacy checks, verified frames, and durable cache ports."""

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from time import perf_counter
from uuid import UUID

from galaxy_frog.application.retrieval.temporal import RetrievalStatus, TemporalRetrievalResult
from galaxy_frog.application.retrieval.visual import VisualRetrievalResult, frame_lineage
from galaxy_frog.domain.retrieval.pipeline import RetrievalWarning
from galaxy_frog.domain.vision.policy import VisualDecision, external_allowed
from galaxy_frog.domain.vision.reasoning_cache import (
    ReasoningFrameVerifier,
    VisualReasoningCache,
    reasoning_cache_key,
)
from galaxy_frog.domain.vision.visual import (
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
    ) -> None:
        self._provider, self._cache, self._verifier, self._external_enabled = (
            provider,
            cache,
            verifier,
            external_enabled,
        )

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
        started = perf_counter()
        provider = self._provider
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
            # Original evidence stays intact; only the provider copy is bounded.
            evidence = context[:3000]
            if len(context) > 3000:
                context_warning = (
                    RetrievalWarning(
                        "visual_context_limit",
                        "Only the first 3000 characters of retrieved text were sent to visual reasoning.",
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
            result = await self._cache.get(video_id, key)
            cache_hit = result is not None
            if result is None:
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
