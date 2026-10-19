"""LlamaParse Cloud OCR adapter for extracted video frames.

This adapter fulfils the ``OCRProvider`` protocol defined in
``galaxy_frog.domain.vision.ports``. It uploads deduplicated frame images
to LlamaCloud REST API, polls for job completion, and normalizes extracted
text blocks into ``OCRBlock`` domain objects with preserved temporal interval
and frame provenance.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx
from pydantic import SecretStr

from galaxy_frog.domain.vision.errors import OCRExtractionError
from galaxy_frog.domain.vision.models import (
    OCRBlock,
    OCRExtractionRequest,
    OCRExtractionResult,
    OCRProviderSpec,
)

_log = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.cloud.llamaindex.ai"
_DEFAULT_TIMEOUT_SECONDS = 60.0
_DEFAULT_POLL_INTERVAL_SECONDS = 1.0
_DEFAULT_MAX_POLL_SECONDS = 120.0
_DEFAULT_PROVIDER_REVISION = "llamaparse-cloud-v1"


def _guess_mime_type(path: Path) -> str:
    """Determine MIME type based on file extension."""
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    return "application/octet-stream"


def _extract_blocks_from_payload(
    payload: Mapping[str, object],
    request: OCRExtractionRequest,
    spec: OCRProviderSpec,
) -> tuple[OCRBlock, ...]:
    """Parse text regions from LlamaParse JSON payload into domain OCRBlocks."""
    blocks: list[OCRBlock] = []

    pages = payload.get("pages")
    if isinstance(pages, Sequence) and not isinstance(pages, (str, bytes)):
        for page in cast(Sequence[object], pages):
            if not isinstance(page, Mapping):
                continue
            page_map = cast(Mapping[str, object], page)

            items = page_map.get("items")
            page_blocks: list[OCRBlock] = []
            if isinstance(items, Sequence) and not isinstance(items, (str, bytes)):
                for item in cast(Sequence[object], items):
                    if not isinstance(item, Mapping):
                        continue
                    item_map = cast(Mapping[str, object], item)
                    raw_text = item_map.get("value") or item_map.get("text") or item_map.get("md")
                    if isinstance(raw_text, str) and raw_text.strip():
                        conf_val = item_map.get("confidence")
                        confidence = (
                            float(conf_val)
                            if isinstance(conf_val, (int, float)) and 0.0 <= conf_val <= 1.0
                            else None
                        )
                        page_blocks.append(
                            OCRBlock(
                                text=raw_text.strip(),
                                start_ms=request.frame.start_ms,
                                end_ms=request.frame.end_ms,
                                confidence=confidence,
                                confidence_method="llamaparse" if confidence is not None else None,
                                source_frame_sequence=request.frame.sequence,
                                provider_spec=spec,
                            )
                        )

            if page_blocks:
                blocks.extend(page_blocks)
            else:
                raw_page_text = page_map.get("text") or page_map.get("md")
                if isinstance(raw_page_text, str) and raw_page_text.strip():
                    conf_val = page_map.get("confidence")
                    confidence = (
                        float(conf_val)
                        if isinstance(conf_val, (int, float)) and 0.0 <= conf_val <= 1.0
                        else None
                    )
                    blocks.append(
                        OCRBlock(
                            text=raw_page_text.strip(),
                            start_ms=request.frame.start_ms,
                            end_ms=request.frame.end_ms,
                            confidence=confidence,
                            confidence_method="llamaparse" if confidence is not None else None,
                            source_frame_sequence=request.frame.sequence,
                            provider_spec=spec,
                        )
                    )

    if not blocks:
        raw_root_text = payload.get("text") or payload.get("markdown") or payload.get("content")
        if isinstance(raw_root_text, str) and raw_root_text.strip():
            blocks.append(
                OCRBlock(
                    text=raw_root_text.strip(),
                    start_ms=request.frame.start_ms,
                    end_ms=request.frame.end_ms,
                    confidence=None,
                    confidence_method=None,
                    source_frame_sequence=request.frame.sequence,
                    provider_spec=spec,
                )
            )

    return tuple(blocks)


class LlamaParseOCRAdapter:
    """OCR provider calling the LlamaParse Cloud REST API.

    Parameters
    ----------
    api_key:
        LlamaCloud API key as a SecretStr or string. If None or blank,
        calling ``extract()`` will raise an ``OCRExtractionError``.
    base_url:
        Base URL for the LlamaCloud API. Defaults to
        ``https://api.cloud.llamaindex.ai``.
    timeout_seconds:
        Network request timeout in seconds.
    poll_interval_seconds:
        Delay between status polling requests.
    max_poll_seconds:
        Maximum total time to wait for parsing completion.
    provider_revision:
        Provider revision string stored in OCRBlock metadata.
    client:
        Optional injected ``httpx.AsyncClient`` for connection pooling or testing.
    """

    def __init__(
        self,
        *,
        api_key: SecretStr | str | None = None,
        base_url: str = _DEFAULT_BASE_URL,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
        max_poll_seconds: float = _DEFAULT_MAX_POLL_SECONDS,
        provider_revision: str = _DEFAULT_PROVIDER_REVISION,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.spec = OCRProviderSpec(
            provider="llamaparse",
            provider_revision=provider_revision,
        )
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._max_poll_seconds = max_poll_seconds
        self._client = client

    def _get_api_key_str(self) -> str:
        """Resolve and validate the API key string."""
        if self._api_key is None:
            msg = "LLAMA_CLOUD_API_KEY is not configured"
            raise OCRExtractionError(msg)
        key = (
            self._api_key.get_secret_value()
            if isinstance(self._api_key, SecretStr)
            else self._api_key
        ).strip()
        if not key:
            msg = "LLAMA_CLOUD_API_KEY must not be empty"
            raise OCRExtractionError(msg)
        return key

    async def extract(self, request: OCRExtractionRequest) -> OCRExtractionResult:
        """Upload a frame to LlamaParse, poll until completion, and return OCR blocks."""
        api_key = self._get_api_key_str()

        if not request.frame.path.is_file():
            msg = f"Frame image not found at {request.frame.path}"
            raise FileNotFoundError(msg)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout_seconds)

        try:
            image_bytes = request.frame.path.read_bytes()
            mime_type = _guess_mime_type(request.frame.path)
            files = {"file": (request.frame.path.name, image_bytes, mime_type)}

            upload_url = f"{self._base_url}/api/parsing/upload"
            try:
                upload_resp = await client.post(upload_url, headers=headers, files=files)
            except httpx.RequestError as exc:
                msg = f"Network failure during LlamaParse frame upload: {exc}"
                raise OCRExtractionError(msg) from exc

            if upload_resp.status_code in {401, 403}:
                msg = f"LlamaParse authentication failed (HTTP {upload_resp.status_code})"
                raise OCRExtractionError(msg)
            if upload_resp.is_error:
                msg = (
                    f"LlamaParse frame upload failed with HTTP {upload_resp.status_code}: "
                    f"{upload_resp.text}"
                )
                raise OCRExtractionError(msg)

            try:
                upload_raw: object = upload_resp.json()
            except ValueError as exc:
                msg = "LlamaParse returned invalid JSON from upload endpoint"
                raise OCRExtractionError(msg) from exc

            if not isinstance(upload_raw, Mapping):
                msg = "LlamaParse upload response is not a JSON object"
                raise OCRExtractionError(msg)

            upload_data = cast(Mapping[str, object], upload_raw)
            raw_id = upload_data.get("id")
            if not raw_id:
                msg = "LlamaParse upload response missing job 'id'"
                raise OCRExtractionError(msg)

            job_id: str = str(raw_id)
            status = str(upload_data.get("status", "PENDING")).upper()

            # Polling loop
            status_url = f"{self._base_url}/api/parsing/job/{job_id}"
            elapsed = 0.0

            while status in {"PENDING", "RUNNING"}:
                if elapsed >= self._max_poll_seconds:
                    msg = (
                        f"LlamaParse extraction timed out after {self._max_poll_seconds}s "
                        f"(job_id={job_id})"
                    )
                    raise TimeoutError(msg)

                await asyncio.sleep(self._poll_interval_seconds)
                elapsed += self._poll_interval_seconds

                try:
                    status_resp = await client.get(status_url, headers=headers)
                except httpx.RequestError as exc:
                    msg = f"Network failure while polling LlamaParse job {job_id}: {exc}"
                    raise OCRExtractionError(msg) from exc

                if status_resp.is_error:
                    msg = (
                        f"LlamaParse job polling failed with HTTP {status_resp.status_code}: "
                        f"{status_resp.text}"
                    )
                    raise OCRExtractionError(msg)

                try:
                    status_raw: object = status_resp.json()
                except ValueError as exc:
                    msg = f"LlamaParse returned invalid JSON while polling job {job_id}"
                    raise OCRExtractionError(msg) from exc

                if not isinstance(status_raw, Mapping):
                    msg = f"LlamaParse status response is not a JSON object for job {job_id}"
                    raise OCRExtractionError(msg)

                status_data = cast(Mapping[str, object], status_raw)
                status = str(status_data.get("status", "")).upper()
                if status == "ERROR":
                    error_msg = str(
                        status_data.get("error_message")
                        or status_data.get("error")
                        or "Unspecified LlamaParse job error"
                    )
                    msg = f"LlamaParse parsing job {job_id} failed: {error_msg}"
                    raise OCRExtractionError(msg)

            # Retrieve result payload
            result_url = f"{self._base_url}/api/parsing/job/{job_id}/result/raw/json"
            try:
                result_resp = await client.get(result_url, headers=headers)
            except httpx.RequestError as exc:
                msg = f"Network failure fetching LlamaParse result for job {job_id}: {exc}"
                raise OCRExtractionError(msg) from exc

            if result_resp.is_error:
                msg = (
                    f"LlamaParse result fetch failed with HTTP {result_resp.status_code}: "
                    f"{result_resp.text}"
                )
                raise OCRExtractionError(msg)

            try:
                result_raw: object = result_resp.json()
            except ValueError as exc:
                msg = f"LlamaParse returned invalid JSON from result endpoint for job {job_id}"
                raise OCRExtractionError(msg) from exc

            if not isinstance(result_raw, Mapping):
                msg = f"LlamaParse result is not a JSON object for job {job_id}"
                raise OCRExtractionError(msg)

            result_data = cast(Mapping[str, object], result_raw)
            blocks = _extract_blocks_from_payload(result_data, request, self.spec)

            return OCRExtractionResult(
                request=request,
                blocks=blocks,
                provider_spec=self.spec,
                extracted_at=datetime.now(UTC),
            )

        finally:
            if owns_client:
                await client.aclose()
