"""Generate one evidence-validated, immutable study document."""

import json
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

from pydantic import BaseModel, Field, ValidationError

from galaxy_frog.domain.study import (
    Chapter,
    FlashcardItem,
    NoteItem,
    QuizItem,
    StudyCitation,
    StudyDocument,
    StudyError,
    StudyItem,
    StudyKind,
    StudySection,
    StudySource,
    StudyVersion,
    SummaryItem,
)
from galaxy_frog.domain.study_chapters import ChapterSpan, TimedVector, chapter_spans
from galaxy_frog.domain.videos.models import VideoSourceKind

_ITEM_LIMITS = {
    StudyKind.CHAPTERS: 1,
    StudyKind.SUMMARY: 3,
    StudyKind.NOTES: 5,
    StudyKind.FLASHCARDS: 2,
    StudyKind.QUIZ: 2,
}
Progress = Callable[[int, int, tuple[str, ...]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class StudyTimeline:
    source: StudySource
    evidence: tuple[StudyCitation, ...]
    vectors: tuple[TimedVector, ...]
    scenes: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class DraftBatch:
    items: tuple[object, ...]
    model: str
    aliases: dict[str, str] | None = None


class StudyGenerator(Protocol):
    async def draft(self, kind: StudyKind, evidence: tuple[StudyCitation, ...]) -> DraftBatch: ...


@dataclass(frozen=True, slots=True)
class GeneratedStudy:
    document: StudyDocument
    fingerprint: str
    provider: str
    model: str


class DraftCitation(BaseModel):
    source_id: str = Field(min_length=1, max_length=72)
    quote: str = Field(min_length=1, max_length=360)


class DraftItem(BaseModel):
    title: str = Field(default="", max_length=140)
    text: str = Field(default="", max_length=900)
    question: str = Field(default="", max_length=280)
    answer: str = Field(default="", max_length=280)
    choices: tuple[str, ...] = Field(default=(), max_length=4)
    correct_index: int | None = Field(default=None, strict=True, ge=0, le=3)
    citations: tuple[DraftCitation, ...] = Field(min_length=1, max_length=2)


def timestamp_url(source: StudySource, seek_ms: int) -> str | None:
    if source.kind == VideoSourceKind.YOUTUBE and re.fullmatch(
        r"[A-Za-z0-9_-]{11}", source.external_id
    ):
        return f"https://www.youtube.com/watch?v={source.external_id}&t={seek_ms // 1000}s"
    return None


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _batch(
    timeline: StudyTimeline, span: ChapterSpan, kind: StudyKind
) -> tuple[tuple[StudyCitation, ...], bool]:
    buckets: list[list[StudyCitation]] = [[] for _ in range(8)]
    seen_ocr: set[str] = set()
    sampled = False
    recall = kind in {StudyKind.FLASHCARDS, StudyKind.QUIZ}
    for item in timeline.evidence:
        if recall and item.quote.rstrip().endswith(("...", "\u2026")):
            continue
        if item.modality == "transcript":
            if item.start_ms >= span.end_ms or item.end_ms <= span.start_ms:
                continue
            seek = max(item.start_ms, span.start_ms)
        else:
            if item.capture_ms is None or not span.start_ms <= item.capture_ms < span.end_ms:
                continue
            if _normalize(item.quote) in seen_ocr:
                continue
            seen_ocr.add(_normalize(item.quote))
            seek = item.capture_ms
        excerpt = item.quote[:320]
        sampled |= len(excerpt) < len(item.quote)
        index = (seek - span.start_ms) * len(buckets) // (span.end_ms - span.start_ms)
        buckets[index].append(
            item.model_copy(
                update={
                    "quote": excerpt,
                    "seek_ms": seek,
                    "timestamp_url": timestamp_url(timeline.source, seek),
                }
            )
        )
    selected: list[StudyCitation] = []
    remaining = {"transcript": 64, "ocr": 12}
    characters = 0
    for index in range(max(map(len, buckets))):
        for bucket in buckets:
            if index >= len(bucket):
                continue
            item = bucket[index]
            if not remaining[item.modality] or characters + len(item.quote) > 1_400:
                sampled = True
                continue
            characters += len(item.quote)
            remaining[item.modality] -= 1
            selected.append(item)
    return tuple(sorted(selected, key=lambda item: (item.seek_ms, item.source_id))), sampled


def _validate(
    value: object, sources: dict[str, StudyCitation]
) -> tuple[DraftItem, tuple[StudyCitation, ...]] | None:
    try:
        draft = DraftItem.model_validate(value)
    except ValidationError:
        return None
    citations: list[StudyCitation] = []
    for citation in draft.citations:
        source = sources.get(citation.source_id)
        quote = citation.quote.strip()
        if source is None or not quote or quote not in source.quote:
            return None
        citations.append(source.model_copy(update={"quote": quote}))
    return draft, tuple(citations)


def _supported(answer: str, citations: tuple[StudyCitation, ...]) -> bool:
    return (
        bool(answer)
        and not answer.endswith(("...", "\u2026"))
        and any(answer in item.quote for item in citations)
    )


def _study_item(
    kind: StudyKind, draft: DraftItem, citations: tuple[StudyCitation, ...]
) -> StudyItem | None:
    title, text = draft.title.strip(), draft.text.strip()
    question, answer = draft.question.strip(), draft.answer.strip()
    if kind == StudyKind.SUMMARY and text:
        return SummaryItem(text=text, citations=citations)
    if kind == StudyKind.NOTES and title and text:
        return NoteItem(title=title, text=text, citations=citations)
    if kind == StudyKind.FLASHCARDS and question and _supported(answer, citations):
        return FlashcardItem(question=question, answer=answer, citations=citations)
    if kind == StudyKind.QUIZ and question and draft.correct_index is not None:
        choices = tuple(choice.strip() for choice in draft.choices)
        if (
            len(choices) == 4
            and all(0 < len(choice) <= 280 for choice in choices)
            and len({_normalize(choice) for choice in choices}) == 4
            and _supported(choices[draft.correct_index], citations)
        ):
            return QuizItem(
                question=question,
                choices=(choices[0], choices[1], choices[2], choices[3]),
                correct_index=draft.correct_index,
                citations=citations,
            )
    return None


def _study_spans(
    kind: StudyKind, timeline: StudyTimeline, version: StudyVersion | None, warnings: list[str]
) -> tuple[tuple[ChapterSpan, str], ...]:
    if kind != StudyKind.CHAPTERS:
        if version is None or not version.document.chapters:
            raise StudyError("STUDY_CHAPTERS_REQUIRED", "A saved chapter version is required.")
        return tuple(
            (ChapterSpan(ch.start_ms, ch.end_ms, ch.boundary_reasons), ch.title)
            for ch in version.document.chapters
        )
    cues = tuple(
        (item.start_ms, item.end_ms) for item in timeline.evidence if item.modality == "transcript"
    )
    if len(timeline.vectors) < 2:
        warnings.append("Stored transcript topic signals were unavailable.")
    if not cues:
        warnings.append("Transcript and cue-gap signals were unavailable.")
    if len(timeline.scenes) < 2:
        warnings.append("Scene change signals were unavailable.")
    return tuple(
        (span, f"Chapter {index}")
        for index, span in enumerate(
            chapter_spans(
                timeline.source.duration_ms,
                cues,
                timeline.vectors,
                timeline.scenes,
                has_ocr=any(item.modality == "ocr" for item in timeline.evidence),
            ),
            start=1,
        )
    )


async def generate_study(
    kind: StudyKind,
    timeline: StudyTimeline,
    pinned: StudyVersion | None,
    generator: StudyGenerator,
    progress: Progress,
) -> GeneratedStudy:
    if not timeline.evidence:
        raise StudyError(
            "STUDY_INSUFFICIENT_EVIDENCE",
            "This video has no usable transcript or on-screen text.",
            status=422,
        )
    warnings: list[str] = []
    spans = _study_spans(kind, timeline, pinned, warnings)
    fingerprint = sha256(timeline.source.model_dump_json().encode())
    if pinned is not None:
        fingerprint.update(pinned.artifact_id.bytes)
    await progress(0, len(spans), tuple(warnings))
    chapters: list[Chapter] = []
    sections: list[StudySection] = []
    actual_model: str | None = None
    for number, (span, title) in enumerate(spans, start=1):
        evidence, sampled = _batch(timeline, span, kind)
        if sampled:
            warnings.append(f"Chapter {number}: evidence was sampled across the interval.")
        fingerprint.update(json.dumps([span.start_ms, span.end_ms, span.reasons]).encode())
        for item in evidence:
            fingerprint.update(item.model_dump_json().encode())
        drafts: list[tuple[DraftItem, tuple[StudyCitation, ...]]] = []
        rejected = False
        if evidence:
            batch = await generator.draft(kind, evidence)
            if actual_model is not None and actual_model != batch.model:
                raise StudyError(
                    "STUDY_MODEL_CHANGED",
                    "The local model changed during generation.",
                    retryable=True,
                    action="Retry the material with a stable Ollama model.",
                )
            actual_model = batch.model
            sources = {item.source_id: item for item in evidence}
            if batch.aliases is not None:
                sources = {alias: sources[source_id] for alias, source_id in batch.aliases.items()}
            rejected = len(batch.items) > _ITEM_LIMITS[kind]
            for value in batch.items[: _ITEM_LIMITS[kind]]:
                checked = _validate(value, sources)
                if checked is None:
                    rejected = True
                else:
                    drafts.append(checked)
        link = timestamp_url(timeline.source, span.start_ms)
        if kind == StudyKind.CHAPTERS:
            citations: tuple[StudyCitation, ...] = ()
            if drafts and drafts[0][0].title.strip():
                draft, citations = drafts[0]
                title = draft.title.strip()
            else:
                warnings.append(f"Chapter {number}: no verified title; using a numbered label.")
            chapters.append(
                Chapter(
                    number=number,
                    start_ms=span.start_ms,
                    end_ms=span.end_ms,
                    title=title,
                    timestamp_url=link,
                    boundary_reasons=span.reasons,
                    citations=citations,
                )
            )
        else:
            items: list[StudyItem] = []
            for draft, citations in drafts:
                validated = _study_item(kind, draft, citations)
                if validated is None:
                    rejected = True
                else:
                    items.append(validated)
            if not items:
                warnings.append(f"Chapter {number}: no supported {kind.value} items.")
            sections.append(
                StudySection(
                    chapter_number=number,
                    title=title,
                    start_ms=span.start_ms,
                    end_ms=span.end_ms,
                    timestamp_url=link,
                    items=tuple(items),
                )
            )
        if rejected:
            warnings.append(
                f"Chapter {number}: unsupported or excess generated items were discarded."
            )
        await progress(number, len(spans), tuple(warnings))
    if not chapters and not any(section.items for section in sections):
        raise StudyError(
            "STUDY_NO_VALID_ITEMS",
            "No generated items passed evidence validation.",
            status=422,
            retryable=True,
            action="Inspect the video evidence and retry generation.",
        )
    return GeneratedStudy(
        StudyDocument(
            source=timeline.source,
            chapter_version=pinned.version if pinned is not None else None,
            chapters=tuple(chapters),
            sections=tuple(sections),
            warnings=tuple(warnings),
        ),
        fingerprint.hexdigest(),
        "ollama" if actual_model is not None else "none",
        actual_model or "none",
    )
