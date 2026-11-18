"""Stateless, single-dispatch Gemini text generation with bounded JSON output."""

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Literal, cast

from galaxy_frog.adapters.generation.ollama import (
    grounded_draft,
    moment_prompt,
    question_prompt,
)
from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure, retry_after_at
from galaxy_frog.domain.generation.models import GenerationDraft, GroundingText
from galaxy_frog.domain.providers import GEMINI_TEXT_MODELS
from galaxy_frog.domain.retrieval.models import RetrievedEvidence

if TYPE_CHECKING:
    from google import genai

type ThinkingLevel = Literal["low", "medium", "high"]
type BeforeDispatch = Callable[[int], Awaitable[None]]

ANSWER_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "minLength": 1, "maxLength": 65_536},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "citations": {
            "type": "array",
            "maxItems": 24,
            "items": {
                "type": "object",
                "properties": {
                    "retrieval_unit_id": {"type": "string", "minLength": 1, "maxLength": 72},
                    "quote": {"type": "string", "minLength": 1, "maxLength": 360},
                },
                "required": ["retrieval_unit_id", "quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["answer", "confidence", "citations"],
    "additionalProperties": False,
}


def sdk_failure(error: Exception) -> ProviderFailure:
    """Classify failures without copying SDK messages, bodies, or credentials."""
    status = getattr(error, "status_code", None)
    if status in {401, 403}:
        return ProviderFailure(ProviderCode.AUTH_FAILED)
    if status == 429:
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", {})
        retry = (
            cast(Mapping[str, object], headers).get("retry-after")
            if isinstance(headers, Mapping)
            else None
        )
        return ProviderFailure(
            ProviderCode.RATE_LIMITED,
            retry_at=retry_after_at(retry if isinstance(retry, str) else None),
        )
    if status in {400, 413, 422}:
        return ProviderFailure(ProviderCode.INVALID_INPUT)
    if status == 404:
        return ProviderFailure(ProviderCode.SETUP_REQUIRED)
    return ProviderFailure(ProviderCode.OUTCOME_UNKNOWN)


class GeminiJSON:
    """An explicit model code; no stored conversation, tools, retries, or continuation."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        revision: str | None = None,
        thinking: ThinkingLevel = "low",
        max_output_tokens: int = 4096,
        timeout_seconds: float = 75,
        before_dispatch: BeforeDispatch | None = None,
        client: genai.Client | None = None,
    ) -> None:
        if not api_key.strip() or model not in GEMINI_TEXT_MODELS:
            raise ProviderFailure(ProviderCode.SETUP_REQUIRED)
        if not 256 <= max_output_tokens <= 16_384 or not 0 < timeout_seconds <= 120:
            raise ValueError("Gemini requests require bounded output and timeout settings.")
        self._key, self.model, self._revision = api_key, model, revision
        self._thinking, self._output, self._timeout = thinking, max_output_tokens, timeout_seconds
        self._before_dispatch, self._client = before_dispatch, client
        self.usage: dict[str, int] | None = None
        self.observed_model: str | None = None

    async def generate(self, prompt: str, schema: dict[str, object]) -> object:
        if len(prompt.encode("utf-8")) > 131_072:
            raise ProviderFailure(ProviderCode.INPUT_LIMIT)
        self.usage = None
        # UTF-8 bytes are a conservative input-token reservation, not a usage
        # measurement. Reserve before SDK/client construction and every dispatch.
        if self._before_dispatch:
            await self._before_dispatch(
                len(prompt.encode("utf-8")) + len(json.dumps(schema).encode("utf-8")) + 1024
            )
        from google import genai

        client = self._client or genai.Client(
            api_key=self._key,
            vertexai=False,
            http_options={
                "api_version": "v1",
                "base_url": "https://generativelanguage.googleapis.com",
                "timeout": int(self._timeout * 1000),
                "retry_options": {"attempts": 1},
                "async_client_args": {"trust_env": False, "follow_redirects": False},
            },
        )
        try:
            try:
                result = await client.aio.interactions.create(
                    model=self.model,
                    input=prompt,
                    store=False,
                    automatic_continuation=False,
                    response_format={
                        "type": "text",
                        "mime_type": "application/json",
                        "schema": schema,
                    },
                    generation_config={
                        "thinking_level": self._thinking,
                        "max_output_tokens": self._output,
                    },
                    timeout=self._timeout,
                )
            except Exception as exc:
                raise sdk_failure(exc) from None
            from google.genai.interactions import Interaction

            if not isinstance(result, Interaction):
                raise ProviderFailure(ProviderCode.INVALID_OUTPUT)
            if result.usage is not None:
                counts = {
                    "input_tokens": result.usage.total_input_tokens,
                    "output_tokens": result.usage.total_output_tokens,
                    "thinking_tokens": result.usage.total_thought_tokens,
                    "cached_input_tokens": result.usage.total_cached_tokens,
                }
                self.usage = {
                    key: value for key, value in counts.items() if value is not None
                } or None
                if any(not 0 <= value <= 10_000_000 for value in (self.usage or {}).values()):
                    raise ProviderFailure(ProviderCode.INVALID_OUTPUT)
            if result.errors and any(
                any(
                    word in (error.code or "").lower() for word in ("safety", "block", "prohibited")
                )
                for error in result.errors
            ):
                raise ProviderFailure(ProviderCode.SAFETY_BLOCKED)
            if result.status != "completed":
                raise ProviderFailure(ProviderCode.INVALID_OUTPUT)
            # Interactions exposes a model code, not a weights revision. Never
            # present it as an independently verified immutable weights digest.
            if result.model != (self._revision or self.model):
                raise ProviderFailure(ProviderCode.INCOMPATIBLE)
            self.observed_model = result.model
            text = result.output_text
            if not text or len(text.encode("utf-8")) > 262_144:
                raise ProviderFailure(ProviderCode.INVALID_OUTPUT)
            try:
                return json.loads(text)
            except ValueError:
                raise ProviderFailure(ProviderCode.INVALID_OUTPUT) from None
        finally:
            if self._client is None:
                await client.aio.aclose()


class GeminiGeneration:
    def __init__(self, provider: GeminiJSON) -> None:
        self.provider = provider

    async def generate(
        self, question: str, evidence: tuple[RetrievedEvidence, ...]
    ) -> GenerationDraft:
        return await self._draft(question_prompt(question, evidence))

    async def generate_moment(
        self, question: str, evidence: tuple[GroundingText, ...]
    ) -> GenerationDraft:
        return await self._draft(moment_prompt(question, evidence))

    async def _draft(self, prompt: str) -> GenerationDraft:
        try:
            return grounded_draft(await self.provider.generate(prompt, ANSWER_SCHEMA))
        except ValueError, KeyError, TypeError:
            raise ProviderFailure(ProviderCode.INVALID_OUTPUT) from None
