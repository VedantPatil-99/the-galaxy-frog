import json
from collections.abc import Callable
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr

from galaxy_frog.adapters.workspace_page_ocr import LlamaParsePageOCR
from galaxy_frog.api.app import create_app
from galaxy_frog.config import Settings
from galaxy_frog.domain.workspace_ocr import PageOCRFailed, PageOCROutcomeUnknown

ORIGIN = "https://parse.invalid"
PAGE = b"\x89PNG\r\n\x1a\nfixture-page-image"
UPLOAD = "/api/v1/beta/files"
START = "/api/v2/parse"
POLL = "/api/v2/parse/job-1"


def adapter(handler: Callable[[httpx.Request], httpx.Response]) -> LlamaParsePageOCR:
    return LlamaParsePageOCR(
        SecretStr("test-token"), ORIGIN, poll_interval=0, transport=httpx.MockTransport(handler)
    )


def upstream(
    request: httpx.Request, result: dict[str, object], *, status: int = 200
) -> httpx.Response:
    if request.url.path == UPLOAD:
        return httpx.Response(200, json={"id": "dfl-file-1"})
    if request.url.path == START:
        return httpx.Response(200, json={"id": "job-1", "status": "PENDING"})
    return httpx.Response(status, json=result)


@pytest.mark.asyncio
async def test_page_ocr_uploads_only_the_fixed_png_and_records_job_before_polling() -> None:
    recorded = AsyncMock()
    paths: list[str] = []

    def provider(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-token"
        assert request.url.host == "parse.invalid"
        paths.append(request.url.path)
        match request.url.path:
            case "/api/v1/beta/files":
                assert b'name="purpose"\r\n\r\nparse' in request.content
                assert b'filename="page.png"' in request.content
                assert PAGE in request.content
            case "/api/v2/parse":
                assert json.loads(request.content) == {
                    "file_id": "dfl-file-1",
                    "tier": "fast",
                    "version": "latest",
                }
            case "/api/v2/parse/job-1":
                recorded.assert_awaited_once_with("job-1")
                assert request.url.params["expand"] == "text"
            case _:
                pytest.fail("Unexpected provider endpoint")
        result: dict[str, object] = {"job": {"id": "job-1", "status": "RUNNING"}}
        if paths.count(POLL) == 2:
            result = {
                "job": {"id": "job-1", "status": "COMPLETED"},
                "text": {"pages": [{"page_number": 1, "text": "Read this page"}]},
            }
        return upstream(request, result)

    result = await adapter(provider).extract(PAGE, record_job=recorded)
    assert result.text == "Read this page"
    assert paths == [UPLOAD, START, POLL, POLL]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        {"job": {"id": "other", "status": "COMPLETED"}},
        {"job": {"id": "job-1", "status": "COMPLETED"}},
        {"job": {"id": "job-1", "status": "COMPLETED"}, "text": {"pages": []}},
        {
            "job": {"id": "job-1", "status": "COMPLETED"},
            "text": {"pages": [{"page_number": 2, "text": "Wrong page"}]},
        },
        {"job": {"id": "job-1", "status": "UNRECOGNIZED"}},
        {"job": {"id": "../wrong", "status": "COMPLETED"}},
    ],
    ids=["wrong-job", "missing-text", "no-page", "wrong-page", "unknown-state", "invalid-id"],
)
async def test_inconsistent_provider_results_are_unknown(result: dict[str, object]) -> None:
    with pytest.raises(PageOCROutcomeUnknown):
        await adapter(lambda request: upstream(request, result)).extract(
            PAGE, record_job=AsyncMock()
        )


@pytest.mark.asyncio
async def test_a_page_with_no_readable_text_has_an_empty_result() -> None:
    result: dict[str, object] = {
        "job": {"id": "job-1", "status": "FAILED", "error_code": "NO_DATA_FOUND_IN_FILE"}
    }
    evidence = await adapter(lambda request: upstream(request, result)).extract(
        PAGE, record_job=AsyncMock()
    )
    assert evidence.text == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("upload-rejected", PageOCRFailed),
        ("job-failed", PageOCRFailed),
        ("poll-error", PageOCROutcomeUnknown),
        ("oversized", PageOCROutcomeUnknown),
        ("network", PageOCROutcomeUnknown),
        ("timeout", PageOCROutcomeUnknown),
    ],
)
async def test_provider_failures_never_retry_an_upload(
    case: str, expected: type[Exception]
) -> None:
    uploads = 0

    def provider(request: httpx.Request) -> httpx.Response:
        nonlocal uploads
        if request.url.path == UPLOAD:
            uploads += 1
            if case == "upload-rejected":
                return httpx.Response(429)
            if case == "oversized":
                return httpx.Response(200, content=b"x" * (256 * 1024 + 1))
            if case == "network":
                raise httpx.ConnectError("unavailable")
            if case == "timeout":
                raise TimeoutError
        return upstream(
            request,
            {"job": {"id": "job-1", "status": "FAILED"}},
            status=404 if case == "poll-error" else 200,
        )

    with pytest.raises(expected):
        await adapter(provider).extract(PAGE, record_job=AsyncMock())
    assert uploads == 1


@pytest.mark.parametrize(
    ("enabled", "key", "origin", "available"),
    [
        (False, "configured", ORIGIN, False),
        (True, None, ORIGIN, False),
        (True, "configured", "http://insecure.invalid", False),
        (True, "configured", ORIGIN, True),
    ],
)
def test_document_ocr_factory_requires_opt_in_key_and_https(
    enabled: bool, key: str | None, origin: str, available: bool
) -> None:
    app = create_app(
        Settings(
            workspace_document_ocr_enabled=enabled,
            llama_cloud_api_key=SecretStr(key) if key else None,
            llama_cloud_base_url=origin,
        )
    )
    assert isinstance(app.state.workspace_page_ocr_provider, LlamaParsePageOCR) is available
