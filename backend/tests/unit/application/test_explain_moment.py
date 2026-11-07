from dataclasses import replace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from tests.unit.vision.test_visual_contracts import frame, identity

from galaxy_frog.application.retrieval.explain_moment import ExplainMoment
from galaxy_frog.application.retrieval.moment import (
    MomentCue,
    MomentEvidenceReader,
    MomentFrame,
    MomentFrames,
    MomentOCR,
    RetrieveMomentEvidence,
)
from galaxy_frog.application.retrieval.reasoning import AdaptiveVisualReasoning
from galaxy_frog.domain.generation.models import (
    AnswerConfidence,
    GenerationCitation,
    GenerationDraft,
    GenerationProviderError,
)
from galaxy_frog.domain.transcripts.models import TranscriptCue
from galaxy_frog.domain.videos.models import (
    CaptionKind,
    SafeVideoMetadata,
    SourceReference,
    VideoSourceKind,
)
from galaxy_frog.domain.videos.records import VideoRecord
from galaxy_frog.domain.vision.models import OCRBlock, OCRProviderSpec
from galaxy_frog.domain.vision.visual import ExecutionLocation, VisualReasoningResult


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        "spoken",
        "visual",
        "disabled",
        "denied",
        "insufficient",
        "unhashed",
        "bad_citation",
        "text_down",
        "empty",
    ],
)
async def test_explain_moment_routes_validates_and_saves_trace(kind: str) -> None:
    source_ref = SourceReference(
        VideoSourceKind.YOUTUBE, "fixture", "https://www.youtube.com/watch?v=fixture"
    )
    video = VideoRecord(uuid4(), SafeVideoMetadata(source_ref, "Moment fixture", 60_000))
    reader = AsyncMock(spec=MomentEvidenceReader)
    reader.get_video.return_value = video
    reader.get_cues.return_value = ()
    reader.get_ocr.return_value = ()
    reader.get_frames.return_value = MomentFrames()
    image = frame()
    current = MomentFrame(
        image.frame_id,
        image.scene_id,
        1,
        20_000,
        30_000,
        22_500,
        image.source,
        image.path,
        image.perceptual_hash.hex_digest,
        image.content_sha256,
    )
    neighbor = replace(current, frame_id=uuid4(), sequence=2, capture_ms=24_000)
    if kind == "unhashed":
        current = replace(current, content_sha256=None)
    ocr = MomentOCR(
        uuid4(),
        current.frame_id,
        22_500,
        OCRBlock("Slide title", 20_000, 30_000, 1, OCRProviderSpec("fixture", "v1")),
    )
    if kind != "empty":
        reader.get_frames.return_value = MomentFrames(current=current, next=neighbor)
        reader.get_ocr.return_value = (ocr,)

    source = TranscriptCue(
        "a" * 64,
        video.metadata.reference,
        "en",
        0,
        21_000,
        23_000,
        "Look at this slide" if kind == "visual" else "Spoken evidence",
        track_id="manual:en",
        caption_kind=CaptionKind.MANUAL,
    )
    if kind in {"spoken", "visual", "bad_citation", "text_down"}:
        reader.get_cues.return_value = (MomentCue(source, ("b" * 64,)),)
    text, vision, cache, verifier, traces = (AsyncMock() for _ in range(5))
    vision.identity = (
        replace(identity(), location=ExecutionLocation.EXTERNAL) if kind == "denied" else identity()
    )
    vision.reason.return_value = VisualReasoningResult(
        "A slide with a large title",
        (current.frame_id,),
        vision.identity,
        10,
        kind == "insufficient",
    )
    cache.get.return_value = None
    proposed = GenerationCitation(
        source.cue_id, "invented quote" if kind == "bad_citation" else "Spoken evidence"
    )
    if kind in {"disabled", "denied", "unhashed", "insufficient"}:
        proposed = GenerationCitation(str(ocr.block_id), "Slide title")
    text.generate_moment.return_value = GenerationDraft(
        "Grounded text explanation",
        AnswerConfidence.HIGH,
        (GenerationCitation("outside-window", "invented"), proposed, proposed),
    )
    if kind == "text_down":
        text.generate_moment.side_effect = GenerationProviderError("unavailable")
    service = ExplainMoment(
        evidence=RetrieveMomentEvidence(cast(MomentEvidenceReader, reader)),
        text=text,
        visual=AdaptiveVisualReasoning(
            provider=None if kind == "disabled" else vision, cache=cache, verifier=verifier
        ),
        traces=traces,
    )

    result = await service.execute(video.video_id, 22_600)

    assert result.evidence.window.start_ms == 7600 and result.evidence.window.end_ms == 37_600
    trace = traces.save.call_args.args[0]
    assert trace.trace_id == result.trace_id and trace.video_id == video.video_id
    assert "Spoken evidence" not in str(trace.payload) and str(image.path) not in str(trace.payload)
    assert trace.payload["kind"] == "explain_moment"
    if kind != "empty":
        assert result.related_moments == (neighbor,)
    if kind == "visual":
        text.generate_moment.assert_not_awaited()
        vision.reason.assert_awaited_once()
        assert result.citations[0].source_id == str(current.frame_id)
        assert result.citations[0].seek_ms == 22_500
        assert result.explanation == "A slide with a large title"
    elif kind == "spoken":
        vision.reason.assert_not_awaited()
        assert len(result.citations) == 1 and result.citations[0].source_id == source.cue_id
        assert result.citations[0].unit_ids == ("b" * 64,) and result.citations[0].seek_ms == 21_000
    elif kind == "empty":
        text.generate_moment.assert_not_awaited()
        assert not result.citations and "insufficient" in result.explanation
    elif kind in {"bad_citation", "text_down"}:
        assert result.warnings and result.citations[0].quote == "Spoken evidence"
        assert result.explanation == "The nearest available evidence is quoted below."
    else:
        if kind != "insufficient":
            vision.reason.assert_not_awaited()
        assert result.warnings and result.citations[0].quote == "Slide title"
        assert result.citations[0].frame_id == current.frame_id
