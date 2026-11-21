"""Bounded document processing and local private storage for source originals."""

import asyncio
import re
import sys
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from uuid import UUID

from anyio import Path as AsyncPath
from anyio import to_thread
from pydantic import ValidationError

from galaxy_frog.adapters.media.process import AsyncSubprocessRunner, CommandRunner
from galaxy_frog.domain.workspace_documents import (
    DOCUMENT_MAX_BYTES,
    DocumentFormat,
    DocumentRejected,
    DocumentStorageUnavailable,
    ParsedDocument,
)

_MAX_RESULT_BYTES = 2 * 1024 * 1024
_MAX_PREVIEW_BYTES = 6 * 1024 * 1024
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class DocumentProcessingUnavailable(Exception):
    """The configured local processor could not run or returned invalid output."""


class DocumentProcessingTimeout(Exception):
    """The processing request exceeded its deadline."""


class WorkspaceDocumentProcessor:
    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or AsyncSubprocessRunner()
        self._slots = asyncio.Semaphore(2)

    async def _process(self, directory: Path, *arguments: str) -> None:
        command = (
            sys.executable,
            "-I",
            "-m",
            "galaxy_frog.adapters.workspace_document_parser",
            *arguments,
        )
        try:
            async with asyncio.timeout(25), self._slots:
                result = await self._runner.run(command, cwd=directory, timeout_seconds=20)
        except TimeoutError:
            raise DocumentProcessingTimeout from None
        except OSError:
            raise DocumentProcessingUnavailable from None
        if result.return_code == 2:
            raise DocumentRejected(result.stdout.strip() or "This document could not be processed.")
        if result.return_code != 0:
            raise DocumentProcessingUnavailable

    async def extract(self, data: bytes, format: DocumentFormat) -> ParsedDocument:
        with TemporaryDirectory(prefix="galaxy-document-") as temporary:
            directory = Path(temporary)
            input_file = directory / "original"
            output_file = directory / "sections.json"
            try:
                await AsyncPath(input_file).write_bytes(data)
                await self._process(directory, "extract", format, str(input_file), str(output_file))
                async with await AsyncPath(output_file).open("rb") as result:
                    body = await result.read(_MAX_RESULT_BYTES + 1)
                if len(body) > _MAX_RESULT_BYTES:
                    raise DocumentProcessingUnavailable
                return ParsedDocument.model_validate_json(body)
            except OSError, ValidationError:
                raise DocumentProcessingUnavailable from None

    async def preview(self, original: Path, page_number: int) -> bytes:
        with TemporaryDirectory(prefix="galaxy-document-preview-") as temporary:
            directory = Path(temporary)
            output_file = directory / "page.png"
            try:
                await self._process(
                    directory,
                    "preview",
                    "pdf",
                    str(original),
                    str(output_file),
                    "--page",
                    str(page_number),
                )
                async with await AsyncPath(output_file).open("rb") as preview:
                    body = await preview.read(_MAX_PREVIEW_BYTES + 1)
                if len(body) > _MAX_PREVIEW_BYTES or not body.startswith(_PNG_MAGIC):
                    raise DocumentProcessingUnavailable
                return body
            except OSError:
                raise DocumentProcessingUnavailable from None


def _object_path(root: Path, notebook_id: UUID, digest: str) -> Path:
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise DocumentStorageUnavailable
    directory = root.resolve()
    path = (directory / str(notebook_id) / digest).resolve()
    if path != directory / str(notebook_id) / digest:
        raise DocumentStorageUnavailable
    return path


def _write_original(root: Path, notebook_id: UUID, digest: str, data: bytes) -> None:
    try:
        path = _object_path(root, notebook_id, digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            _verify_original(root, notebook_id, digest)
            return
        with ExitStack() as cleanup:
            with NamedTemporaryFile(dir=path.parent, prefix=".upload-", delete=False) as output:
                staged = Path(output.name)
                cleanup.callback(staged.unlink, missing_ok=True)
                output.write(data)
            staged.replace(path)
    except OSError, RuntimeError, ValueError:
        raise DocumentStorageUnavailable from None


def _verify_original(root: Path, notebook_id: UUID, digest: str) -> Path:
    try:
        path = _object_path(root, notebook_id, digest)
        with path.open("rb") as original:
            data = original.read(DOCUMENT_MAX_BYTES + 1)
        if not 1 <= len(data) <= DOCUMENT_MAX_BYTES or sha256(data).hexdigest() != digest:
            raise DocumentStorageUnavailable
        return path
    except OSError, RuntimeError, ValueError:
        raise DocumentStorageUnavailable from None


async def store_document(root: Path, notebook_id: UUID, digest: str, data: bytes) -> None:
    await to_thread.run_sync(_write_original, root, notebook_id, digest, data)


async def document_original(root: Path, notebook_id: UUID, digest: str) -> Path:
    return await to_thread.run_sync(_verify_original, root, notebook_id, digest)
