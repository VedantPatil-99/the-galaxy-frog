"""Unit tests for multimodal query routing, evidence modeling, fusion, and search service."""

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from galaxy_frog.application.retrieval.multimodal import (
    MultimodalSearchResult,
    MultimodalSearchService,
)
from galaxy_frog.domain.retrieval.errors import RetrievalIntegrityError
from galaxy_frog.domain.retrieval.models import RetrievedEvidence
from galaxy_frog.domain.retrieval.multimodal import (
    EvidenceModality,
    MultimodalEvidence,
    QueryModality,
    analyze_multimodal_query,
    fuse_multimodal_rankings,
)
from galaxy_frog.domain.retrieval.temporal import TimeWindow
from galaxy_frog.domain.transcripts.models import RetrievalUnit
from galaxy_frog.domain.vision.models import (
    OCRBlock,
    OCRProviderSpec,
    RetrievedOCREvidence,
)

_SPEC = OCRProviderSpec(provider="llamaparse", provider_revision="1.0.0")


def make_transcript_evidence(
    video_id: object,
    unit_id: str,
    start_ms: int,
    end_ms: int,
    text: str,
    score: float = 0.8,
) -> RetrievedEvidence:
    full_unit_id = (unit_id * 64)[:64]
    return RetrievedEvidence(
        video_id=video_id,  # pyright: ignore[reportArgumentType]
        unit=RetrievalUnit(
            unit_id=full_unit_id,
            start_ms=start_ms,
            end_ms=end_ms,
            text=text,
            cue_ids=("c" * 64,),
        ),
        score=score,
    )


def make_ocr_evidence(
    video_id: object,
    block_id: object,
    start_ms: int,
    end_ms: int,
    text: str,
    score: float = 0.85,
    sequence: int = 1,
) -> RetrievedOCREvidence:
    return RetrievedOCREvidence(
        video_id=video_id,  # pyright: ignore[reportArgumentType]
        block_id=block_id,  # pyright: ignore[reportArgumentType]
        frame_id=uuid4(),
        block=OCRBlock(
            text=text,
            start_ms=start_ms,
            end_ms=end_ms,
            source_frame_sequence=sequence,
            provider_spec=_SPEC,
        ),
        score=score,
        image_path=Path("C:/tmp/frame_001.jpg"),
    )


# ---------------------------------------------------------------------------
# Query Routing Tests
# ---------------------------------------------------------------------------


class TestQueryRouting:
    def test_empty_query_rejected(self) -> None:
        with pytest.raises(ValueError, match="query must not be empty"):
            analyze_multimodal_query("   ")

    def test_visual_intent_detected(self) -> None:
        analysis = analyze_multimodal_query("show the python code on the slide")
        assert analysis.modality == QueryModality.VISUAL
        assert "code" in analysis.visual_terms
        assert "slide" in analysis.visual_terms
        assert analysis.spoken_terms == ()

    def test_spoken_intent_detected(self) -> None:
        analysis = analyze_multimodal_query("what did the speaker say about rust")
        assert analysis.modality == QueryModality.SPOKEN
        assert "say" in analysis.spoken_terms
        assert analysis.visual_terms == ()

    def test_hybrid_intent_both_cues(self) -> None:
        analysis = analyze_multimodal_query("what did they say about the architecture diagram")
        assert analysis.modality == QueryModality.HYBRID
        assert "diagram" in analysis.visual_terms
        assert "say" in analysis.spoken_terms

    def test_hybrid_intent_general_query(self) -> None:
        analysis = analyze_multimodal_query("transformer attention mechanism")
        assert analysis.modality == QueryModality.HYBRID
        assert analysis.visual_terms == ()
        assert analysis.spoken_terms == ()

    def test_forced_modality_override(self) -> None:
        analysis = analyze_multimodal_query(
            "show the slide diagram",
            forced_modality=QueryModality.SPOKEN,
        )
        assert analysis.modality == QueryModality.SPOKEN


# ---------------------------------------------------------------------------
# MultimodalEvidence Model Tests
# ---------------------------------------------------------------------------


class TestMultimodalEvidence:
    def test_valid_transcript_evidence(self) -> None:
        vid = uuid4()
        t_hit = make_transcript_evidence(vid, "u1", 1000, 3000, "Spoken text")
        evidence = MultimodalEvidence(
            modality=EvidenceModality.TRANSCRIPT,
            video_id=vid,
            start_ms=1000,
            end_ms=3000,
            text="Spoken text",
            score=0.9,
            fusion_rank=1,
            temporal_bonus=0.05,
            transcript_evidence=t_hit,
        )
        assert evidence.modality == EvidenceModality.TRANSCRIPT
        assert evidence.transcript_evidence is t_hit
        assert evidence.ocr_evidence is None

    def test_valid_ocr_evidence(self) -> None:
        vid = uuid4()
        o_hit = make_ocr_evidence(vid, uuid4(), 2000, 5000, "Slide title")
        evidence = MultimodalEvidence(
            modality=EvidenceModality.OCR,
            video_id=vid,
            start_ms=2000,
            end_ms=5000,
            text="Slide title",
            score=0.85,
            fusion_rank=2,
            ocr_evidence=o_hit,
        )
        assert evidence.modality == EvidenceModality.OCR
        assert evidence.ocr_evidence is o_hit
        assert evidence.transcript_evidence is None

    def test_invalid_modality_type_rejected(self) -> None:
        vid = uuid4()
        with pytest.raises(ValueError, match="modality must be an EvidenceModality member"):
            MultimodalEvidence(
                modality="invalid",  # pyright: ignore[reportArgumentType]
                video_id=vid,
                start_ms=0,
                end_ms=1000,
                text="Text",
                score=0.5,
                fusion_rank=1,
            )

    def test_negative_start_ms_rejected(self) -> None:
        vid = uuid4()
        with pytest.raises(ValueError, match="start_ms must be non-negative"):
            MultimodalEvidence(
                modality=EvidenceModality.TRANSCRIPT,
                video_id=vid,
                start_ms=-1,
                end_ms=1000,
                text="Text",
                score=0.5,
                fusion_rank=1,
            )

    def test_inverted_interval_rejected(self) -> None:
        vid = uuid4()
        with pytest.raises(ValueError, match="end_ms must be greater than start_ms"):
            MultimodalEvidence(
                modality=EvidenceModality.TRANSCRIPT,
                video_id=vid,
                start_ms=1000,
                end_ms=1000,
                text="Text",
                score=0.5,
                fusion_rank=1,
            )

    @pytest.mark.parametrize("bad_score", [-0.1, 1.1, float("nan"), float("inf")])
    def test_invalid_score_rejected(self, bad_score: float) -> None:
        vid = uuid4()
        with pytest.raises(ValueError, match=r"score must be finite and between 0\.0 and 1\.0"):
            MultimodalEvidence(
                modality=EvidenceModality.TRANSCRIPT,
                video_id=vid,
                start_ms=0,
                end_ms=1000,
                text="Text",
                score=bad_score,
                fusion_rank=1,
            )

    def test_invalid_fusion_rank_rejected(self) -> None:
        vid = uuid4()
        with pytest.raises(ValueError, match="fusion_rank must be positive"):
            MultimodalEvidence(
                modality=EvidenceModality.TRANSCRIPT,
                video_id=vid,
                start_ms=0,
                end_ms=1000,
                text="Text",
                score=0.5,
                fusion_rank=0,
            )

    def test_invalid_temporal_bonus_rejected(self) -> None:
        vid = uuid4()
        with pytest.raises(ValueError, match="temporal_bonus must be non-negative"):
            MultimodalEvidence(
                modality=EvidenceModality.TRANSCRIPT,
                video_id=vid,
                start_ms=0,
                end_ms=1000,
                text="Text",
                score=0.5,
                fusion_rank=1,
                temporal_bonus=-0.1,
            )

    def test_transcript_modality_requires_transcript_evidence(self) -> None:
        vid = uuid4()
        with pytest.raises(ValueError, match="transcript modality requires transcript_evidence"):
            MultimodalEvidence(
                modality=EvidenceModality.TRANSCRIPT,
                video_id=vid,
                start_ms=0,
                end_ms=1000,
                text="Text",
                score=0.5,
                fusion_rank=1,
                transcript_evidence=None,
            )

    def test_transcript_modality_rejects_ocr_evidence(self) -> None:
        vid = uuid4()
        t_hit = make_transcript_evidence(vid, "u1", 0, 1000, "Text")
        o_hit = make_ocr_evidence(vid, uuid4(), 0, 1000, "Text")
        with pytest.raises(ValueError, match="no ocr_evidence"):
            MultimodalEvidence(
                modality=EvidenceModality.TRANSCRIPT,
                video_id=vid,
                start_ms=0,
                end_ms=1000,
                text="Text",
                score=0.5,
                fusion_rank=1,
                transcript_evidence=t_hit,
                ocr_evidence=o_hit,
            )

    def test_transcript_modality_video_id_mismatch(self) -> None:
        vid1 = uuid4()
        vid2 = uuid4()
        t_hit = make_transcript_evidence(vid2, "u1", 0, 1000, "Text")
        with pytest.raises(ValueError, match="transcript_evidence video_id does not match"):
            MultimodalEvidence(
                modality=EvidenceModality.TRANSCRIPT,
                video_id=vid1,
                start_ms=0,
                end_ms=1000,
                text="Text",
                score=0.5,
                fusion_rank=1,
                transcript_evidence=t_hit,
            )

    def test_transcript_modality_interval_mismatch(self) -> None:
        vid = uuid4()
        t_hit = make_transcript_evidence(vid, "u1", 0, 1000, "Text")
        with pytest.raises(ValueError, match="transcript_evidence intervals do not match"):
            MultimodalEvidence(
                modality=EvidenceModality.TRANSCRIPT,
                video_id=vid,
                start_ms=0,
                end_ms=2000,
                text="Text",
                score=0.5,
                fusion_rank=1,
                transcript_evidence=t_hit,
            )

    def test_transcript_modality_text_mismatch(self) -> None:
        vid = uuid4()
        t_hit = make_transcript_evidence(vid, "u1", 0, 1000, "Text A")
        with pytest.raises(ValueError, match="transcript_evidence text does not match"):
            MultimodalEvidence(
                modality=EvidenceModality.TRANSCRIPT,
                video_id=vid,
                start_ms=0,
                end_ms=1000,
                text="Text B",
                score=0.5,
                fusion_rank=1,
                transcript_evidence=t_hit,
            )

    def test_ocr_modality_requires_ocr_evidence(self) -> None:
        vid = uuid4()
        with pytest.raises(ValueError, match="ocr modality requires ocr_evidence"):
            MultimodalEvidence(
                modality=EvidenceModality.OCR,
                video_id=vid,
                start_ms=0,
                end_ms=1000,
                text="Text",
                score=0.5,
                fusion_rank=1,
                ocr_evidence=None,
            )

    def test_ocr_modality_rejects_transcript_evidence(self) -> None:
        vid = uuid4()
        t_hit = make_transcript_evidence(vid, "u1", 0, 1000, "Text")
        o_hit = make_ocr_evidence(vid, uuid4(), 0, 1000, "Text")
        with pytest.raises(ValueError, match="no transcript_evidence"):
            MultimodalEvidence(
                modality=EvidenceModality.OCR,
                video_id=vid,
                start_ms=0,
                end_ms=1000,
                text="Text",
                score=0.5,
                fusion_rank=1,
                ocr_evidence=o_hit,
                transcript_evidence=t_hit,
            )

    def test_ocr_modality_video_id_mismatch(self) -> None:
        vid1 = uuid4()
        vid2 = uuid4()
        o_hit = make_ocr_evidence(vid2, uuid4(), 0, 1000, "Text")
        with pytest.raises(ValueError, match="ocr_evidence video_id does not match"):
            MultimodalEvidence(
                modality=EvidenceModality.OCR,
                video_id=vid1,
                start_ms=0,
                end_ms=1000,
                text="Text",
                score=0.5,
                fusion_rank=1,
                ocr_evidence=o_hit,
            )

    def test_ocr_modality_interval_mismatch(self) -> None:
        vid = uuid4()
        o_hit = make_ocr_evidence(vid, uuid4(), 0, 1000, "Text")
        with pytest.raises(ValueError, match="ocr_evidence intervals do not match"):
            MultimodalEvidence(
                modality=EvidenceModality.OCR,
                video_id=vid,
                start_ms=0,
                end_ms=2000,
                text="Text",
                score=0.5,
                fusion_rank=1,
                ocr_evidence=o_hit,
            )

    def test_ocr_modality_text_mismatch(self) -> None:
        vid = uuid4()
        o_hit = make_ocr_evidence(vid, uuid4(), 0, 1000, "Text A")
        with pytest.raises(ValueError, match="ocr_evidence text does not match"):
            MultimodalEvidence(
                modality=EvidenceModality.OCR,
                video_id=vid,
                start_ms=0,
                end_ms=1000,
                text="Text B",
                score=0.5,
                fusion_rank=1,
                ocr_evidence=o_hit,
            )


# ---------------------------------------------------------------------------
# Multimodal RRF Fusion Tests
# ---------------------------------------------------------------------------


class TestFuseMultimodalRankings:
    def test_invalid_parameters_rejected(self) -> None:
        vid = uuid4()
        with pytest.raises(ValueError, match="RRF constant must be positive"):
            fuse_multimodal_rankings(vid, (), (), constant=0)
        with pytest.raises(ValueError, match="limit must be positive"):
            fuse_multimodal_rankings(vid, (), (), limit=0)
        with pytest.raises(ValueError, match="temporal_overlap_bonus must be non-negative"):
            fuse_multimodal_rankings(vid, (), (), temporal_overlap_bonus=-0.01)

    def test_foreign_transcript_candidate_rejected(self) -> None:
        vid = uuid4()
        foreign_vid = uuid4()
        t_hit = make_transcript_evidence(foreign_vid, "u1", 0, 1000, "Text")
        with pytest.raises(
            RetrievalIntegrityError, match="transcript evidence belongs to another video"
        ):
            fuse_multimodal_rankings(vid, (t_hit,), ())

    def test_foreign_ocr_candidate_rejected(self) -> None:
        vid = uuid4()
        foreign_vid = uuid4()
        o_hit = make_ocr_evidence(foreign_vid, uuid4(), 0, 1000, "Text")
        with pytest.raises(RetrievalIntegrityError, match="OCR evidence belongs to another video"):
            fuse_multimodal_rankings(vid, (), (o_hit,))

    def test_empty_candidates_returns_empty(self) -> None:
        vid = uuid4()
        assert fuse_multimodal_rankings(vid, (), ()) == ()

    def test_single_modality_fusion(self) -> None:
        vid = uuid4()
        t1 = make_transcript_evidence(vid, "u1", 0, 1000, "First spoken", score=0.9)
        t2 = make_transcript_evidence(vid, "u2", 1000, 2000, "Second spoken", score=0.7)
        results = fuse_multimodal_rankings(vid, (t1, t2), ())
        assert len(results) == 2
        assert results[0].fusion_rank == 1
        assert results[0].text == "First spoken"
        assert results[0].score == 1.0  # normalized max
        assert results[1].fusion_rank == 2
        assert results[1].text == "Second spoken"
        assert 0.0 < results[1].score < 1.0

    def test_temporal_overlap_bonus(self) -> None:
        vid = uuid4()
        # t1 (0-2000) overlaps with o1 (1000-3000)
        t1 = make_transcript_evidence(vid, "u1", 0, 2000, "Spoken topic")
        # t2 (5000-7000) does not overlap with any OCR
        t2 = make_transcript_evidence(vid, "u2", 5000, 7000, "Unrelated spoken")
        # o1 (1000-3000) overlaps with t1
        o1 = make_ocr_evidence(vid, uuid4(), 1000, 3000, "Visual topic")

        results = fuse_multimodal_rankings(
            vid,
            (t1, t2),
            (o1,),
            temporal_overlap_bonus=0.1,
        )
        assert len(results) == 3
        # t1 and o1 should both receive temporal bonus
        t1_res = next(r for r in results if r.text == "Spoken topic")
        o1_res = next(r for r in results if r.text == "Visual topic")
        t2_res = next(r for r in results if r.text == "Unrelated spoken")

        assert t1_res.temporal_bonus == 0.1
        assert o1_res.temporal_bonus == 0.1
        assert t2_res.temporal_bonus == 0.0

    def test_limit_respected(self) -> None:
        vid = uuid4()
        t_hits = [
            make_transcript_evidence(vid, f"u{i}", i * 1000, (i + 1) * 1000, f"Text {i}")
            for i in range(10)
        ]
        results = fuse_multimodal_rankings(vid, t_hits, (), limit=3)
        assert len(results) == 3


# ---------------------------------------------------------------------------
# MultimodalSearchService Tests
# ---------------------------------------------------------------------------


class FakeTranscriptRetriever:
    def __init__(self, hits: tuple[RetrievedEvidence, ...] = ()) -> None:
        self.hits = hits
        self.calls: list[tuple[UUID, str, int, TimeWindow | None]] = []
        self.fail = False

    async def search(
        self, video_id: UUID, query: str, *, limit: int, window: TimeWindow | None = None
    ) -> tuple[RetrievedEvidence, ...]:
        self.calls.append((video_id, query, limit, window))
        if self.fail:
            raise RuntimeError("Database connection lost")
        return self.hits


class FakeOCRRetriever:
    def __init__(self, hits: tuple[RetrievedOCREvidence, ...] = ()) -> None:
        self.hits = hits
        self.calls: list[tuple[UUID, str, int, TimeWindow | None]] = []
        self.fail = False

    async def search(
        self, video_id: UUID, query: str, *, limit: int, window: TimeWindow | None = None
    ) -> tuple[RetrievedOCREvidence, ...]:
        self.calls.append((video_id, query, limit, window))
        if self.fail:
            raise RuntimeError("OCR index table missing")
        return self.hits


class TestMultimodalSearchService:
    @pytest.mark.parametrize("bad_limit", [0, 31, -5])
    @pytest.mark.asyncio
    async def test_invalid_limit_rejected(self, bad_limit: int) -> None:
        service = MultimodalSearchService(
            transcript_retriever=FakeTranscriptRetriever(),
            ocr_retriever=FakeOCRRetriever(),
        )
        with pytest.raises(ValueError, match="result limit must be between 1 and 30"):
            await service.search(uuid4(), "query", limit=bad_limit)

    @pytest.mark.asyncio
    async def test_spoken_modality_queries_transcript_only(self) -> None:
        vid = uuid4()
        t_hit = make_transcript_evidence(vid, "u1", 0, 1000, "Spoken result")
        t_retriever = FakeTranscriptRetriever((t_hit,))
        o_retriever = FakeOCRRetriever()

        service = MultimodalSearchService(
            transcript_retriever=t_retriever,
            ocr_retriever=o_retriever,
        )
        result: MultimodalSearchResult = await service.search(
            vid,
            "what did he say about python",
        )

        assert result.routing.modality == QueryModality.SPOKEN
        assert len(t_retriever.calls) == 1
        assert len(o_retriever.calls) == 0
        assert len(result.evidence) == 1
        assert result.evidence[0].modality == EvidenceModality.TRANSCRIPT
        assert result.degraded is False
        assert result.warnings == ()

    @pytest.mark.asyncio
    async def test_spoken_modality_failure_raises(self) -> None:
        vid = uuid4()
        t_retriever = FakeTranscriptRetriever()
        t_retriever.fail = True
        service = MultimodalSearchService(
            transcript_retriever=t_retriever,
            ocr_retriever=FakeOCRRetriever(),
        )
        with pytest.raises(RuntimeError, match="Database connection lost"):
            await service.search(vid, "what did he say about rust")

    @pytest.mark.asyncio
    async def test_visual_modality_queries_ocr_only(self) -> None:
        vid = uuid4()
        o_hit = make_ocr_evidence(vid, uuid4(), 1000, 2000, "Code on slide")
        t_retriever = FakeTranscriptRetriever()
        o_retriever = FakeOCRRetriever((o_hit,))

        service = MultimodalSearchService(
            transcript_retriever=t_retriever,
            ocr_retriever=o_retriever,
        )
        result: MultimodalSearchResult = await service.search(
            vid,
            "show the code on the slide",
        )

        assert result.routing.modality == QueryModality.VISUAL
        assert len(t_retriever.calls) == 0
        assert len(o_retriever.calls) == 1
        assert len(result.evidence) == 1
        assert result.evidence[0].modality == EvidenceModality.OCR
        assert result.degraded is False

    @pytest.mark.asyncio
    async def test_visual_modality_failure_raises(self) -> None:
        vid = uuid4()
        o_retriever = FakeOCRRetriever()
        o_retriever.fail = True
        service = MultimodalSearchService(
            transcript_retriever=FakeTranscriptRetriever(),
            ocr_retriever=o_retriever,
        )
        with pytest.raises(RuntimeError, match="OCR index table missing"):
            await service.search(vid, "slide code")

    @pytest.mark.asyncio
    async def test_hybrid_modality_both_succeed(self) -> None:
        vid = uuid4()
        t_hit = make_transcript_evidence(vid, "u1", 0, 2000, "Spoken text")
        o_hit = make_ocr_evidence(vid, uuid4(), 1000, 3000, "Visual text")
        t_retriever = FakeTranscriptRetriever((t_hit,))
        o_retriever = FakeOCRRetriever((o_hit,))

        service = MultimodalSearchService(
            transcript_retriever=t_retriever,
            ocr_retriever=o_retriever,
        )
        result = await service.search(vid, "general search query", limit=5)

        assert result.routing.modality == QueryModality.HYBRID
        assert len(t_retriever.calls) == 1
        assert len(o_retriever.calls) == 1
        assert len(result.evidence) == 2
        assert result.degraded is False
        assert result.warnings == ()

    @pytest.mark.asyncio
    async def test_hybrid_transcript_failure_degrades_gracefully(self) -> None:
        vid = uuid4()
        o_hit = make_ocr_evidence(vid, uuid4(), 1000, 3000, "Visual text")
        t_retriever = FakeTranscriptRetriever()
        t_retriever.fail = True
        o_retriever = FakeOCRRetriever((o_hit,))

        service = MultimodalSearchService(
            transcript_retriever=t_retriever,
            ocr_retriever=o_retriever,
        )
        result = await service.search(vid, "general query")

        assert result.routing.modality == QueryModality.HYBRID
        assert result.degraded is True
        assert len(result.warnings) == 1
        assert "transcript_retrieval_failed" in result.warnings[0]
        assert len(result.evidence) == 1
        assert result.evidence[0].modality == EvidenceModality.OCR

    @pytest.mark.asyncio
    async def test_hybrid_ocr_failure_degrades_gracefully(self) -> None:
        vid = uuid4()
        t_hit = make_transcript_evidence(vid, "u1", 0, 2000, "Spoken text")
        t_retriever = FakeTranscriptRetriever((t_hit,))
        o_retriever = FakeOCRRetriever()
        o_retriever.fail = True

        service = MultimodalSearchService(
            transcript_retriever=t_retriever,
            ocr_retriever=o_retriever,
        )
        result = await service.search(vid, "general query")

        assert result.routing.modality == QueryModality.HYBRID
        assert result.degraded is True
        assert len(result.warnings) == 1
        assert "ocr_retrieval_failed" in result.warnings[0]
        assert len(result.evidence) == 1
        assert result.evidence[0].modality == EvidenceModality.TRANSCRIPT
