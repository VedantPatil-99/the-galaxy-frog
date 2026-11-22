"""LlamaParse v2 adapter for exactly one rendered PDF page."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated

import httpx
from pydantic import BaseModel, SecretStr, StringConstraints, ValidationError

from galaxy_frog.domain.workspace_ocr import (
    OCR_TIMEOUT_SECONDS,
    PageOCRFailed,
    PageOCROutcomeUnknown,
    PageOCRResult,
)

_MAX_PROVIDER_BYTES = 256 * 1024
_Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,100}$")]


class _ProviderReference(BaseModel):
    id: _Identifier


class _ProviderJob(_ProviderReference):
    status: str
    error_code: str | None = None


class _PageText(BaseModel):
    page_number: int
    text: str


class _TextResult(BaseModel):
    pages: tuple[_PageText, ...]


class _JobResult(BaseModel):
    job: _ProviderJob
    text: _TextResult | None = None


async def _body(client: httpx.AsyncClient, request: httpx.Request) -> bytes:
    response = await client.send(request, stream=True)
    try:
        if response.is_client_error and request.method == "POST":
            raise PageOCRFailed
        if not response.is_success:
            raise PageOCROutcomeUnknown
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=16_384):
            if len(body) + len(chunk) > _MAX_PROVIDER_BYTES:
                raise PageOCROutcomeUnknown
            body.extend(chunk)
        return bytes(body)
    finally:
        await response.aclose()


class LlamaParsePageOCR:
    """Upload one page image under a generic filename and read its Fast parse result."""

    def __init__(
        self,
        api_key: SecretStr,
        origin: str,
        *,
        poll_interval: float = 1.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._origin = origin
        self._poll_interval = poll_interval
        self._transport = transport

    async def extract(
        self, png: bytes, *, record_job: Callable[[str], Awaitable[None]]
    ) -> PageOCRResult:
        try:
            async with (
                asyncio.timeout(OCR_TIMEOUT_SECONDS),
                httpx.AsyncClient(
                    base_url=self._origin,
                    headers={
                        "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                        "Accept": "application/json",
                    },
                    timeout=10,
                    follow_redirects=False,
                    trust_env=False,
                    transport=self._transport,
                ) as client,
            ):
                upload = client.build_request(
                    "POST",
                    "/api/v1/beta/files",
                    data={"purpose": "parse"},
                    files={"file": ("page.png", png, "image/png")},
                )
                file = _ProviderReference.model_validate_json(await _body(client, upload))
                start = client.build_request(
                    "POST",
                    "/api/v2/parse",
                    json={"file_id": file.id, "tier": "fast", "version": "latest"},
                )
                job = _ProviderReference.model_validate_json(await _body(client, start))
                await record_job(job.id)

                while True:
                    get = client.build_request(
                        "GET", f"/api/v2/parse/{job.id}", params={"expand": "text"}
                    )
                    result = _JobResult.model_validate_json(await _body(client, get))
                    if result.job.id != job.id:
                        raise PageOCROutcomeUnknown
                    if result.job.status == "COMPLETED":
                        if (
                            result.text is None
                            or len(result.text.pages) != 1
                            or result.text.pages[0].page_number != 1
                        ):
                            raise PageOCROutcomeUnknown
                        return PageOCRResult(result.text.pages[0].text)
                    if (
                        result.job.error_code == "NO_DATA_FOUND_IN_FILE"
                        and result.job.status == "FAILED"
                    ):
                        return PageOCRResult("")
                    if result.job.status in {"FAILED", "CANCELLED"}:
                        raise PageOCRFailed
                    if result.job.status not in {"PENDING", "RUNNING"}:
                        raise PageOCROutcomeUnknown
                    await asyncio.sleep(self._poll_interval)
        except httpx.HTTPError, ValidationError, TimeoutError:
            raise PageOCROutcomeUnknown from None
