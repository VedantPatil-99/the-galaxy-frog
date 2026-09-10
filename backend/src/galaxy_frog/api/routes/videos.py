"""Transcript-first video import and read endpoints."""

from collections.abc import Callable
from http import HTTPStatus
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from galaxy_frog.adapters.embeddings.ollama import EmbeddingProviderError
from galaxy_frog.adapters.generation.ollama import GenerationProviderError
from galaxy_frog.adapters.video_sources.youtube import YouTubeSource
from galaxy_frog.api.dependencies import (
    get_ingestion_repository,
    get_job_dispatcher,
    get_video_repository,
)
from galaxy_frog.api.dispatching import dispatch_ingestion_job
from galaxy_frog.api.errors import ApiError
from galaxy_frog.api.job_schemas import ingestion_job_response
from galaxy_frog.api.schemas import ErrorResponse
from galaxy_frog.api.video_schemas import (
    AnswerResponse,
    EvidenceResponse,
    ImportVideoRequest,
    ImportVideoResponse,
    QuestionRequest,
    RetrievalUnitResponse,
    TranscriptCueResponse,
    TranscriptionRunResponse,
    TranscriptResponse,
    VideoResponse,
)
from galaxy_frog.application.ingestion.create_job import CreateIngestionJob
from galaxy_frog.application.ingestion.dispatch import JobDispatcher
from galaxy_frog.application.questions.answer_question import (
    AnswerQuestion,
    CitationValidationError,
)
from galaxy_frog.db.ingestion_repository import PostgresIngestionRepository
from galaxy_frog.db.transcript_search import PgVectorTranscriptSearch, TranscriptIndexError
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.domain.generation.ports import GenerationProvider
from galaxy_frog.domain.retrieval.ports import TextEmbeddingProvider
from galaxy_frog.domain.videos.records import TranscriptRecord, VideoRecord
from galaxy_frog.domain.videos.source import VideoSource, VideoSourceError, VideoSourceErrorCode

router = APIRouter(prefix="/v1/videos", tags=["videos"])

RepositoryDependency = Annotated[SqlAlchemyVideoRepository, Depends(get_video_repository)]
IngestionRepositoryDependency = Annotated[
    PostgresIngestionRepository,
    Depends(get_ingestion_repository),
]
DispatcherDependency = Annotated[JobDispatcher, Depends(get_job_dispatcher)]


def _transcript_search(
    request: Request,
    repository: SqlAlchemyVideoRepository,
) -> PgVectorTranscriptSearch:
    provider_factory = cast(
        Callable[[], TextEmbeddingProvider], request.app.state.embedding_provider_factory
    )
    provider = provider_factory()
    return PgVectorTranscriptSearch(
        session=repository.session,
        videos=repository,
        provider=provider,
    )


def _video_response(video: VideoRecord, *, index_ready: bool) -> VideoResponse:
    metadata = video.metadata
    return VideoResponse(
        video_id=video.video_id,
        source_kind=metadata.reference.kind,
        external_id=metadata.reference.external_id,
        canonical_url=metadata.reference.canonical_url,
        title=metadata.title,
        duration_ms=metadata.duration_ms,
        channel_name=metadata.channel_name,
        thumbnail_url=metadata.thumbnail_url,
        index_ready=index_ready,
    )


def _transcript_response(record: TranscriptRecord, *, index_ready: bool) -> TranscriptResponse:
    transcription = record.transcription
    return TranscriptResponse(
        video=_video_response(record.video, index_ready=index_ready),
        cues=[
            TranscriptCueResponse(
                cue_id=cue.cue_id,
                source_order=cue.source_order,
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                text=cue.text,
                language_code=cue.language_code,
                origin=cue.origin.value,
                track_id=cue.track_id,
                caption_kind=cue.caption_kind.value if cue.caption_kind is not None else None,
                transcription_run_id=cue.transcription_run_id,
                confidence=cue.confidence,
                confidence_method=cue.confidence_method,
            )
            for cue in record.cues
        ],
        retrieval_units=[
            RetrievalUnitResponse(
                retrieval_unit_id=unit.unit_id,
                start_ms=unit.start_ms,
                end_ms=unit.end_ms,
                text=unit.text,
                cue_ids=list(unit.cue_ids),
            )
            for unit in record.units
        ],
        transcription=(
            TranscriptionRunResponse(
                run_id=transcription.run_id,
                job_id=transcription.result.job_id,
                audio_asset_id=transcription.audio_asset_id,
                audio_attempt=transcription.result.attempt,
                fallback_reason=transcription.result.fallback_reason.value,
                audio_start_ms=transcription.result.audio_start_ms,
                audio_end_ms=transcription.result.audio_end_ms,
                provider=transcription.result.spec.provider,
                provider_revision=transcription.result.spec.provider_revision,
                model=transcription.result.spec.model,
                model_revision=transcription.result.spec.model_revision,
                device=transcription.result.spec.device.value,
                compute_type=transcription.result.spec.compute_type.value,
                language_code=transcription.result.language_code,
                language_confidence=transcription.result.language_confidence,
                language_confidence_method=transcription.result.language_confidence_method,
                processing_seconds=transcription.result.processing_seconds,
                transcribed_at=transcription.result.transcribed_at,
            )
            if transcription is not None
            else None
        ),
    )


def _source_error(error: VideoSourceError) -> ApiError:
    unavailable = error.code in {
        VideoSourceErrorCode.SOURCE_UNAVAILABLE,
        VideoSourceErrorCode.SOURCE_AUTH_REQUIRED,
    }
    status = HTTPStatus.SERVICE_UNAVAILABLE if unavailable else HTTPStatus.UNPROCESSABLE_ENTITY
    return ApiError(
        status_code=status,
        code=error.code,
        message=error.message,
        retryable=error.retryable,
        suggested_action=(
            "Retry later or choose another public captioned video."
            if unavailable
            else "Provide a supported public YouTube video with captions."
        ),
    )


@router.post(
    "/import",
    response_model=ImportVideoResponse,
    status_code=HTTPStatus.ACCEPTED,
    responses={422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def import_video(
    body: ImportVideoRequest,
    request: Request,
    repository: IngestionRepositoryDependency,
    dispatcher: DispatcherDependency,
) -> ImportVideoResponse:
    """Create or reuse a durable job without running provider work in the request."""

    sources = cast(
        tuple[VideoSource, ...], getattr(request.app.state, "video_sources", (YouTubeSource(),))
    )
    service = CreateIngestionJob(sources=sources, repository=repository)
    try:
        result = await service.execute(str(body.source_url))
    except VideoSourceError as exc:
        raise _source_error(exc) from exc
    await dispatch_ingestion_job(dispatcher, result.job.job_id)
    return ImportVideoResponse(
        job=ingestion_job_response(result.job),
        reused=not result.created,
    )


@router.get(
    "/{video_id}",
    response_model=VideoResponse,
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def get_video(
    video_id: UUID,
    request: Request,
    repository: RepositoryDependency,
) -> VideoResponse:
    """Return safe canonical metadata for one imported video."""

    video = await repository.get_video(video_id)
    if video is None:
        raise ApiError(
            status_code=HTTPStatus.NOT_FOUND,
            code="VIDEO_NOT_FOUND",
            message="The requested video does not exist.",
            retryable=False,
        )
    index_ready = await _transcript_search(request, repository).is_indexed(video_id)
    return _video_response(video, index_ready=index_ready)


@router.get(
    "/{video_id}/transcript",
    response_model=TranscriptResponse,
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def get_transcript(
    video_id: UUID,
    request: Request,
    repository: RepositoryDependency,
) -> TranscriptResponse:
    """Return exact cues and retrieval units with complete provenance."""

    transcript = await repository.get_transcript(video_id)
    if transcript is None:
        raise ApiError(
            status_code=HTTPStatus.NOT_FOUND,
            code="VIDEO_NOT_FOUND",
            message="The requested video does not exist.",
            retryable=False,
        )
    index_ready = await _transcript_search(request, repository).is_indexed(video_id)
    return _transcript_response(transcript, index_ready=index_ready)


@router.post(
    "/{video_id}/questions",
    response_model=AnswerResponse,
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def ask_question(
    video_id: UUID,
    body: QuestionRequest,
    request: Request,
    repository: RepositoryDependency,
) -> AnswerResponse:
    """Retrieve transcript evidence and return a deterministically validated answer."""

    if await repository.get_video(video_id) is None:
        raise ApiError(
            status_code=HTTPStatus.NOT_FOUND,
            code="VIDEO_NOT_FOUND",
            message="The requested video does not exist.",
            retryable=False,
        )
    provider_factory = cast(
        Callable[[], GenerationProvider], request.app.state.generation_provider_factory
    )
    service = AnswerQuestion(
        search=_transcript_search(request, repository),
        provider=provider_factory(),
    )
    try:
        answer = await service.execute(video_id, body.question)
    except EmbeddingProviderError as exc:
        raise ApiError(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            code="EMBEDDING_UNAVAILABLE",
            message="Transcript retrieval is temporarily unavailable.",
            retryable=True,
            suggested_action="Start Ollama with the configured BGE-M3 model and retry.",
        ) from exc
    except GenerationProviderError as exc:
        raise ApiError(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            code="GENERATION_UNAVAILABLE",
            message="Grounded answer generation is temporarily unavailable.",
            retryable=True,
            suggested_action="Start Ollama with the configured generation model and retry.",
        ) from exc
    except (CitationValidationError, TranscriptIndexError) as exc:
        raise ApiError(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            code="CITATION_VALIDATION_FAILED",
            message="The generated answer could not be supported by the retrieved transcript.",
            retryable=True,
            suggested_action="Retry the question or inspect the transcript evidence.",
        ) from exc
    return AnswerResponse(
        answer=answer.answer,
        confidence=answer.confidence.value,
        evidence=[
            EvidenceResponse(
                video_id=item.video_id,
                retrieval_unit_id=item.retrieval_unit_id,
                cue_ids=list(item.cue_ids),
                quote=item.quote,
                start_ms=item.start_ms,
                end_ms=item.end_ms,
            )
            for item in answer.evidence
        ],
        warnings=list(answer.warnings),
        degraded_mode=answer.degraded_mode,
    )
