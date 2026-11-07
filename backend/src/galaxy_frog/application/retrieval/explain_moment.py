"""Explain a bounded playhead window with source-validated evidence."""

import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, Protocol, cast
from uuid import UUID, uuid4

from galaxy_frog.application.retrieval.moment import (
    MomentEvidence,
    MomentFrame,
    RetrieveMomentEvidence,
)
from galaxy_frog.application.retrieval.reasoning import AdaptiveVisualReasoning, ReasoningExecution
from galaxy_frog.domain.generation.models import (
    GenerationDraft,
    GenerationProviderError,
    GroundingText,
)
from galaxy_frog.domain.retrieval.pipeline import RetrievalWarning
from galaxy_frog.domain.retrieval.traces import RetrievalTrace, RetrievalTraceRepository, TraceValue
from galaxy_frog.domain.transcripts.models import TranscriptOrigin
from galaxy_frog.domain.videos.models import CaptionKind
from galaxy_frog.domain.vision.models import OCRProviderSpec, PerceptualHash
from galaxy_frog.domain.vision.visual import VisualFrame

_VISUAL_REFERENCE = re.compile(
    r"\b(?:(?:this|that|the) (?:slide|diagram|chart|graph|figure|image|screen|table|code)"
    r"|on (?:the|your) screen|as you can see)\b",
    re.IGNORECASE,
)
_CONTEXT_LIMIT = 8
_TEXT_LIMIT = 350


class MomentTextProvider(Protocol):
    async def generate_moment(
        self, question: str, evidence: tuple[GroundingText, ...]
    ) -> GenerationDraft: ...


@dataclass(frozen=True, slots=True)
class MomentTranscriptLineage:
    origin: TranscriptOrigin
    track_id: str | None
    caption_kind: CaptionKind | None
    transcription_run_id: UUID | None


@dataclass(frozen=True, slots=True)
class MomentCitation:
    modality: Literal["transcript", "ocr", "frame"]
    source_id: str
    start_ms: int
    end_ms: int
    seek_ms: int
    quote: str | None = None
    unit_ids: tuple[str, ...] = ()
    frame_id: UUID | None = None
    transcript_lineage: MomentTranscriptLineage | None = None
    ocr_provider: OCRProviderSpec | None = None


@dataclass(frozen=True, slots=True)
class MomentExplanation:
    evidence: MomentEvidence
    explanation: str
    citations: tuple[MomentCitation, ...]
    related_moments: tuple[MomentFrame, ...]
    warnings: tuple[RetrievalWarning, ...]
    trace_id: UUID


def _context(evidence: MomentEvidence) -> tuple[MomentCitation, ...]:
    start = evidence.window.start_ms
    timestamp = evidence.timestamp_ms
    candidates = [
        MomentCitation(
            "transcript",
            item.cue.cue_id,
            item.cue.start_ms,
            item.cue.end_ms,
            max(start, item.cue.start_ms),
            item.cue.text[:_TEXT_LIMIT],
            item.unit_ids,
            transcript_lineage=MomentTranscriptLineage(
                item.cue.origin,
                item.cue.track_id,
                item.cue.caption_kind,
                item.cue.transcription_run_id,
            ),
        )
        for item in evidence.cues
    ] + [
        MomentCitation(
            "ocr",
            str(item.block_id),
            item.block.start_ms,
            item.block.end_ms,
            item.capture_ms,
            item.block.text[:_TEXT_LIMIT],
            frame_id=item.frame_id,
            ocr_provider=item.block.provider_spec,
        )
        for item in evidence.ocr
    ]

    def distance(citation: MomentCitation) -> int:
        if citation.modality == "ocr":
            return abs(citation.seek_ms - timestamp)
        return max(citation.start_ms - timestamp, timestamp - citation.end_ms + 1, 0)

    return tuple(sorted(candidates, key=distance)[:_CONTEXT_LIMIT])


def _validate_citations(
    draft: GenerationDraft, context: tuple[MomentCitation, ...]
) -> tuple[MomentCitation, ...]:
    by_id = {item.source_id: item for item in context}
    validated: dict[str, MomentCitation] = {}
    for proposed in draft.citations:
        source = by_id.get(proposed.retrieval_unit_id)
        quote = proposed.quote.strip()
        if source is not None and quote and quote in (source.quote or ""):
            validated.setdefault(source.source_id, replace(source, quote=quote))
    return tuple(validated.values())


def _frame_for_reasoning(video_id: UUID, frame: MomentFrame) -> VisualFrame | None:
    if frame.dhash is None or frame.content_sha256 is None:
        return None
    return VisualFrame(
        video_id=video_id,
        frame_id=frame.frame_id,
        scene_id=frame.scene_id,
        sequence=frame.sequence,
        start_ms=frame.start_ms,
        end_ms=frame.end_ms,
        perceptual_hash=PerceptualHash(frame.dhash, "dhash", 64),
        content_sha256=frame.content_sha256,
        source=frame.source,
        path=frame.path,
        capture_ms=frame.capture_ms,
    )


def _citation_lineage(citation: MomentCitation) -> dict[str, object]:
    metadata = asdict(citation)
    del metadata["quote"]
    return metadata


class ExplainMoment:
    def __init__(
        self,
        *,
        evidence: RetrieveMomentEvidence,
        text: MomentTextProvider,
        visual: AdaptiveVisualReasoning,
        traces: RetrievalTraceRepository,
    ) -> None:
        self._evidence = evidence
        self._text = text
        self._visual = visual
        self._traces = traces

    async def execute(
        self, video_id: UUID, timestamp_ms: int, *, external_permission: bool = False
    ) -> MomentExplanation:
        evidence = await self._evidence.execute(video_id, timestamp_ms)
        context = _context(evidence)
        speech = [
            item.cue.text
            for item in evidence.cues
            if item.cue.start_ms <= timestamp_ms < item.cue.end_ms
        ]
        needs_image = not speech or any(_VISUAL_REFERENCE.search(text) for text in speech)
        current = evidence.frames.current
        warnings: list[RetrievalWarning] = []
        reasoning: ReasoningExecution | None = None
        citations: tuple[MomentCitation, ...] = ()
        explanation = ""
        task = (
            f"Explain what happens at {timestamp_ms} ms in the examined window "
            f"[{evidence.window.start_ms}, {evidence.window.end_ms}) ms."
        )
        lineage = [_citation_lineage(item) for item in context]
        if needs_image:
            frame = _frame_for_reasoning(video_id, current) if current else None
            if frame is None or current is None:
                warnings.append(
                    RetrievalWarning(
                        "moment_frame_unavailable",
                        "No verified frame was available for image understanding. Nearby evidence is shown below.",
                    )
                )
            else:
                reasoning = await self._visual.reason_with_context(
                    video_id,
                    f"{task} Describe only clearly visible details in the sampled frame. "
                    "Use nearby transcript and OCR for context. Quote on-screen text only if legible in "
                    "the image. Leave out uncertain details about the setting or meaning.",
                    (frame,),
                    "\n".join(f"{item.modality}: {item.quote}" for item in context),
                    evidence_fingerprint=sha256(
                        json.dumps(lineage, default=str, sort_keys=True).encode()
                    ).hexdigest(),
                    decision_reason="moment_requires_image_understanding",
                    external_permission=external_permission,
                )
                warnings.extend(reasoning.warnings)
                visual_result = reasoning.result
                if visual_result is not None:
                    if visual_result.insufficient_evidence:
                        warnings.append(
                            RetrievalWarning(
                                "moment_visual_insufficient",
                                "The sampled frame did not provide enough evidence for an image explanation.",
                            )
                        )
                    else:
                        explanation = visual_result.answer
                        citations = (
                            MomentCitation(
                                "frame",
                                str(frame.frame_id),
                                frame.start_ms,
                                frame.end_ms,
                                current.capture_ms,
                                frame_id=frame.frame_id,
                            ),
                        )
        if not citations and context:
            text_context = tuple(
                GroundingText(
                    item.source_id, item.modality, item.start_ms, item.end_ms, item.quote or ""
                )
                for item in context
            )
            try:
                draft = await self._text.generate_moment(task, text_context)
                citations = _validate_citations(draft, context)
            except GenerationProviderError:
                warnings.append(
                    RetrievalWarning(
                        "moment_text_unavailable",
                        "The text model was unavailable. Source excerpts are shown below.",
                    )
                )
            else:
                if citations:
                    explanation = draft.answer
                else:
                    warnings.append(
                        RetrievalWarning(
                            "moment_citations_unusable",
                            "The text model did not return usable citations. Source excerpts are shown below.",
                        )
                    )
            if not citations:
                citations = context[:2]
                explanation = "The nearest available evidence is quoted below."
        if not explanation:
            explanation = "There is insufficient evidence to explain this moment."
        related = tuple(
            frame for frame in (evidence.frames.previous, evidence.frames.next) if frame
        )
        trace_id = uuid4()
        payload = cast(
            dict[str, TraceValue],
            json.loads(
                json.dumps(
                    {
                        "kind": "explain_moment",
                        "route": "image" if needs_image else "text",
                        "timestamp_ms": timestamp_ms,
                        "window": asdict(evidence.window),
                        "evidence_counts": {
                            "transcript": len(evidence.cues),
                            "ocr": len(evidence.ocr),
                        },
                        "text_context": lineage,
                        "citations": [_citation_lineage(item) for item in citations],
                        "frames": [
                            {
                                "frame_id": frame.frame_id,
                                "scene_id": frame.scene_id,
                                "capture_ms": frame.capture_ms,
                                "start_ms": frame.start_ms,
                                "end_ms": frame.end_ms,
                                "source": frame.source,
                            }
                            for frame in (evidence.frames.previous, current, evidence.frames.next)
                            if frame
                        ],
                        "external_permission_requested": external_permission,
                        "visual_reasoning": reasoning.trace_payload() if reasoning else None,
                        "warnings": [asdict(warning) for warning in warnings],
                    },
                    default=str,
                    allow_nan=False,
                )
            ),
        )
        await self._traces.save(RetrievalTrace(trace_id, video_id, datetime.now(UTC), payload))
        return MomentExplanation(
            evidence, explanation, citations, related, tuple(warnings), trace_id
        )
