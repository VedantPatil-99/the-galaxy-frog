"""Chapter boundaries, evidence rejection, and stable study exports."""

import itertools
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest

from galaxy_frog.application.study_export import to_markdown
from galaxy_frog.application.study_generation import DraftBatch, StudyTimeline, generate_study
from galaxy_frog.domain.study import (
    FlashcardItem,
    QuizItem,
    StudyCitation,
    StudyDocument,
    StudyError,
    StudyKind,
    StudySource,
    StudyVersion,
)
from galaxy_frog.domain.study_chapters import TimedVector, chapter_spans
from galaxy_frog.domain.transcripts.models import TranscriptOrigin
from galaxy_frog.domain.videos.models import CaptionKind, VideoSourceKind


class ScriptedGenerator:
    def __init__(self, *batches: DraftBatch) -> None:
        self.batches = iter(batches)
        self.calls: list[tuple[StudyKind, tuple[StudyCitation, ...]]] = []

    async def draft(self, kind: StudyKind, evidence: tuple[StudyCitation, ...]) -> DraftBatch:
        self.calls.append((kind, evidence))
        return next(self.batches)


async def progress(_done: int, _total: int, _warnings: tuple[str, ...]) -> None:
    pass


def source(duration: int = 100_000) -> StudySource:
    return StudySource(
        video_id=uuid4(),
        kind=VideoSourceKind.YOUTUBE,
        external_id="abcdefghijk",
        canonical_url="https://www.youtube.com/watch?v=abcdefghijk",
        title="ML [class] <tag>",
        duration_ms=duration,
    )


def cue(start: int, end: int, text: str = "The answer is gradient descent.") -> StudyCitation:
    return StudyCitation(
        source_id=f"cue:{start}",
        modality="transcript",
        start_ms=start,
        end_ms=end,
        seek_ms=start,
        timestamp_url=None,
        quote=text,
        cue_id=str(start),
        unit_ids=("unit-a",),
        origin=TranscriptOrigin.CAPTION,
        track_id="manual:en",
        caption_kind=CaptionKind.MANUAL,
    )


def ocr(start: int, capture: int | None, text: str) -> StudyCitation:
    return StudyCitation(
        source_id=f"ocr:{capture}",
        modality="ocr",
        start_ms=start,
        end_ms=start + 60_000,
        seek_ms=capture or 0,
        timestamp_url=None,
        quote=text,
        capture_ms=capture,
        ocr_block_id=uuid4(),
        frame_id=uuid4(),
    )


def version(
    kind: StudyKind, doc: StudyDocument, pinned: StudyVersion | None = None
) -> StudyVersion:
    return StudyVersion(
        artifact_id=uuid4(),
        kind=kind,
        version=1,
        chapter_version_id=pinned.artifact_id if pinned else None,
        created_at=datetime.now(UTC),
        schema_version=1,
        prompt_version="study-v1",
        evidence_fingerprint="a" * 64,
        provider="ollama",
        model="fixture",
        document=doc,
    )


def draft(source_id: str, quote: str, **fields: object) -> dict[str, object]:
    return {**fields, "citations": [{"source_id": source_id, "quote": quote}]}


def test_chapters_use_topic_gaps_and_scenes_without_splitting_on_rapid_cuts() -> None:
    continuous = ((0, 20_000), (19_000, 48_000), (45_000, 70_000), (66_000, 120_000))
    vectors = (TimedVector(0, (1.0, 0.0)), TimedVector(52_000, (0.0, 1.0)))
    scenes = ((0, 50_000), (50_000, 90_000), (90_000, 120_000))
    spans = chapter_spans(120_000, continuous, vectors, scenes, has_ocr=False)
    assert [(span.start_ms, span.end_ms) for span in spans] == [(0, 52_000), (52_000, 120_000)]
    assert spans[1].reasons == ("topic_change", "scene_change")
    assert len(chapter_spans(120_000, continuous, (), scenes, has_ocr=True)) == 1
    gap = chapter_spans(120_000, ((0, 45_000), (50_000, 120_000)), (), scenes, has_ocr=False)
    assert gap[1].reasons == ("transcript_gap", "scene_change")
    assert chapter_spans(120_000, (), (), scenes, has_ocr=True)[1].reasons == ("scene_change",)


def test_chapters_keep_short_and_long_timelines_contiguous() -> None:
    assert chapter_spans(10_000, (), (), (), has_ocr=False)[0].end_ms == 10_000
    zero = (TimedVector(0, (0.0,)), TimedVector(54_000, (1.0,)))
    assert len(chapter_spans(100_000, (), zero, (), has_ocr=False)) == 1
    gaps = tuple((start, start + 1_000) for start in range(0, 1_200_000, 50_000))
    spans = chapter_spans(1_400_000, gaps, (), (), has_ocr=False)
    assert len(spans) == 20
    assert spans[0].start_ms == 0 and spans[-1].end_ms == 1_400_000
    assert all(left.end_ms == right.start_ms for left, right in itertools.pairwise(spans))
    assert chapter_spans(800_000, (), (), (), has_ocr=False)[1].reasons == ("long_span",)


@pytest.mark.asyncio
async def test_titles_and_sampled_batches_preserve_exact_ocr_and_transcript_seek() -> None:
    first = cue(0, 50_000)
    second = cue(55_000, 80_000).model_copy(
        update={
            "origin": TranscriptOrigin.ASR,
            "transcription_run_id": uuid4(),
            "track_id": None,
        }
    )
    valid = ocr(40_000, 55_000, "Backpropagation")
    duplicate = ocr(40_000, 59_000, " BACKPROPAGATION ")
    unknown = ocr(0, None, "Unknown capture")
    provider = ScriptedGenerator(
        DraftBatch((draft(first.source_id, "gradient descent", title="Optimization"),), "fixture"),
        DraftBatch((draft(valid.source_id, "Backpropagation", title="Neural nets"),), "fixture"),
    )
    timeline = StudyTimeline(source(110_000), (first, second, valid, duplicate, unknown), (), ())
    result = await generate_study(StudyKind.CHAPTERS, timeline, None, provider, progress)
    a, b = result.document.chapters
    assert a.start_ms == 0 and a.citations[0].seek_ms == 0
    assert b.start_ms == 55_000 and b.citations[0].start_ms == 40_000
    assert b.citations[0].seek_ms == 55_000 and b.citations[0].frame_id == valid.frame_id
    assert {item.source_id for item in provider.calls[1][1]} == {valid.source_id, second.source_id}
    assert provider.calls[1][1][0].transcription_run_id == second.transcription_run_id
    assert b.timestamp_url == "https://www.youtube.com/watch?v=abcdefghijk&t=55s"
    assert "Optimization" in to_markdown(version(StudyKind.CHAPTERS, result.document))


@pytest.mark.asyncio
async def test_missing_evidence_and_invalid_titles_have_explicit_outcomes() -> None:
    video = source().model_copy(update={"kind": VideoSourceKind.LOCAL_FILE})
    with pytest.raises(StudyError, match="no usable"):
        await generate_study(
            StudyKind.CHAPTERS,
            StudyTimeline(video, (), (), ()),
            None,
            ScriptedGenerator(),
            progress,
        )
    timeline = StudyTimeline(video, (cue(0, 1000),), (), ((0, 50_000), (50_000, 100_000)))
    provider = ScriptedGenerator(
        DraftBatch((draft("invented", "bad", title="Made up"), None), "fixture")
    )
    result = await generate_study(StudyKind.CHAPTERS, timeline, None, provider, progress)
    assert result.document.chapters[0].title == "Chapter 1"
    assert result.document.chapters[0].timestamp_url is None
    assert any("discarded" in warning for warning in result.document.warnings)
    with pytest.raises(StudyError, match="saved chapter"):
        await generate_study(StudyKind.QUIZ, timeline, None, ScriptedGenerator(), progress)


@pytest.mark.asyncio
async def test_sampling_covers_late_evidence_and_flags_omission() -> None:
    evidence = tuple(cue(start, start + 500) for start in range(0, 80_000, 500))
    gen = ScriptedGenerator(DraftBatch((), "fixture"))
    result = await generate_study(
        StudyKind.CHAPTERS, StudyTimeline(source(80_000), evidence, (), ()), None, gen, progress
    )
    supplied = gen.calls[0][1]
    assert 0 < len(supplied) < 64
    assert sum(len(item.quote) for item in supplied) <= 1_400
    assert supplied[-1].start_ms >= 70_000
    assert any("sampled" in warning for warning in result.document.warnings)


@pytest.mark.parametrize(
    ("kind", "fields", "rendered"),
    [
        (StudyKind.SUMMARY, {"text": "Gradient descent reduces error."}, "Gradient descent"),
        (
            StudyKind.NOTES,
            {"title": "Optimization", "text": "Use gradient descent."},
            "Optimization",
        ),
        (
            StudyKind.FLASHCARDS,
            {"question": "Which method?", "answer": "gradient descent"},
            "Which method",
        ),
        (
            StudyKind.QUIZ,
            {
                "question": "Which method?",
                "choices": ["gradient descent", "A", "B", "C"],
                "correct_index": 0,
            },
            "Correct",
        ),
    ],
)
@pytest.mark.asyncio
async def test_all_materials_reject_false_quotes_and_export_the_pinned_version(
    kind: StudyKind,
    fields: dict[str, object],
    rendered: str,
) -> None:
    evidence = cue(0, 100_000)
    timeline = StudyTimeline(source(), (evidence,), (), ())
    chapters = await generate_study(
        StudyKind.CHAPTERS, timeline, None, ScriptedGenerator(DraftBatch((), "fixture")), progress
    )
    pinned = version(StudyKind.CHAPTERS, chapters.document)
    batch = DraftBatch(
        (
            draft(evidence.source_id, "Invented quote", **fields),
            draft(evidence.source_id, "gradient descent", **fields),
        ),
        "fixture",
    )
    result = await generate_study(kind, timeline, pinned, ScriptedGenerator(batch), progress)
    saved = version(kind, result.document, pinned)
    items = saved.document.sections[0].items
    assert len(items) == 1 and items[0].citations[0].quote == "gradient descent"
    assert rendered in to_markdown(saved)
    assert str(pinned.artifact_id) in to_markdown(saved)
    assert "t=0s" in to_markdown(saved)
    assert "<tag>" not in to_markdown(saved)
    if kind == StudyKind.QUIZ:
        assert cast(QuizItem, items[0]).correct_index == 0
    if kind == StudyKind.FLASHCARDS:
        assert cast(FlashcardItem, items[0]).answer == "gradient descent"


@pytest.mark.asyncio
async def test_wrong_choice_and_invented_answer_fail_when_nothing_is_supported() -> None:
    evidence = cue(0, 100_000)
    timeline = StudyTimeline(source(), (evidence,), (), ())
    result = await generate_study(
        StudyKind.CHAPTERS, timeline, None, ScriptedGenerator(DraftBatch((), "fixture")), progress
    )
    pinned = version(StudyKind.CHAPTERS, result.document)
    for kind, fields in (
        (StudyKind.FLASHCARDS, {"question": "Which method?", "answer": "Made up"}),
        (
            StudyKind.QUIZ,
            {
                "question": "Which?",
                "choices": ["gradient descent", "a", "A", "b"],
                "correct_index": 0,
            },
        ),
    ):
        with pytest.raises(StudyError, match="No generated items"):
            await generate_study(
                kind,
                timeline,
                pinned,
                ScriptedGenerator(
                    DraftBatch((draft(evidence.source_id, evidence.quote, **fields),), "fixture")
                ),
                progress,
            )


@pytest.mark.asyncio
async def test_aliases_recover_original_ids_and_empty_chapters_remain_visible() -> None:
    item = cue(0, 15_000, "Gradient descent." + (" extra" * 75))
    timeline = StudyTimeline(
        source(700_000),
        (item,),
        (TimedVector(0, (1.0,)), TimedVector(2_000, (1.0,))),
        (),
    )
    chapters = await generate_study(
        StudyKind.CHAPTERS,
        timeline,
        None,
        ScriptedGenerator(
            DraftBatch(
                (draft("E1", "Gradient descent.", title="Optimization"),),
                "fixture",
                {"E1": item.source_id},
            )
        ),
        progress,
    )
    assert len(chapters.document.chapters) == 2
    assert chapters.document.chapters[0].citations[0].source_id == item.source_id
    assert chapters.document.chapters[1].title == "Chapter 2"
    pinned = version(StudyKind.CHAPTERS, chapters.document)
    result = await generate_study(
        StudyKind.SUMMARY,
        timeline,
        pinned,
        ScriptedGenerator(
            DraftBatch(
                (None, draft(item.source_id, "Gradient descent.", text="Optimization")), "fixture"
            )
        ),
        progress,
    )
    assert not result.document.sections[1].items
    assert "No supported items for this interval" in to_markdown(
        version(StudyKind.SUMMARY, result.document, pinned)
    )
    empty = pinned.model_copy(update={"document": StudyDocument(source=timeline.source)})
    with pytest.raises(StudyError, match="saved chapter"):
        await generate_study(StudyKind.SUMMARY, timeline, empty, ScriptedGenerator(), progress)


@pytest.mark.asyncio
async def test_run_refuses_to_mix_models_across_chapters() -> None:
    timeline = StudyTimeline(source(120_000), (cue(0, 40_000), cue(55_000, 95_000)), (), ())
    provider = ScriptedGenerator(DraftBatch((), "old"), DraftBatch((), "new"))
    with pytest.raises(StudyError, match="model changed"):
        await generate_study(StudyKind.CHAPTERS, timeline, None, provider, progress)


@pytest.mark.asyncio
async def test_ocr_only_chapter_labels_missing_transcript_and_preserves_capture() -> None:
    item = ocr(0, 5_231, "Mitochondria release energy.")
    timeline = StudyTimeline(source(), (item,), (), ((0, 100_000),))
    result = await generate_study(
        StudyKind.CHAPTERS,
        timeline,
        None,
        ScriptedGenerator(
            DraftBatch((draft(item.source_id, "Mitochondria", title="Cell energy"),), "fixture")
        ),
        progress,
    )
    chapter = result.document.chapters[0]
    assert chapter.citations[0].seek_ms == 5_231
    assert any("Transcript and cue-gap" in warning for warning in result.document.warnings)


@pytest.mark.asyncio
async def test_recall_omits_interrupted_cues_and_rejects_incomplete_answers() -> None:
    first = cue(0, 1000, "Incomplete thought...")
    second = cue(1100, 3000, "Elephants have long trunks.")
    timeline = StudyTimeline(source(), (first, second), (), ())
    chapters = await generate_study(
        StudyKind.CHAPTERS, timeline, None, ScriptedGenerator(DraftBatch((), "fixture")), progress
    )
    pinned = version(StudyKind.CHAPTERS, chapters.document)
    provider = ScriptedGenerator(
        DraftBatch(
            (
                draft(
                    second.source_id,
                    "long trunks",
                    question="What do elephants have?",
                    answer="long trunks...",
                ),
            ),
            "fixture",
        )
    )
    with pytest.raises(StudyError, match="No generated items"):
        await generate_study(StudyKind.FLASHCARDS, timeline, pinned, provider, progress)
    assert [item.source_id for item in provider.calls[0][1]] == [second.source_id]
