"""Export the selected saved study document without querying a model."""

import re

from galaxy_frog.domain.study import (
    FlashcardItem,
    NoteItem,
    QuizItem,
    StudyCitation,
    StudyVersion,
)


def _escape(value: str) -> str:
    return re.sub(r"""([\\!"#$%&'()*+,\-./:;<=>?@\[\]^_`{|}~])""", r"\\\1", " ".join(value.split()))


def _interval(start: int, end: int, url: str | None) -> str:
    label = f"{start}-{end} ms"
    return f"[{label}]({url})" if url is not None else label


def _citation(item: StudyCitation) -> str:
    time = _interval(item.start_ms, item.end_ms, item.timestamp_url)
    kind = "On-screen text" if item.modality == "ocr" else f"Transcript ({item.origin})"
    refs = ", ".join(_escape(value) for value in (item.source_id, *item.unit_ids))
    return f"  - {kind} · {time} · seek {item.seek_ms} ms · {refs}: {_escape(item.quote)}"


def to_markdown(version: StudyVersion) -> str:
    doc = version.document
    lines = [
        f"# {_escape(doc.source.title)} — {version.kind.value.capitalize()} v{version.version}",
        "",
        f"Video: {_escape(doc.source.external_id)} ({doc.source.video_id})",
        f"Saved version: {version.artifact_id}",
    ]
    if version.chapter_version_id is not None:
        lines.append(f"Chapter version: {version.chapter_version_id}")
    if doc.warnings:
        lines.extend(("", "## Coverage"))
        lines.extend(f"- {_escape(warning)}" for warning in doc.warnings)
    for chapter in doc.chapters:
        time = _interval(chapter.start_ms, chapter.end_ms, chapter.timestamp_url)
        lines.extend(("", f"## {chapter.number}. {_escape(chapter.title)} · {time}", ""))
        lines.extend(_citation(item).lstrip() for item in chapter.citations)
    for section in doc.sections:
        time = _interval(section.start_ms, section.end_ms, section.timestamp_url)
        lines.extend(("", f"## {section.chapter_number}. {_escape(section.title)} · {time}", ""))
        if not section.items:
            lines.append("No supported items for this interval.")
        for item in section.items:
            match item:
                case NoteItem():
                    lines.append(f"- **{_escape(item.title)}:** {_escape(item.text)}")
                case FlashcardItem():
                    lines.append(f"- **{_escape(item.question)}** — {_escape(item.answer)}")
                case QuizItem():
                    lines.append(f"- **{_escape(item.question)}**")
                    for index, choice in enumerate(item.choices):
                        marker = " **(Correct)**" if index == item.correct_index else ""
                        lines.append(f"  - {_escape(choice)}{marker}")
                case _:
                    lines.append(f"- {_escape(item.text)}")
            lines.extend(_citation(citation) for citation in item.citations)
    return "\n".join(lines) + "\n"
