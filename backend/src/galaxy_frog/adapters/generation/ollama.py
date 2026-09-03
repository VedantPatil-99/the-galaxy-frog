"""Structured grounded-answer generation through a user-managed Ollama service."""

import json
from collections.abc import Mapping
from typing import cast

import httpx

from galaxy_frog.domain.generation.models import (
    AnswerConfidence,
    GenerationCitation,
    GenerationDraft,
)
from galaxy_frog.domain.retrieval.models import RetrievedEvidence


class GenerationProviderError(RuntimeError):
    """Safe provider failure that never exposes response internals."""


class OllamaGenerationProvider:
    """Ask a configured local Qwen model for evidence-bound JSON output."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = client

    async def generate(
        self,
        question: str,
        evidence: tuple[RetrievedEvidence, ...],
    ) -> GenerationDraft:
        prompt = self._prompt(question, evidence)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=90)
        try:
            response = await client.post(
                f"{self._base_url}/api/generate",
                json={
                    "model": self._model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "think": False,
                    "options": {"temperature": 0},
                },
            )
            response.raise_for_status()
            payload: object = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise GenerationProviderError(
                "The configured Ollama generation provider is unavailable."
            ) from exc
        finally:
            if owns_client:
                await client.aclose()
        if not isinstance(payload, Mapping):
            raise GenerationProviderError("Ollama returned an invalid generation response.")
        payload_mapping = cast(Mapping[str, object], payload)
        response_value = payload_mapping.get("response")
        if not isinstance(response_value, str):
            raise GenerationProviderError("Ollama returned an invalid generation response.")
        try:
            document: object = json.loads(response_value)
            return self._parse_draft(document)
        except (ValueError, KeyError, TypeError) as exc:
            raise GenerationProviderError(
                "Ollama returned an invalid grounded-answer document."
            ) from exc

    @staticmethod
    def _prompt(question: str, evidence: tuple[RetrievedEvidence, ...]) -> str:
        blocks = "\n\n".join(
            f"ID: {item.unit.unit_id}\nINTERVAL_MS: {item.unit.start_ms}-{item.unit.end_ms}\nTEXT: {item.unit.text}"
            for item in evidence
        )
        return (
            "Answer only from the supplied transcript evidence. Return one JSON object with "
            'keys "answer", "confidence" (low, medium, or high), and "citations". Each citation '
            'must contain "retrieval_unit_id" and an exact contiguous "quote" copied from that '
            "unit. If evidence is insufficient, say so and return an empty citations list.\n\n"
            f"QUESTION:\n{question}\n\nEVIDENCE:\n{blocks}"
        )

    @staticmethod
    def _parse_draft(value: object) -> GenerationDraft:
        if not isinstance(value, Mapping):
            raise ValueError("draft must be an object")
        mapping = cast(Mapping[str, object], value)
        answer = mapping["answer"]
        confidence = mapping["confidence"]
        citations_value = mapping["citations"]
        if not isinstance(answer, str) or not answer.strip() or not isinstance(confidence, str):
            raise ValueError("draft answer and confidence are invalid")
        if not isinstance(citations_value, list):
            raise ValueError("draft citations must be a list")
        citations: list[GenerationCitation] = []
        for item in cast(list[object], citations_value):
            if not isinstance(item, Mapping):
                raise ValueError("citation must be an object")
            item_mapping = cast(Mapping[str, object], item)
            unit_id = item_mapping.get("retrieval_unit_id")
            quote = item_mapping.get("quote")
            if not isinstance(unit_id, str) or not isinstance(quote, str):
                raise ValueError("citation fields are invalid")
            citations.append(GenerationCitation(unit_id, quote))
        return GenerationDraft(
            answer=answer.strip(),
            confidence=AnswerConfidence(confidence),
            citations=tuple(citations),
        )
