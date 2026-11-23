import runpy
import sys
from collections.abc import Sequence
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
from zipfile import ZipFile

import pytest
from anyio import Path as AsyncPath
from docx import Document
from pypdf import PdfWriter
from pypdf.generic import ContentStream, DictionaryObject, NameObject

from galaxy_frog.adapters import workspace_document_parser as parser
from galaxy_frog.adapters import workspace_documents as documents
from galaxy_frog.adapters.media.process import CommandResult, CommandRunner, CommandTimedOut
from galaxy_frog.adapters.workspace_document_parser import extract_document, render_pdf_page
from galaxy_frog.adapters.workspace_documents import (
    DocumentProcessingTimeout,
    DocumentProcessingUnavailable,
    WorkspaceDocumentProcessor,
    document_original,
    store_document,
)
from galaxy_frog.domain.workspace_documents import (
    DocumentRejected,
    DocumentStorageUnavailable,
    document_filename,
)


def sample_pdf() -> bytes:
    writer = PdfWriter()
    first = writer.add_blank_page(width=220, height=300)
    first[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject("/F1"): DictionaryObject(
                        {
                            NameObject("/Type"): NameObject("/Font"),
                            NameObject("/Subtype"): NameObject("/Type1"),
                            NameObject("/BaseFont"): NameObject("/Helvetica"),
                        }
                    )
                }
            )
        }
    )
    stream = ContentStream(None, writer)
    stream.set_data(b"BT /F1 12 Tf 18 270 Td (Water flows downhill.) Tj ET")
    first.replace_contents(stream)
    writer.add_blank_page(width=220, height=300)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_pdf_extraction_and_preview_keep_page_boundaries(tmp_path: Path) -> None:
    original = tmp_path / "notes.pdf"
    original.write_bytes(sample_pdf())
    result = extract_document(original, "pdf")
    assert len(result.sections) == 2
    assert "Water flows downhill." in result.sections[0].text
    assert result.sections[0].spans
    assert not result.sections[1].text and not result.sections[1].spans
    output = tmp_path / "page.png"
    render_pdf_page(original, 1, output)
    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(DocumentRejected, match="unavailable"):
        render_pdf_page(original, 3, output)


def test_docx_extracts_body_in_document_order_without_pages(tmp_path: Path) -> None:
    original = tmp_path / "notes.docx"
    doc = Document()
    doc.add_paragraph("Field notes")
    doc.add_paragraph()
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Sample"
    table.cell(0, 1).text = "River"
    doc.add_paragraph("Collected in November.")
    doc.save(str(original))
    result = extract_document(original, "docx")
    assert len(result.sections) == 1
    assert result.sections[0].text == "Field notes\n\nSample\tRiver\n\nCollected in November."
    assert len(result.sections[0].spans) == 3


def test_docx_archive_entry_and_size_checks_run_before_parsing(tmp_path: Path) -> None:
    original = tmp_path / "too-many-entries.docx"
    with ZipFile(original, "w") as archive:
        for index in range(257):
            archive.writestr(f"part-{index}.xml", "contents")
    with pytest.raises(DocumentRejected, match="archive exceeds"):
        extract_document(original, "docx")


@pytest.mark.parametrize("filename", ["../notes.pdf", "notes.exe", "nested\\notes.docx"])
def test_document_formats_come_from_a_safe_filename(filename: str) -> None:
    with pytest.raises(DocumentRejected):
        document_filename(filename)


def test_utf8_text_and_markdown_preserve_original_text_and_spans(tmp_path: Path) -> None:
    original = tmp_path / "field notes.md"
    original.write_bytes(b"\xef\xbb\xbf# Glacier\r\n\r\nCold source")
    assert document_filename("field notes.MD") == ("field notes.MD", "markdown")
    result = extract_document(original, "markdown")
    assert result.sections[0].text == "# Glacier\n\nCold source"
    assert result.sections[0].spans == ((0, 9), (11, 22))
    original.write_bytes(b"\xff")
    with pytest.raises(DocumentRejected, match="UTF-8"):
        extract_document(original, "plain")


def test_pdf_must_have_supported_signature(tmp_path: Path) -> None:
    original = tmp_path / "bad.pdf"
    original.write_text("Not a PDF", encoding="utf-8")
    with pytest.raises(DocumentRejected, match="do not match"):
        extract_document(original, "pdf")


def test_password_protected_pdf_is_rejected(tmp_path: Path) -> None:
    original = tmp_path / "locked.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=220, height=300)
    writer.encrypt("fixture")
    writer.write(original)
    with pytest.raises(DocumentRejected, match="password protection"):
        extract_document(original, "pdf")


@pytest.mark.asyncio
async def test_document_parser_deadline_returns_a_controlled_failure() -> None:
    runner = AsyncMock(spec=CommandRunner)
    runner.run.side_effect = CommandTimedOut("document parser")
    with pytest.raises(DocumentProcessingTimeout):
        await WorkspaceDocumentProcessor(runner).extract(b"Notes", "plain")
    assert runner.run.call_args.kwargs["timeout_seconds"] == 20


@pytest.mark.parametrize(
    ("limit", "message"),
    [
        ("DOCUMENT_MAX_PAGES", "pages"),
        ("_MAX_PAGE_STREAM", "page exceeds"),
        ("DOCUMENT_MAX_TEXT_BYTES", "extracted text"),
        ("DOCUMENT_MAX_BLOCKS", "extracted text"),
    ],
)
def test_pdf_extraction_respects_processing_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str, message: str
) -> None:
    original = tmp_path / "notes.pdf"
    original.write_bytes(sample_pdf())
    monkeypatch.setattr(parser, limit, 1 if limit != "DOCUMENT_MAX_BLOCKS" else 0)
    with pytest.raises(DocumentRejected, match=message):
        extract_document(original, "pdf")


@pytest.mark.parametrize("is_docx", [False, True])
def test_docx_archive_rejects_missing_parts_and_unsupported_paths(
    tmp_path: Path, is_docx: bool
) -> None:
    original = tmp_path / "notes.docx"
    with ZipFile(original, "w") as archive:
        archive.writestr("/unscoped.xml", "<part/>")
        if is_docx:
            for part in ("[Content_Types].xml", "_rels/.rels", "word/document.xml"):
                archive.writestr(part, "<part/>")
    with pytest.raises(
        DocumentRejected, match="unsupported archive entry" if is_docx else "do not match"
    ):
        extract_document(original, "docx")


def test_docx_text_extraction_honors_the_section_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = tmp_path / "notes.docx"
    doc = Document()
    doc.add_paragraph("Field observations")
    doc.save(str(original))
    monkeypatch.setattr(parser, "MAX_TEXT_CHARACTERS", 8)
    with pytest.raises(DocumentRejected, match="exceeds the extracted text limit"):
        extract_document(original, "docx")


@pytest.mark.parametrize(
    ("data", "reason"),
    [(b"%PDF-", "do not match"), (b"", "nonempty"), (b" \n", "section is empty")],
)
def test_text_import_requires_real_nonempty_text(tmp_path: Path, data: bytes, reason: str) -> None:
    original = tmp_path / "notes.txt"
    original.write_bytes(data)
    with pytest.raises(DocumentRejected, match=reason):
        extract_document(original, "plain")


@pytest.mark.parametrize("cause", ["dimensions", "png-encoder"])
def test_pdf_preview_rejects_invalid_render_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cause: str
) -> None:
    import cv2  # pyright: ignore[reportMissingModuleSource]
    from pypdfium2 import PdfPage  # pyright: ignore[reportMissingTypeStubs]

    original = tmp_path / "notes.pdf"
    original.write_bytes(sample_pdf())
    if cause == "dimensions":
        monkeypatch.setattr(PdfPage, "get_size", Mock(return_value=(0.0, 300.0)))
    else:
        monkeypatch.setattr(cv2, "imencode", Mock(return_value=(False, None)))
    with pytest.raises(DocumentRejected):
        render_pdf_page(original, 1, tmp_path / "page.png")


@pytest.mark.parametrize(
    ("operation", "format", "source", "page", "expected"),
    [
        ("extract", "pdf", "valid", None, 0),
        ("preview", "pdf", "valid", 1, 0),
        ("preview", "pdf", "valid", None, 2),
        ("preview", "docx", "valid", 1, 2),
        ("extract", "pdf", "missing", None, 1),
        ("extract", "docx", "invalid", None, 2),
    ],
)
def test_document_worker_command_reports_bounded_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    format: str,
    source: str,
    page: int | None,
    expected: int,
) -> None:
    filename = tmp_path / "source"
    output = tmp_path / "output"
    if source == "valid":
        filename.write_bytes(sample_pdf())
    elif source == "invalid":
        filename.write_bytes(b"invalid archive")
    argv = ["workspace_document_parser", operation, format, str(filename), str(output)]
    if page is not None:
        argv.extend(("--page", str(page)))
    monkeypatch.setattr(sys, "argv", argv)
    assert parser.main() == expected


def test_worker_script_entrypoint_exits_with_the_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = tmp_path / "notes"
    original.write_text("Verified note", encoding="utf-8")
    output = tmp_path / "output"
    monkeypatch.setattr(sys, "argv", ["parser", "extract", "plain", str(original), str(output)])
    assert parser.__file__ is not None
    with pytest.raises(SystemExit) as result:
        runpy.run_path(parser.__file__, run_name="__main__")
    assert result.value.code == 0 and output.is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["unstartable", "unexpected-exit", "rejected"])
async def test_worker_errors_do_not_expose_process_diagnostics(outcome: str) -> None:
    runner = AsyncMock(spec=CommandRunner)
    if outcome == "unstartable":
        runner.run.side_effect = OSError("private local path")
    else:
        runner.run.return_value = CommandResult(
            (), 1 if outcome == "unexpected-exit" else 2, "", ""
        )
    expected = DocumentRejected if outcome == "rejected" else DocumentProcessingUnavailable
    with pytest.raises(expected):
        await WorkspaceDocumentProcessor(runner).extract(b"Notes", "plain")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "has_output"),
    [("extract", True), ("extract", False), ("preview", True), ("preview", False)],
)
async def test_worker_output_is_bounded_and_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, has_output: bool
) -> None:
    monkeypatch.setattr(documents, "_MAX_RESULT_BYTES", 4)
    runner = AsyncMock(spec=CommandRunner)

    async def write_result(arguments: Sequence[str], **_: object) -> CommandResult:
        if has_output:
            await AsyncPath(arguments[7]).write_bytes(b"not a valid result")
        return CommandResult(tuple(arguments), 0, "", "")

    runner.run.side_effect = write_result
    processor = WorkspaceDocumentProcessor(runner)
    with pytest.raises(DocumentProcessingUnavailable):
        if operation == "extract":
            await processor.extract(b"Notes", "plain")
        else:
            await processor.preview(tmp_path / "original.pdf", 1)


@pytest.mark.asyncio
async def test_private_storage_detects_corrupted_missing_and_invalid_objects(
    tmp_path: Path,
) -> None:
    notebook_id = uuid4()
    content = b"Original"
    digest = sha256(content).hexdigest()
    with pytest.raises(DocumentStorageUnavailable):
        await document_original(tmp_path, notebook_id, "not-a-digest")
    with pytest.raises(DocumentStorageUnavailable):
        await document_original(tmp_path, notebook_id, digest)
    await store_document(tmp_path, notebook_id, digest, content)
    original = await document_original(tmp_path, notebook_id, digest)
    await store_document(tmp_path, notebook_id, digest, content)
    await AsyncPath(original).write_bytes(b"Different")
    with pytest.raises(DocumentStorageUnavailable):
        await document_original(tmp_path, notebook_id, digest)
    with pytest.raises(DocumentStorageUnavailable):
        await store_document(original, notebook_id, digest, content)


@pytest.mark.asyncio
async def test_private_storage_rejects_paths_resolved_outside_the_notebook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notebook_id = uuid4()
    digest = sha256(b"Notes").hexdigest()
    original_resolve = Path.resolve

    def redirected(path: Path, strict: bool = False) -> Path:
        if path.name == digest:
            return tmp_path / "other-notebook" / digest
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", redirected)
    with pytest.raises(DocumentStorageUnavailable):
        await document_original(tmp_path, notebook_id, digest)
