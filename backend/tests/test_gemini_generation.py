"""Exercise the actual SDK over fake HTTP, including single-dispatch privacy rules."""

import json
import traceback
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import httpx2
import pytest
import pytest_asyncio
from google import genai
from google.genai import types

from galaxy_frog.adapters.generation.gemini import (
    ANSWER_SCHEMA,
    GeminiGeneration,
    GeminiJSON,
    daily_quota,
)
from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure

MODEL = "gemini-3.5-flash-lite"
ANSWER: dict[str, object] = {"answer": "Grounded answer", "confidence": "low", "citations": []}


def result(**overrides: object) -> dict[str, object]:
    output = overrides.pop("output_text", json.dumps(ANSWER))
    return {
        "id": "test-interaction",
        "status": "completed",
        "model": MODEL,
        "steps": [{"type": "model_output", "content": [{"type": "text", "text": output}]}],
        "usage": {
            "total_input_tokens": 100,
            "total_output_tokens": 20,
            "total_thought_tokens": 5,
            "total_cached_tokens": 10,
        },
        **overrides,
    }


@pytest_asyncio.fixture
async def sdk() -> AsyncGenerator[
    tuple[genai.Client, list[httpx2.Request], list[httpx2.Response | Exception]]
]:
    requests: list[httpx2.Request] = []
    responses: list[httpx2.Response | Exception] = [httpx2.Response(200, json=result())]

    def send(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        response = responses[0]
        if isinstance(response, Exception):
            raise response
        return response

    transport = httpx2.AsyncClient(transport=httpx2.MockTransport(send), trust_env=False)
    client = genai.Client(
        api_key="synthetic-provider-key",
        vertexai=False,
        http_options=types.HttpOptions(
            api_version="v1",
            httpx_async_client=transport,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    try:
        yield client, requests, responses
    finally:
        await client.aio.aclose()
        client.close()


@pytest.mark.asyncio
async def test_stateless_sdk_request_and_usage(
    sdk: tuple[genai.Client, list[httpx2.Request], list[httpx2.Response | Exception]],
) -> None:
    client, requests, _responses = sdk
    verify = AsyncMock()
    provider = GeminiJSON("synthetic-provider-key", MODEL, before_dispatch=verify, client=client)
    generator = GeminiGeneration(provider)
    assert (await generator.generate("Question", ())).answer == "Grounded answer"
    assert (await generator.generate_moment("Moment", ())).answer == "Grounded answer"
    assert len(requests) == verify.await_count == 2
    payload = json.loads(requests[0].content)
    assert requests[0].url.path == "/v1/interactions"
    assert requests[0].url.host == "generativelanguage.googleapis.com"
    assert "synthetic-provider-key" not in str(requests[0].url)
    assert payload["store"] is False
    assert "previous_interaction_id" not in payload and "tools" not in payload
    assert payload["response_format"]["schema"] == ANSWER_SCHEMA
    assert payload["generation_config"] == {"thinking_level": "low", "max_output_tokens": 4096}
    assert provider.usage == {
        "input_tokens": 100,
        "output_tokens": 20,
        "thinking_tokens": 5,
        "cached_input_tokens": 10,
    }


@pytest.mark.asyncio
async def test_consent_denial_prevents_sdk_dispatch(
    sdk: tuple[genai.Client, list[httpx2.Request], list[httpx2.Response | Exception]],
) -> None:
    client, requests, _responses = sdk
    verify = AsyncMock(side_effect=ProviderFailure(ProviderCode.CONSENT_REQUIRED))
    with pytest.raises(ProviderFailure, match="PROVIDER_CONSENT_REQUIRED"):
        await GeminiJSON("key", MODEL, before_dispatch=verify, client=client).generate(
            "prompt", ANSWER_SCHEMA
        )
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code",
    [
        (401, ProviderCode.AUTH_FAILED),
        (403, ProviderCode.AUTH_FAILED),
        (429, ProviderCode.RATE_LIMITED),
        (400, ProviderCode.INVALID_INPUT),
        (413, ProviderCode.INVALID_INPUT),
        (422, ProviderCode.INVALID_INPUT),
        (404, ProviderCode.SETUP_REQUIRED),
        (503, ProviderCode.OUTCOME_UNKNOWN),
    ],
)
async def test_sdk_errors_are_safe_and_never_retried(
    sdk: tuple[genai.Client, list[httpx2.Request], list[httpx2.Response | Exception]],
    status: int,
    code: ProviderCode,
) -> None:
    client, requests, responses = sdk
    responses[0] = httpx2.Response(
        status,
        json={"error": {"message": "synthetic-private-credential", "code": status}},
        headers={"retry-after": "30"},
    )
    with pytest.raises(ProviderFailure) as caught:
        await GeminiJSON("key", MODEL, client=client).generate("prompt", ANSWER_SCHEMA)
    assert caught.value.code == code
    assert len(requests) == 1
    assert "synthetic-private-credential" not in "".join(traceback.format_exception(caught.value))
    if status == 429:
        assert caught.value.retry_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes,code",
    [
        ({"status": "incomplete"}, ProviderCode.INVALID_OUTPUT),
        ({"model": "gemini-3.8-flash"}, ProviderCode.INCOMPATIBLE),
        ({"output_text": ""}, ProviderCode.INVALID_OUTPUT),
        ({"output_text": "x" * 262_145}, ProviderCode.INVALID_OUTPUT),
        ({"output_text": "invalid JSON"}, ProviderCode.INVALID_OUTPUT),
        ({"status": "failed", "errors": [{"code": "SAFETY"}]}, ProviderCode.SAFETY_BLOCKED),
        ({"usage": {"total_input_tokens": -1}}, ProviderCode.INVALID_OUTPUT),
    ],
)
async def test_incomplete_unsafe_and_invalid_output_is_rejected(
    sdk: tuple[genai.Client, list[httpx2.Request], list[httpx2.Response | Exception]],
    changes: dict[str, object],
    code: ProviderCode,
) -> None:
    client, _requests, responses = sdk
    responses[0] = httpx2.Response(200, json=result(**changes))
    with pytest.raises(ProviderFailure) as caught:
        await GeminiJSON("key", MODEL, client=client).generate("prompt", ANSWER_SCHEMA)
    assert caught.value.code == code


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx2.ConnectError, httpx2.ReadError, httpx2.ReadTimeout])
async def test_unknown_network_outcome_does_not_leak(
    sdk: tuple[genai.Client, list[httpx2.Request], list[httpx2.Response | Exception]],
    failure: type[Exception],
) -> None:
    client, requests, responses = sdk
    responses[0] = failure("synthetic-private-credential")
    with pytest.raises(ProviderFailure, match="PROVIDER_OUTCOME_UNKNOWN") as caught:
        await GeminiJSON("key", MODEL, client=client).generate("prompt", ANSWER_SCHEMA)
    assert len(requests) == 1
    assert "synthetic-private-credential" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.asyncio
async def test_limits_precede_dispatch() -> None:
    for model, tokens, timeout in [("unsupported", 4096, 75), (MODEL, 1, 75), (MODEL, 4096, 0)]:
        with pytest.raises((ProviderFailure, ValueError)):
            GeminiJSON("key", model, max_output_tokens=tokens, timeout_seconds=timeout)
    verify = AsyncMock()
    provider = GeminiJSON("key", MODEL, before_dispatch=verify)
    with pytest.raises(ProviderFailure, match="PROVIDER_INPUT_LIMIT"):
        await provider.generate("x" * 131_073, ANSWER_SCHEMA)
    verify.assert_not_awaited()


@pytest.mark.asyncio
async def test_owned_sdk_client_pins_privacy_options(
    sdk: tuple[genai.Client, list[httpx2.Request], list[httpx2.Response | Exception]],
) -> None:
    client, requests, responses = sdk
    responses[0] = httpx2.Response(200, json=result(usage=None))
    with patch("google.genai.Client", return_value=client) as factory:
        provider = GeminiJSON("synthetic-provider-key", MODEL, revision=MODEL)
        assert await provider.generate("prompt", ANSWER_SCHEMA) == ANSWER
    assert len(requests) == 1 and provider.usage is None
    options = factory.call_args.kwargs
    assert options["vertexai"] is False
    assert options["http_options"]["retry_options"] == {"attempts": 1}
    assert options["http_options"]["base_url"] == "https://generativelanguage.googleapis.com"
    assert options["http_options"]["async_client_args"] == {
        "trust_env": False,
        "follow_redirects": False,
    }


def test_daily_quota_uses_structured_ids_and_bounds_untrusted_nesting() -> None:
    assert daily_quota(
        {"error": {"details": [{"violations": [{"quotaId": "GenerateRequestsPerDayPerProject"}]}]}}
    )
    assert daily_quota({"quota_id": "requests_per_day"})
    assert not daily_quota({"message": "perday", "quotaId": 123})
    assert not daily_quota({"quotaId": "perday"}, depth=7)
    assert not daily_quota([])


@pytest.mark.asyncio
async def test_daily_provider_quota_is_distinct_from_temporary_rate_limit(
    sdk: tuple[genai.Client, list[httpx2.Request], list[httpx2.Response | Exception]],
) -> None:
    client, requests, responses = sdk
    responses[0] = httpx2.Response(
        429, json={"error": {"details": [{"violations": [{"quotaId": "GenerateRequestsPerDay"}]}]}}
    )
    with pytest.raises(ProviderFailure) as caught:
        await GeminiJSON("key", MODEL, client=client).generate("prompt", ANSWER_SCHEMA)
    assert caught.value.code is ProviderCode.QUOTA_EXHAUSTED and len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "document",
    [
        {"answer": " ", "confidence": "low", "citations": []},
        {"answer": "x" * 65_537, "confidence": "low", "citations": []},
        {"answer": "Answer", "confidence": "unknown", "citations": []},
        {
            "answer": "Answer",
            "confidence": "low",
            "citations": [{"retrieval_unit_id": "id", "quote": "x" * 361}],
        },
        {"unexpected": "shape"},
    ],
)
async def test_answer_schema_is_enforced_locally(
    sdk: tuple[genai.Client, list[httpx2.Request], list[httpx2.Response | Exception]],
    document: dict[str, object],
) -> None:
    client, _requests, responses = sdk
    responses[0] = httpx2.Response(200, json=result(output_text=json.dumps(document)))
    with pytest.raises(ProviderFailure, match="PROVIDER_INVALID_OUTPUT"):
        await GeminiGeneration(GeminiJSON("key", MODEL, client=client)).generate("Question", ())


@pytest.mark.asyncio
async def test_valid_citation_and_absent_usage_are_preserved(
    sdk: tuple[genai.Client, list[httpx2.Request], list[httpx2.Response | Exception]],
) -> None:
    client, _requests, responses = sdk
    responses[0] = httpx2.Response(
        200,
        json=result(
            usage={},
            output_text=json.dumps(
                {
                    "answer": "Answer",
                    "confidence": "high",
                    "citations": [{"retrieval_unit_id": "id", "quote": "exact quote"}],
                }
            ),
        ),
    )
    provider = GeminiJSON("key", MODEL, client=client)
    answer = await GeminiGeneration(provider).generate("Question", ())
    assert answer.citations[0].quote == "exact quote" and provider.usage is None
    with pytest.raises(ProviderFailure, match="PROVIDER_INPUT_LIMIT"):
        await provider.generate("x" * 131_073, ANSWER_SCHEMA)
    assert provider.usage is None and provider.observed_model is None


@pytest.mark.asyncio
async def test_unexpected_sdk_return_shape_fails_closed(
    sdk: tuple[genai.Client, list[httpx2.Request], list[httpx2.Response | Exception]],
) -> None:
    client, _requests, _responses = sdk
    with (
        patch.object(client.aio.interactions, "create", AsyncMock(return_value=object())),
        pytest.raises(ProviderFailure, match="PROVIDER_INVALID_OUTPUT"),
    ):
        await GeminiJSON("key", MODEL, client=client).generate("prompt", ANSWER_SCHEMA)
