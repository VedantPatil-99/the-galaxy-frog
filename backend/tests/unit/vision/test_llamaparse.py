"""Unit tests for LlamaParseOCRAdapter with 100% statement and branch coverage."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from galaxy_frog.adapters.vision.llamaparse import (
    LlamaParseOCRAdapter,
    _extract_blocks_from_payload,
    _guess_mime_type,
)
from galaxy_frog.domain.vision.errors import OCRExtractionError
from galaxy_frog.domain.vision.models import (
    FrameSample,
    FrameSource,
    OCRExtractionRequest,
    OCRExtractionResult,
    OCRProviderSpec,
)
from galaxy_frog.domain.vision.ports import OCRProvider


@pytest.fixture()
def sample_frame(tmp_path: Path) -> FrameSample:
    img_path = tmp_path / "frame_001.jpg"
    img_path.write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF")
    return FrameSample(
        video_id=uuid4(),
        sequence=0,
        start_ms=0,
        end_ms=5000,
        path=img_path,
        source=FrameSource.SCENE_BOUNDARY,
    )


@pytest.fixture()
def ocr_request(sample_frame: FrameSample) -> OCRExtractionRequest:
    return OCRExtractionRequest(
        video_id=sample_frame.video_id,
        frame=sample_frame,
        job_id=uuid4(),
    )


class TestGuessMimeType:
    def test_known_image_extensions(self) -> None:
        assert _guess_mime_type(Path("frame.jpg")) == "image/jpeg"
        assert _guess_mime_type(Path("frame.jpeg")) == "image/jpeg"
        assert _guess_mime_type(Path("frame.png")) == "image/png"
        assert _guess_mime_type(Path("frame.webp")) == "image/webp"

    def test_unknown_extension_defaults_to_octet_stream(self) -> None:
        assert _guess_mime_type(Path("frame.bmp")) == "application/octet-stream"
        assert _guess_mime_type(Path("frame")) == "application/octet-stream"


class TestExtractBlocksFromPayload:
    def test_empty_payload_returns_empty_tuple(self, ocr_request: OCRExtractionRequest) -> None:
        spec = OCRProviderSpec(provider="llamaparse", provider_revision="v1")
        blocks = _extract_blocks_from_payload({}, ocr_request, spec)
        assert blocks == ()

    def test_page_with_items_and_confidence(self, ocr_request: OCRExtractionRequest) -> None:
        spec = OCRProviderSpec(provider="llamaparse", provider_revision="v1")
        payload: dict[str, Any] = {
            "pages": [
                "not-a-dict",  # Should be skipped
                {
                    "items": [
                        "not-a-dict",  # Should be skipped
                        {"value": ""},  # Empty string skipped
                        {"text": 123},  # Non-string skipped
                        {
                            "value": "Slide Heading",
                            "confidence": 0.95,
                        },
                        {
                            "md": "Bullet point text",
                            "confidence": 1.5,  # Out of [0, 1] bounds -> confidence is None
                        },
                    ]
                },
            ]
        }
        blocks = _extract_blocks_from_payload(payload, ocr_request, spec)

        assert len(blocks) == 2
        assert blocks[0].text == "Slide Heading"
        assert blocks[0].confidence == 0.95
        assert blocks[0].confidence_method == "llamaparse"
        assert blocks[0].start_ms == ocr_request.frame.start_ms
        assert blocks[0].end_ms == ocr_request.frame.end_ms
        assert blocks[0].source_frame_sequence == ocr_request.frame.sequence

        assert blocks[1].text == "Bullet point text"
        assert blocks[1].confidence is None
        assert blocks[1].confidence_method is None

    def test_page_without_items_uses_page_level_text(
        self, ocr_request: OCRExtractionRequest
    ) -> None:
        spec = OCRProviderSpec(provider="llamaparse", provider_revision="v1")
        payload: dict[str, object] = {
            "pages": [
                {
                    "items": [],
                    "text": "Full page raw text",
                    "confidence": 0.88,
                },
                {
                    "md": "Page 2 markdown",
                },
            ]
        }
        blocks = _extract_blocks_from_payload(payload, ocr_request, spec)

        assert len(blocks) == 2
        assert blocks[0].text == "Full page raw text"
        assert blocks[0].confidence == 0.88
        assert blocks[1].text == "Page 2 markdown"
        assert blocks[1].confidence is None

    def test_fallback_to_root_markdown_or_content(self, ocr_request: OCRExtractionRequest) -> None:
        spec = OCRProviderSpec(provider="llamaparse", provider_revision="v1")
        payload: dict[str, object] = {"markdown": "Root level markdown output"}
        blocks = _extract_blocks_from_payload(payload, ocr_request, spec)

        assert len(blocks) == 1
        assert blocks[0].text == "Root level markdown output"
        assert blocks[0].confidence is None

    def test_page_with_empty_text_and_no_items_is_skipped(
        self, ocr_request: OCRExtractionRequest
    ) -> None:
        spec = OCRProviderSpec(provider="llamaparse", provider_revision="v1")
        payload: dict[str, object] = {
            "pages": [
                {
                    "items": [],
                    "text": "   ",
                },
                {
                    "items": [],
                    "text": None,
                },
            ]
        }
        blocks = _extract_blocks_from_payload(payload, ocr_request, spec)
        assert blocks == ()


class TestLlamaParseOCRAdapterInit:
    def test_adapter_satisfies_ocr_provider_protocol(self) -> None:
        adapter = LlamaParseOCRAdapter(api_key="test-key")
        assert isinstance(adapter, OCRProvider)
        assert adapter.spec.provider == "llamaparse"
        assert adapter.spec.provider_revision == "llamaparse-cloud-v1"

    def test_custom_init_parameters(self) -> None:
        adapter = LlamaParseOCRAdapter(
            api_key=SecretStr("secret-key"),
            base_url="https://custom.api.cloud.llamaindex.ai/",
            timeout_seconds=30.0,
            poll_interval_seconds=0.5,
            max_poll_seconds=60.0,
            provider_revision="custom-v2",
        )
        assert adapter.spec.provider_revision == "custom-v2"
        assert adapter._base_url == "https://custom.api.cloud.llamaindex.ai"
        assert adapter._timeout_seconds == 30.0
        assert adapter._poll_interval_seconds == 0.5
        assert adapter._max_poll_seconds == 60.0


class TestLlamaParseOCRAdapterExtract:
    @pytest.mark.asyncio
    async def test_missing_api_key_raises_error(self, ocr_request: OCRExtractionRequest) -> None:
        adapter = LlamaParseOCRAdapter(api_key=None)
        with pytest.raises(OCRExtractionError, match="LLAMA_CLOUD_API_KEY is not configured"):
            await adapter.extract(ocr_request)

    @pytest.mark.asyncio
    async def test_empty_api_key_raises_error(self, ocr_request: OCRExtractionRequest) -> None:
        adapter = LlamaParseOCRAdapter(api_key=SecretStr("   "))
        with pytest.raises(OCRExtractionError, match="LLAMA_CLOUD_API_KEY must not be empty"):
            await adapter.extract(ocr_request)

    @pytest.mark.asyncio
    async def test_missing_frame_image_file_raises_not_found(self, tmp_path: Path) -> None:
        non_existent = FrameSample(
            video_id=uuid4(),
            sequence=0,
            start_ms=0,
            end_ms=5000,
            path=tmp_path / "missing.jpg",
            source=FrameSource.SCENE_BOUNDARY,
        )
        req = OCRExtractionRequest(
            video_id=non_existent.video_id, frame=non_existent, job_id=uuid4()
        )
        adapter = LlamaParseOCRAdapter(api_key="test-key")

        with pytest.raises(FileNotFoundError, match="Frame image not found"):
            await adapter.extract(req)

    @pytest.mark.asyncio
    async def test_upload_network_failure(self, ocr_request: OCRExtractionRequest) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")

        adapter = LlamaParseOCRAdapter(api_key="test-key", client=mock_client)
        with pytest.raises(
            OCRExtractionError, match="Network failure during LlamaParse frame upload"
        ):
            await adapter.extract(ocr_request)

    @pytest.mark.asyncio
    async def test_upload_authentication_failure_401(
        self, ocr_request: OCRExtractionRequest
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_resp

        adapter = LlamaParseOCRAdapter(api_key="bad-key", client=mock_client)
        with pytest.raises(
            OCRExtractionError, match="LlamaParse authentication failed \\(HTTP 401\\)"
        ):
            await adapter.extract(ocr_request)

    @pytest.mark.asyncio
    async def test_upload_http_error_500(self, ocr_request: OCRExtractionRequest) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.is_error = True
        mock_resp.text = "Internal Server Error"
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_resp

        adapter = LlamaParseOCRAdapter(api_key="test-key", client=mock_client)
        with pytest.raises(
            OCRExtractionError, match="LlamaParse frame upload failed with HTTP 500"
        ):
            await adapter.extract(ocr_request)

    @pytest.mark.asyncio
    async def test_upload_invalid_json(self, ocr_request: OCRExtractionRequest) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.is_error = False
        mock_resp.json.side_effect = ValueError("Invalid JSON")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_resp

        adapter = LlamaParseOCRAdapter(api_key="test-key", client=mock_client)
        with pytest.raises(
            OCRExtractionError, match="LlamaParse returned invalid JSON from upload endpoint"
        ):
            await adapter.extract(ocr_request)

    @pytest.mark.asyncio
    async def test_upload_missing_job_id(self, ocr_request: OCRExtractionRequest) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.is_error = False
        mock_resp.json.return_value = {"status": "PENDING"}  # No 'id'
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_resp

        adapter = LlamaParseOCRAdapter(api_key="test-key", client=mock_client)
        with pytest.raises(OCRExtractionError, match="LlamaParse upload response missing job 'id'"):
            await adapter.extract(ocr_request)

    @pytest.mark.asyncio
    async def test_upload_non_dict_response(self, ocr_request: OCRExtractionRequest) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.is_error = False
        mock_resp.json.return_value = ["not", "a", "dict"]
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_resp

        adapter = LlamaParseOCRAdapter(api_key="test-key", client=mock_client)
        with pytest.raises(
            OCRExtractionError, match="LlamaParse upload response is not a JSON object"
        ):
            await adapter.extract(ocr_request)

    @pytest.mark.asyncio
    async def test_polling_timeout(self, ocr_request: OCRExtractionRequest) -> None:
        mock_upload_resp = MagicMock()
        mock_upload_resp.status_code = 200
        mock_upload_resp.is_error = False
        mock_upload_resp.json.return_value = {"id": "job-123", "status": "PENDING"}

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_upload_resp

        # max_poll_seconds = 0.0 -> immediate timeout
        adapter = LlamaParseOCRAdapter(
            api_key="test-key",
            client=mock_client,
            poll_interval_seconds=0.01,
            max_poll_seconds=0.0,
        )

        with pytest.raises(TimeoutError, match="LlamaParse extraction timed out"):
            await adapter.extract(ocr_request)

    @pytest.mark.asyncio
    async def test_polling_network_failure(self, ocr_request: OCRExtractionRequest) -> None:
        mock_upload_resp = MagicMock()
        mock_upload_resp.status_code = 200
        mock_upload_resp.is_error = False
        mock_upload_resp.json.return_value = {"id": "job-123", "status": "PENDING"}

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_upload_resp
        mock_client.get.side_effect = httpx.ReadTimeout("Timeout reading status")

        adapter = LlamaParseOCRAdapter(
            api_key="test-key",
            client=mock_client,
            poll_interval_seconds=0.01,
            max_poll_seconds=5.0,
        )

        with pytest.raises(
            OCRExtractionError, match="Network failure while polling LlamaParse job job-123"
        ):
            await adapter.extract(ocr_request)

    @pytest.mark.asyncio
    async def test_polling_http_error(self, ocr_request: OCRExtractionRequest) -> None:
        mock_upload_resp = MagicMock()
        mock_upload_resp.status_code = 200
        mock_upload_resp.is_error = False
        mock_upload_resp.json.return_value = {"id": "job-123", "status": "PENDING"}

        mock_status_resp = MagicMock()
        mock_status_resp.status_code = 502
        mock_status_resp.is_error = True
        mock_status_resp.text = "Bad Gateway"

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_upload_resp
        mock_client.get.return_value = mock_status_resp

        adapter = LlamaParseOCRAdapter(
            api_key="test-key",
            client=mock_client,
            poll_interval_seconds=0.01,
            max_poll_seconds=5.0,
        )

        with pytest.raises(OCRExtractionError, match="LlamaParse job polling failed with HTTP 502"):
            await adapter.extract(ocr_request)

    @pytest.mark.asyncio
    async def test_polling_invalid_json(self, ocr_request: OCRExtractionRequest) -> None:
        mock_upload_resp = MagicMock()
        mock_upload_resp.status_code = 200
        mock_upload_resp.is_error = False
        mock_upload_resp.json.return_value = {"id": "job-123", "status": "PENDING"}

        mock_status_resp = MagicMock()
        mock_status_resp.status_code = 200
        mock_status_resp.is_error = False
        mock_status_resp.json.side_effect = ValueError("Invalid JSON in status")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_upload_resp
        mock_client.get.return_value = mock_status_resp

        adapter = LlamaParseOCRAdapter(
            api_key="test-key",
            client=mock_client,
            poll_interval_seconds=0.01,
            max_poll_seconds=5.0,
        )

        with pytest.raises(
            OCRExtractionError, match="LlamaParse returned invalid JSON while polling job job-123"
        ):
            await adapter.extract(ocr_request)

    @pytest.mark.asyncio
    async def test_polling_non_dict_response(self, ocr_request: OCRExtractionRequest) -> None:
        mock_upload_resp = MagicMock()
        mock_upload_resp.status_code = 200
        mock_upload_resp.is_error = False
        mock_upload_resp.json.return_value = {"id": "job-123", "status": "PENDING"}

        mock_status_resp = MagicMock()
        mock_status_resp.status_code = 200
        mock_status_resp.is_error = False
        mock_status_resp.json.return_value = ["not", "a", "dict"]

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_upload_resp
        mock_client.get.return_value = mock_status_resp

        adapter = LlamaParseOCRAdapter(
            api_key="test-key",
            client=mock_client,
            poll_interval_seconds=0.01,
            max_poll_seconds=5.0,
        )

        with pytest.raises(
            OCRExtractionError, match="LlamaParse status response is not a JSON object"
        ):
            await adapter.extract(ocr_request)

    @pytest.mark.asyncio
    async def test_polling_status_error(self, ocr_request: OCRExtractionRequest) -> None:
        mock_upload_resp = MagicMock()
        mock_upload_resp.status_code = 200
        mock_upload_resp.is_error = False
        mock_upload_resp.json.return_value = {"id": "job-123", "status": "PENDING"}

        mock_status_resp = MagicMock()
        mock_status_resp.status_code = 200
        mock_status_resp.is_error = False
        mock_status_resp.json.return_value = {
            "status": "ERROR",
            "error_message": "Document conversion failed",
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_upload_resp
        mock_client.get.return_value = mock_status_resp

        adapter = LlamaParseOCRAdapter(
            api_key="test-key",
            client=mock_client,
            poll_interval_seconds=0.01,
            max_poll_seconds=5.0,
        )

        with pytest.raises(
            OCRExtractionError,
            match="LlamaParse parsing job job-123 failed: Document conversion failed",
        ):
            await adapter.extract(ocr_request)

    @pytest.mark.asyncio
    async def test_result_fetch_network_failure(self, ocr_request: OCRExtractionRequest) -> None:
        mock_upload_resp = MagicMock()
        mock_upload_resp.status_code = 200
        mock_upload_resp.is_error = False
        mock_upload_resp.json.return_value = {"id": "job-123", "status": "SUCCESS"}

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_upload_resp
        mock_client.get.side_effect = httpx.ConnectError("Failed to fetch result")

        adapter = LlamaParseOCRAdapter(api_key="test-key", client=mock_client)
        with pytest.raises(
            OCRExtractionError, match="Network failure fetching LlamaParse result for job job-123"
        ):
            await adapter.extract(ocr_request)

    @pytest.mark.asyncio
    async def test_result_fetch_http_error(self, ocr_request: OCRExtractionRequest) -> None:
        mock_upload_resp = MagicMock()
        mock_upload_resp.status_code = 200
        mock_upload_resp.is_error = False
        mock_upload_resp.json.return_value = {"id": "job-123", "status": "SUCCESS"}

        mock_result_resp = MagicMock()
        mock_result_resp.status_code = 404
        mock_result_resp.is_error = True
        mock_result_resp.text = "Result not found"

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_upload_resp
        mock_client.get.return_value = mock_result_resp

        adapter = LlamaParseOCRAdapter(api_key="test-key", client=mock_client)
        with pytest.raises(
            OCRExtractionError, match="LlamaParse result fetch failed with HTTP 404"
        ):
            await adapter.extract(ocr_request)

    @pytest.mark.asyncio
    async def test_result_fetch_invalid_json(self, ocr_request: OCRExtractionRequest) -> None:
        mock_upload_resp = MagicMock()
        mock_upload_resp.status_code = 200
        mock_upload_resp.is_error = False
        mock_upload_resp.json.return_value = {"id": "job-123", "status": "SUCCESS"}

        mock_result_resp = MagicMock()
        mock_result_resp.status_code = 200
        mock_result_resp.is_error = False
        mock_result_resp.json.side_effect = ValueError("Corrupt result JSON")

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_upload_resp
        mock_client.get.return_value = mock_result_resp

        adapter = LlamaParseOCRAdapter(api_key="test-key", client=mock_client)
        with pytest.raises(
            OCRExtractionError, match="LlamaParse returned invalid JSON from result endpoint"
        ):
            await adapter.extract(ocr_request)

    @pytest.mark.asyncio
    async def test_result_fetch_non_dict_response(self, ocr_request: OCRExtractionRequest) -> None:
        mock_upload_resp = MagicMock()
        mock_upload_resp.status_code = 200
        mock_upload_resp.is_error = False
        mock_upload_resp.json.return_value = {"id": "job-123", "status": "SUCCESS"}

        mock_result_resp = MagicMock()
        mock_result_resp.status_code = 200
        mock_result_resp.is_error = False
        mock_result_resp.json.return_value = ["not", "a", "dict"]

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_upload_resp
        mock_client.get.return_value = mock_result_resp

        adapter = LlamaParseOCRAdapter(api_key="test-key", client=mock_client)
        with pytest.raises(OCRExtractionError, match="LlamaParse result is not a JSON object"):
            await adapter.extract(ocr_request)

    @pytest.mark.asyncio
    async def test_successful_extraction_with_polling(
        self, ocr_request: OCRExtractionRequest
    ) -> None:
        # Step 1: Upload returns PENDING
        mock_upload_resp = MagicMock()
        mock_upload_resp.status_code = 200
        mock_upload_resp.is_error = False
        mock_upload_resp.json.return_value = {"id": "job-456", "status": "PENDING"}

        # Step 2: Poll 1 returns RUNNING, Poll 2 returns SUCCESS
        mock_status_running = MagicMock()
        mock_status_running.status_code = 200
        mock_status_running.is_error = False
        mock_status_running.json.return_value = {"status": "RUNNING"}

        mock_status_success = MagicMock()
        mock_status_success.status_code = 200
        mock_status_success.is_error = False
        mock_status_success.json.return_value = {"status": "SUCCESS"}

        # Step 3: Result returns JSON with pages and items
        mock_result_resp = MagicMock()
        mock_result_resp.status_code = 200
        mock_result_resp.is_error = False
        mock_result_resp.json.return_value = {
            "pages": [
                {
                    "items": [
                        {"value": "Detected text on slide", "confidence": 0.92},
                    ]
                }
            ]
        }

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = mock_upload_resp
        mock_client.get.side_effect = [
            mock_status_running,
            mock_status_success,
            mock_result_resp,
        ]

        adapter = LlamaParseOCRAdapter(
            api_key=SecretStr("real-test-key"),
            client=mock_client,
            poll_interval_seconds=0.01,
            max_poll_seconds=5.0,
        )

        result = await adapter.extract(ocr_request)

        assert isinstance(result, OCRExtractionResult)
        assert result.request == ocr_request
        assert result.provider_spec == adapter.spec
        assert len(result.blocks) == 1
        assert result.blocks[0].text == "Detected text on slide"
        assert result.blocks[0].confidence == 0.92
        assert result.blocks[0].confidence_method == "llamaparse"
        assert result.blocks[0].start_ms == ocr_request.frame.start_ms
        assert result.blocks[0].end_ms == ocr_request.frame.end_ms
        assert result.blocks[0].source_frame_sequence == ocr_request.frame.sequence
        assert result.extracted_at.tzinfo is not None

    @pytest.mark.asyncio
    async def test_internal_client_closed_when_owned(
        self, ocr_request: OCRExtractionRequest
    ) -> None:
        # Test that when no client is passed in, LlamaParseOCRAdapter creates and closes it
        mock_upload_resp = MagicMock()
        mock_upload_resp.status_code = 200
        mock_upload_resp.is_error = False
        mock_upload_resp.json.return_value = {"id": "job-789", "status": "SUCCESS"}

        mock_result_resp = MagicMock()
        mock_result_resp.status_code = 200
        mock_result_resp.is_error = False
        mock_result_resp.json.return_value = {"text": "Simple root text"}

        mock_internal_client = AsyncMock(spec=httpx.AsyncClient)
        mock_internal_client.post.return_value = mock_upload_resp
        mock_internal_client.get.return_value = mock_result_resp

        with patch("httpx.AsyncClient", return_value=mock_internal_client):
            adapter = LlamaParseOCRAdapter(api_key="owned-key", client=None)
            result = await adapter.extract(ocr_request)

            assert len(result.blocks) == 1
            assert result.blocks[0].text == "Simple root text"
            mock_internal_client.aclose.assert_awaited_once()
