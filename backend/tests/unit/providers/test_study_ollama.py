"""Exercise the local-only provider boundary with an HTTP transport."""

import httpx
import pytest

from galaxy_frog.adapters.generation.study_ollama import OllamaStudyGenerator
from galaxy_frog.domain.study import StudyError, StudyKind


@pytest.mark.asyncio
async def test_study_provider_submits_structured_drafting_and_reports_actual_model() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("http://127.0.0.1:11434/api/generate")
        assert b"exact supplied ID" in request.content
        assert b"correct_index" in request.content
        return httpx.Response(200, json={"model": "fixture:latest", "response": '{"items":[]}'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = OllamaStudyGenerator("http://127.0.0.1:11434/", "fixture", client)
        result = await provider.draft(StudyKind.QUIZ, ())
        assert result.items == () and result.model == "fixture:latest"


@pytest.mark.asyncio
async def test_study_provider_rejects_remote_and_malformed_responses() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"response": "bad"}))
    ) as client:
        with pytest.raises(StudyError, match="loopback"):
            await OllamaStudyGenerator("https://remote.example", "fixture", client).draft(
                StudyKind.NOTES, ()
            )
        with pytest.raises(StudyError, match="invalid response"):
            await OllamaStudyGenerator("http://localhost", "fixture", client).draft(
                StudyKind.NOTES, ()
            )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"model": "ok", "response": "bad"})
        )
    ) as client:
        draft = await OllamaStudyGenerator("http://localhost", "fixture", client).draft(
            StudyKind.NOTES, ()
        )
        assert draft.items == (None,)


@pytest.mark.asyncio
async def test_study_provider_fails_safely_on_unavailability() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(503, text="internal detail"))
    ) as client:
        with pytest.raises(StudyError, match="did not respond") as exc:
            await OllamaStudyGenerator("http://[::1]", "fixture", client).draft(
                StudyKind.FLASHCARDS, ()
            )
        assert exc.value.failure.retryable
        assert "internal detail" not in str(exc.value)
