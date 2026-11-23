"""Workspace answer drafts use bounded structured output through the existing adapter."""

import json

import httpx
import pytest

from galaxy_frog.adapters.generation.ollama import OllamaGenerationProvider
from galaxy_frog.domain.generation.workspace_answer import (
    WorkspaceAnswerDraftInvalid,
    WorkspaceAnswerPassage,
)


@pytest.mark.asyncio
async def test_workspace_ollama_transport_requests_blocks_and_returns_observed_metrics() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/generate"
        body = json.loads(request.content)
        assert body["format"]["required"] == ["blocks"]
        assert body["stream"] is False
        assert '"reference": "E1"' in body["prompt"]
        assert "passages" in body["prompt"]
        return httpx.Response(
            200,
            json={
                "model": "fixture:latest",
                "done": True,
                "response": json.dumps(
                    {
                        "blocks": [
                            {
                                "text": "The seed is green.",
                                "citations": [
                                    {"reference": "E1", "quote": "green seed"},
                                ],
                            }
                        ]
                    }
                ),
                "eval_count": 19,
                "eval_duration": 1_000_000,
                "prompt_eval_count": 70,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = OllamaGenerationProvider(
            base_url="http://localhost:11434", model="fixture", client=client
        )
        draft = await provider.generate_workspace_answer(
            "What colour is the seed?", (WorkspaceAnswerPassage("E1", "document", "A green seed."),)
        )
    assert draft.model == "fixture:latest"
    assert draft.metrics.eval_count == 19
    assert draft.metrics.load_duration is None
    assert draft.draft.blocks[0].citations[0].quote == "green seed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "draft"),
    [
        ("different:latest", '{"blocks":[]}'),
        ("fixture:latest", '{"blocks":[{"text":"Uncited","citations":[]}]}'),
    ],
)
async def test_workspace_ollama_rejects_invalid_draft_or_replacement_model(
    model: str, draft: str
) -> None:
    response = {"model": model, "done": True, "response": draft}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=response))
    ) as client:
        provider = OllamaGenerationProvider(
            base_url="http://localhost:11434", model="fixture:latest", client=client
        )
        with pytest.raises(WorkspaceAnswerDraftInvalid):
            await provider.generate_workspace_answer("Question", ())
