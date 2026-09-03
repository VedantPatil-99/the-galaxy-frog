"""Tests for retrieval-backed answers and citation validation."""

# pyright: reportPrivateUsage=false

from uuid import UUID, uuid4

import pytest

from galaxy_frog.application.questions.answer_question import (
    INSUFFICIENT_EVIDENCE,
    AnswerQuestion,
    CitationValidationError,
)
from galaxy_frog.domain.generation import (
    AnswerConfidence,
    GenerationCitation,
    GenerationDraft,
)
from galaxy_frog.domain.retrieval import RetrievedEvidence
from galaxy_frog.domain.transcripts import RetrievalUnit


class FakeSearch:
    def __init__(self, evidence: tuple[RetrievedEvidence, ...]) -> None:
        self.evidence = evidence

    async def ensure_indexed(self, video_id: UUID) -> UUID:
        return video_id

    async def search(
        self,
        video_id: UUID,
        query: str,
        *,
        limit: int,
    ) -> tuple[RetrievedEvidence, ...]:
        del video_id, query
        assert limit == 8
        return self.evidence


class FakeGenerator:
    def __init__(self, draft: GenerationDraft) -> None:
        self.draft = draft

    async def generate(
        self,
        question: str,
        evidence: tuple[RetrievedEvidence, ...],
    ) -> GenerationDraft:
        assert question
        assert evidence
        return self.draft


def evidence() -> RetrievedEvidence:
    return RetrievedEvidence(
        video_id=uuid4(),
        unit=RetrievalUnit(
            unit_id="a" * 64,
            start_ms=1000,
            end_ms=2000,
            text="The speaker explains temporal provenance.",
            cue_ids=("b" * 64,),
        ),
        score=0.9,
    )


@pytest.mark.asyncio
async def test_returns_validated_timestamped_evidence() -> None:
    item = evidence()
    service = AnswerQuestion(
        search=FakeSearch((item,)),
        provider=FakeGenerator(
            GenerationDraft(
                answer="It preserves provenance.",
                confidence=AnswerConfidence.HIGH,
                citations=(
                    GenerationCitation(item.unit.unit_id, "temporal provenance"),
                    GenerationCitation(item.unit.unit_id, "temporal provenance"),
                ),
            )
        ),
    )

    answer = await service.execute(item.video_id, "What does it preserve?")

    assert answer.answer == "It preserves provenance."
    assert len(answer.evidence) == 1
    assert answer.evidence[0].start_ms == 1000
    assert answer.evidence[0].cue_ids == item.unit.cue_ids


@pytest.mark.asyncio
async def test_returns_insufficient_evidence_without_generation() -> None:
    draft = GenerationDraft("unused", AnswerConfidence.LOW, ())
    answer = await AnswerQuestion(search=FakeSearch(()), provider=FakeGenerator(draft)).execute(
        uuid4(), "Question"
    )

    assert answer.answer == INSUFFICIENT_EVIDENCE
    assert not answer.evidence
    assert answer.warnings


@pytest.mark.asyncio
async def test_empty_provider_citations_return_insufficient_evidence() -> None:
    item = evidence()
    answer = await AnswerQuestion(
        search=FakeSearch((item,)),
        provider=FakeGenerator(GenerationDraft("Unknown.", AnswerConfidence.LOW, ())),
    ).execute(item.video_id, "Question")

    assert answer.answer == INSUFFICIENT_EVIDENCE


@pytest.mark.parametrize(
    "citation",
    [
        GenerationCitation("c" * 64, "temporal provenance"),
        GenerationCitation("a" * 64, "unsupported quote"),
        GenerationCitation("a" * 64, "  "),
    ],
)
@pytest.mark.asyncio
async def test_rejects_unsupported_citations(citation: GenerationCitation) -> None:
    item = evidence()
    service = AnswerQuestion(
        search=FakeSearch((item,)),
        provider=FakeGenerator(GenerationDraft("Draft", AnswerConfidence.MEDIUM, (citation,))),
    )

    with pytest.raises(CitationValidationError):
        await service.execute(item.video_id, "Question")


@pytest.mark.asyncio
async def test_rejects_empty_questions() -> None:
    with pytest.raises(ValueError):
        await AnswerQuestion(
            search=FakeSearch(()),
            provider=FakeGenerator(GenerationDraft("x", AnswerConfidence.LOW, ())),
        ).execute(uuid4(), " ")


@pytest.mark.asyncio
async def test_rejects_evidence_owned_by_another_video() -> None:
    item = evidence()
    service = AnswerQuestion(
        search=FakeSearch((item,)),
        provider=FakeGenerator(
            GenerationDraft(
                "Draft",
                AnswerConfidence.MEDIUM,
                (GenerationCitation(item.unit.unit_id, "temporal provenance"),),
            )
        ),
    )

    with pytest.raises(CitationValidationError, match="requested video"):
        await service.execute(uuid4(), "Question")


def test_validator_rejects_a_draft_without_usable_citations() -> None:
    item = evidence()
    with pytest.raises(CitationValidationError, match="usable citations"):
        AnswerQuestion._validate(
            item.video_id,
            GenerationDraft("Draft", AnswerConfidence.LOW, ()),
            (item,),
        )
