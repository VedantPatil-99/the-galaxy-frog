"""Run format-specific document parsing or PDF rasterization in a child process."""

import argparse
import math
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import cast
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from docx import Document
from docx.table import Table
from pypdf import PdfReader

from galaxy_frog.domain.workspace_documents import (
    DOCUMENT_MAX_BLOCKS,
    DOCUMENT_MAX_BYTES,
    DOCUMENT_MAX_PAGES,
    DOCUMENT_MAX_TEXT_BYTES,
    DocumentFormat,
    DocumentRejected,
    ParsedDocument,
    StoredDocumentSection,
)
from galaxy_frog.domain.workspace_text import (
    MAX_TEXT_CHARACTERS,
    WorkspaceTextRejected,
    prepare_workspace_text,
)

_MAX_PAGE_STREAM = 8 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
_MAX_ARCHIVE_ENTRIES = 256
_PREVIEW_MAX_EDGE = 1440


def _pdf_text(path: Path) -> Iterator[str]:
    with path.open("rb") as original:
        if original.read(5) != b"%PDF-":
            raise DocumentRejected("The file contents do not match a PDF.")
        original.seek(0)
        pdf = PdfReader(original)
        if pdf.is_encrypted:
            raise DocumentRejected("Remove password protection from the PDF before uploading it.")
        if not 1 <= len(pdf.pages) <= DOCUMENT_MAX_PAGES:
            raise DocumentRejected(f"Choose a PDF with 1 to {DOCUMENT_MAX_PAGES} pages.")
        for page in pdf.pages:
            contents = page.get_contents()
            if contents is not None and len(contents.get_data()) > _MAX_PAGE_STREAM:
                raise DocumentRejected("A PDF page exceeds the processing limit.")
            yield page.extract_text()


def _check_docx(path: Path) -> None:
    with ZipFile(path) as archive:
        entries = archive.infolist()
        names = {entry.filename for entry in entries}
        if (
            len(entries) > _MAX_ARCHIVE_ENTRIES
            or len(entries) != len(names)
            or sum(entry.file_size for entry in entries) > _MAX_ARCHIVE_BYTES
        ):
            raise DocumentRejected("This DOCX archive exceeds the processing limits.")
        if not {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}.issubset(names):
            raise DocumentRejected("The file contents do not match a DOCX document.")
        for entry in entries:
            entry_path = PurePosixPath(entry.filename)
            if (
                entry_path.is_absolute()
                or ".." in entry_path.parts
                or "\\" in entry.filename
                or entry.flag_bits & 1
                or entry.compress_type not in {ZIP_STORED, ZIP_DEFLATED}
            ):
                raise DocumentRejected("This DOCX contains an unsupported archive entry.")


def _docx_segments(path: Path) -> Iterator[str]:
    _check_docx(path)
    with path.open("rb") as original:
        document = Document(original)
    for item in document.iter_inner_content():
        if isinstance(item, Table):
            for row in item.rows:
                yield "\t".join(cell.text for cell in row.cells)
        else:
            yield item.text


def _word_text(path: Path) -> str:
    paragraphs: list[str] = []
    length = 0
    for segment in _docx_segments(path):
        if segment.strip():
            length += len(segment) + 2
            if length > MAX_TEXT_CHARACTERS:
                raise DocumentRejected("The DOCX exceeds the extracted text limit.")
            paragraphs.append(segment)
    return "\n\n".join(paragraphs)


def _plain_text(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith((b"%PDF-", b"PK\x03\x04")):
        raise DocumentRejected("The file contents do not match a UTF-8 text document.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DocumentRejected("Save the text file with UTF-8 encoding and try again.") from None


def extract_document(path: Path, format: DocumentFormat) -> ParsedDocument:
    if not 1 <= path.stat().st_size <= DOCUMENT_MAX_BYTES:
        raise DocumentRejected("Choose a nonempty file of 8 MiB or less.")
    if format == "pdf":
        parts = _pdf_text(path)
    else:
        parts = iter((_word_text(path) if format == "docx" else _plain_text(path),))
    sections: list[StoredDocumentSection] = []
    byte_count = block_count = 0
    for text in parts:
        if not text.strip() and format == "pdf":
            sections.append(StoredDocumentSection(text="", spans=()))
            continue
        try:
            prepared = prepare_workspace_text(text)
        except WorkspaceTextRejected:
            raise DocumentRejected("A document section is empty, invalid, or too large.") from None
        byte_count += len(prepared.text.encode("utf-8"))
        block_count += len(prepared.spans)
        if byte_count > DOCUMENT_MAX_TEXT_BYTES or block_count > DOCUMENT_MAX_BLOCKS:
            raise DocumentRejected("The document exceeds the extracted text limit.")
        sections.append(StoredDocumentSection(text=prepared.text, spans=prepared.spans))
    return ParsedDocument(sections=tuple(sections))


def render_pdf_page(path: Path, page_number: int, output: Path) -> None:
    import cv2  # pyright: ignore[reportMissingModuleSource]
    from numpy import uint8
    from numpy.typing import NDArray
    from pypdfium2 import PdfBitmap, PdfDocument  # pyright: ignore[reportMissingTypeStubs]

    with PdfDocument(path) as pdf:
        if not 1 <= page_number <= min(DOCUMENT_MAX_PAGES, len(pdf)):
            raise DocumentRejected("The requested PDF page is unavailable.")
        page = pdf[page_number - 1]
        try:
            width, height = page.get_size()
            if not all(math.isfinite(edge) and edge > 0 for edge in (width, height)):
                raise DocumentRejected("This PDF page has invalid dimensions.")
            scale = min(2.0, math.nextafter(_PREVIEW_MAX_EDGE / max(width, height), 0.0))
            # PDFium accepts fractional scales although the untyped wrapper defaults to an int.
            bitmap = cast(
                PdfBitmap,
                page.render(scale=scale, draw_annots=False),  # pyright: ignore[reportUnknownMemberType, reportArgumentType]
            )
            try:
                pixels = cast(NDArray[uint8], bitmap.to_numpy())
                encoded, image = cv2.imencode(".png", pixels)
                if not encoded:
                    raise DocumentRejected("The PDF page could not be rendered.")
                output.write_bytes(image.tobytes())
            finally:
                bitmap.close()  # pyright: ignore[reportUnknownMemberType]
        finally:
            page.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("extract", "preview"))
    parser.add_argument("format", choices=("pdf", "docx", "plain", "markdown"))
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--page", type=int)
    args = parser.parse_args()
    try:
        if args.operation == "extract":
            result = extract_document(args.source, cast(DocumentFormat, args.format))
            args.output.write_text(result.model_dump_json(), encoding="utf-8")
        elif args.format == "pdf" and args.page is not None:
            render_pdf_page(args.source, args.page, args.output)
        else:
            raise DocumentRejected("Only imported PDF pages have an image preview.")
    except DocumentRejected as error:
        print(str(error))
        return 2
    except ImportError, OSError:
        return 1
    except Exception:
        print("The document is damaged or could not be processed.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
