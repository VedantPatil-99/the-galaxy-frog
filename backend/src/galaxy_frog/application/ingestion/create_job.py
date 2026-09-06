"""Create one canonical durable ingestion job without starting expensive work."""

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256

from galaxy_frog.application.ingestion.ports import IngestionRepository
from galaxy_frog.domain.ingestion.models import IngestionJob
from galaxy_frog.domain.videos.models import SourceReference
from galaxy_frog.domain.videos.source import VideoSource, VideoSourceError, VideoSourceErrorCode


def ingestion_input_fingerprint(
    source: SourceReference,
    *,
    pipeline_revision: str,
    preferred_languages: Sequence[str],
) -> str:
    """Hash every input that changes the logical output of a Phase 2 import."""

    revision = pipeline_revision.strip()
    languages = tuple(language.strip().casefold() for language in preferred_languages)
    if not revision or not languages or any(not language for language in languages):
        raise ValueError("pipeline revision and preferred languages must not be empty")
    identity = "\x1f".join(
        (
            source.kind,
            source.external_id,
            source.canonical_url,
            revision,
            *languages,
        )
    )
    return sha256(identity.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class CreateIngestionJobResult:
    """Durable job plus whether this request created its canonical row."""

    job: IngestionJob
    created: bool


class CreateIngestionJob:
    """Resolve a source identity and persist a queued job idempotently."""

    def __init__(
        self,
        *,
        sources: Sequence[VideoSource],
        repository: IngestionRepository,
        pipeline_revision: str = "phase-2-v1",
        preferred_languages: Sequence[str] = ("en",),
    ) -> None:
        self._sources = tuple(sources)
        self._repository = repository
        self._pipeline_revision = pipeline_revision
        self._preferred_languages = tuple(preferred_languages)

    async def execute(self, locator: str) -> CreateIngestionJobResult:
        reference = self._resolve_source(locator)
        fingerprint = ingestion_input_fingerprint(
            reference,
            pipeline_revision=self._pipeline_revision,
            preferred_languages=self._preferred_languages,
        )
        job, created = await self._repository.create_or_get(reference, fingerprint)
        return CreateIngestionJobResult(job=job, created=created)

    def _resolve_source(self, locator: str) -> SourceReference:
        for source in self._sources:
            try:
                return source.canonicalize(locator)
            except VideoSourceError as exc:
                if exc.code is not VideoSourceErrorCode.INVALID_SOURCE:
                    raise
        raise VideoSourceError(
            VideoSourceErrorCode.INVALID_SOURCE,
            "The video source is not supported.",
        )
