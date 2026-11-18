"""Structured Study proposals, validated against source evidence by the application."""

from pydantic import BaseModel, Field, ValidationError

from galaxy_frog.adapters.generation.gemini import GeminiJSON
from galaxy_frog.adapters.generation.study_ollama import study_format, study_prompt
from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure
from galaxy_frog.application.study_generation import DraftBatch
from galaxy_frog.domain.study import StudyCitation, StudyKind

LIMITS = {
    StudyKind.CHAPTERS: 1,
    StudyKind.SUMMARY: 3,
    StudyKind.NOTES: 5,
    StudyKind.FLASHCARDS: 2,
    StudyKind.QUIZ: 2,
}


class _Items(BaseModel):
    items: tuple[object, ...] = Field(max_length=5)


class GeminiStudyGenerator:
    def __init__(self, provider: GeminiJSON) -> None:
        self.provider = provider

    async def draft(self, kind: StudyKind, evidence: tuple[StudyCitation, ...]) -> DraftBatch:
        prompt, aliases = study_prompt(kind, evidence, detailed=True)
        data = await self.provider.generate(prompt, study_format(kind, LIMITS[kind]))
        try:
            items = _Items.model_validate(data).items
        except ValidationError:
            raise ProviderFailure(ProviderCode.INVALID_OUTPUT) from None
        return DraftBatch(
            items,
            self.provider.observed_model or self.provider.model,
            aliases,
            "google-gemini",
            "study-gemini-v1",
        )
