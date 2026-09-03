"""Dense retrieval, grounded generation, and deterministic citation validation."""

from uuid import UUID

from galaxy_frog.domain.generation.models import (
    AnswerConfidence,
    GeneratedAnswer,
    GenerationDraft,
    ValidatedCitation,
)
from galaxy_frog.domain.generation.ports import GenerationProvider
from galaxy_frog.domain.retrieval.models import RetrievedEvidence
from galaxy_frog.domain.retrieval.ports import TranscriptSearch

INSUFFICIENT_EVIDENCE = "I could not find sufficient evidence in this video."


class CitationValidationError(RuntimeError):
    """Raised when provider citations do not resolve to retrieved evidence."""


class AnswerQuestion:
    """Answer one question only after retrieving and validating transcript evidence."""

    def __init__(
        self,
        *,
        search: TranscriptSearch,
        provider: GenerationProvider,
        evidence_limit: int = 8,
    ) -> None:
        self._search = search
        self._provider = provider
        self._evidence_limit = evidence_limit

    async def execute(self, video_id: UUID, question: str) -> GeneratedAnswer:
        if not question.strip():
            raise ValueError("question must not be empty")
        evidence = await self._search.search(video_id, question, limit=self._evidence_limit)
        if not evidence:
            return self._insufficient()
        draft = await self._provider.generate(question, evidence)
        if not draft.citations:
            return self._insufficient()
        return self._validate(video_id, draft, evidence)

    @staticmethod
    def _validate(
        video_id: UUID,
        draft: GenerationDraft,
        evidence: tuple[RetrievedEvidence, ...],
    ) -> GeneratedAnswer:
        if any(item.video_id != video_id for item in evidence):
            raise CitationValidationError(
                "Retrieved transcript evidence did not belong to the requested video."
            )
        by_id = {item.unit.unit_id: item for item in evidence}
        citations: list[ValidatedCitation] = []
        seen: set[str] = set()
        for proposed in draft.citations:
            item = by_id.get(proposed.retrieval_unit_id)
            quote = proposed.quote.strip()
            source_text = item.unit.text if item is not None else ""
            if item is None or not quote or quote not in source_text:
                raise CitationValidationError(
                    "The generated answer contained an unsupported transcript citation."
                )
            if proposed.retrieval_unit_id in seen:
                continue
            seen.add(proposed.retrieval_unit_id)
            citations.append(
                ValidatedCitation(
                    video_id=item.video_id,
                    retrieval_unit_id=item.unit.unit_id,
                    cue_ids=item.unit.cue_ids,
                    quote=quote,
                    start_ms=item.unit.start_ms,
                    end_ms=item.unit.end_ms,
                )
            )
        if not citations:
            raise CitationValidationError("The generated answer did not contain usable citations.")
        return GeneratedAnswer(
            answer=draft.answer,
            confidence=draft.confidence,
            evidence=tuple(citations),
        )

    @staticmethod
    def _insufficient() -> GeneratedAnswer:
        return GeneratedAnswer(
            answer=INSUFFICIENT_EVIDENCE,
            confidence=AnswerConfidence.LOW,
            evidence=(),
            warnings=("No adequate transcript evidence was found.",),
        )
