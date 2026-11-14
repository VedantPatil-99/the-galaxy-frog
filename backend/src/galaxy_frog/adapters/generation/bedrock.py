"""Grounded text generation through the in-region Bedrock Converse API."""

import json

import httpx
from pydantic import BaseModel, Field, ValidationError

from galaxy_frog.adapters.generation.ollama import grounded_draft, moment_prompt, question_prompt
from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure
from galaxy_frog.application.provider_registry import BEDROCK_MODEL
from galaxy_frog.domain.generation.models import GenerationDraft, GroundingText
from galaxy_frog.domain.retrieval.models import RetrievedEvidence


class _Content(BaseModel):
    text: str = Field(max_length=32_768)


class _Message(BaseModel):
    content: list[_Content] = Field(min_length=1, max_length=8)


class _Output(BaseModel):
    message: _Message


class _Usage(BaseModel):
    inputTokens: int = Field(ge=0)
    outputTokens: int = Field(ge=0)


class _Converse(BaseModel):
    output: _Output
    stopReason: str
    usage: _Usage | None = None


class BedrockGeneration:
    def __init__(self, region: str, token: str, client: httpx.AsyncClient) -> None:
        self._url = f"https://bedrock-runtime.{region}.amazonaws.com/model/{BEDROCK_MODEL}/converse"
        self._token, self._client = token, client
        self.usage: dict[str, int] | None = None

    async def generate(
        self, question: str, evidence: tuple[RetrievedEvidence, ...]
    ) -> GenerationDraft:
        return await self._generate(question_prompt(question, evidence))

    async def generate_moment(
        self, question: str, evidence: tuple[GroundingText, ...]
    ) -> GenerationDraft:
        return await self._generate(moment_prompt(question, evidence))

    async def _generate(self, prompt: str) -> GenerationDraft:
        if len(prompt.encode()) > 65_536:
            raise ProviderFailure(ProviderCode.INPUT_LIMIT)
        try:
            async with self._client.stream(
                "POST",
                self._url,
                headers={"Authorization": f"Bearer {self._token}"},
                json={
                    "messages": [{"role": "user", "content": [{"text": prompt}]}],
                    "inferenceConfig": {"maxTokens": 1024, "temperature": 0},
                },
            ) as response:
                if response.status_code in {401, 403}:
                    raise ProviderFailure(ProviderCode.AUTH_FAILED)
                if response.status_code == 429:
                    raise ProviderFailure(ProviderCode.RATE_LIMITED)
                if response.status_code in {408, 424} or response.status_code >= 500:
                    raise ProviderFailure(ProviderCode.OUTCOME_UNKNOWN)
                if response.status_code != 200:
                    raise ProviderFailure(ProviderCode.UNAVAILABLE)
                data = bytearray()
                async for part in response.aiter_bytes():
                    data.extend(part)
                    if len(data) > 65_536:
                        raise ProviderFailure(ProviderCode.INVALID_OUTPUT)
        except httpx.RequestError as exc:
            raise ProviderFailure(ProviderCode.OUTCOME_UNKNOWN) from exc
        try:
            payload = _Converse.model_validate_json(data)
            if payload.usage is not None:
                self.usage = {
                    "input_tokens": payload.usage.inputTokens,
                    "output_tokens": payload.usage.outputTokens,
                }
            if payload.stopReason in {"guardrail_intervened", "content_filtered"}:
                raise ProviderFailure(ProviderCode.SAFETY_BLOCKED)
            if payload.stopReason != "end_turn":
                raise ProviderFailure(ProviderCode.INVALID_OUTPUT)
            output = "".join(part.text for part in payload.output.message.content).strip()
            if output.startswith("```json\n") and output.endswith("```"):
                output = output[8:-3].strip()
            return grounded_draft(json.loads(output))
        except (ValueError, KeyError, TypeError, ValidationError) as exc:
            raise ProviderFailure(ProviderCode.INVALID_OUTPUT) from exc
