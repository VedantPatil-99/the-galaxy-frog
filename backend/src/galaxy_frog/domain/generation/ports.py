"""Provider protocol for evidence-constrained generation."""

from typing import Protocol

from galaxy_frog.domain.generation.models import GenerationDraft
from galaxy_frog.domain.retrieval.models import RetrievedEvidence


class GenerationProvider(Protocol):
    """Generate a structured draft from an explicit evidence set."""

    async def generate(
        self,
        question: str,
        evidence: tuple[RetrievedEvidence, ...],
    ) -> GenerationDraft: ...
