"""Video-scoped study artifacts, durable runs, and version exports."""

from collections.abc import Awaitable
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError

from galaxy_frog.api.dependencies import get_video_repository
from galaxy_frog.api.errors import ApiError
from galaxy_frog.api.schemas import ErrorResponse
from galaxy_frog.application.study_export import to_markdown
from galaxy_frog.db.study_repository import StudyRepository
from galaxy_frog.db.video_repository import SqlAlchemyVideoRepository
from galaxy_frog.domain.study import (
    StudyError,
    StudyKind,
    StudyRun,
    StudyVersion,
    StudyVersionSummary,
)

router = APIRouter(prefix="/v1", tags=["study"])
_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponse} for status in (404, 409, 422, 503)
}


class ChapterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID


class StudyArtifactRequest(ChapterRequest):
    kind: Literal["summary", "notes", "flashcards", "quiz"]
    chapter_version_id: UUID


class RegenerateRequest(ChapterRequest):
    chapter_version_id: UUID | None = None


class StudyIndex(BaseModel):
    latest: tuple[StudyVersionSummary, ...]
    runs: tuple[StudyRun, ...]
    versions: tuple[StudyVersionSummary, ...] = ()
    next_before_version: int | None = None


def get_study_repository(
    videos: Annotated[SqlAlchemyVideoRepository, Depends(get_video_repository)],
) -> StudyRepository:
    return StudyRepository(videos.session)


Repository = Annotated[StudyRepository, Depends(get_study_repository)]


async def _safe[T](operation: Awaitable[T]) -> T:
    try:
        return await operation
    except StudyError as exc:
        failure = exc.failure
        raise ApiError(
            status_code=exc.status,
            code=failure.code,
            message=failure.message,
            retryable=failure.retryable,
            suggested_action=failure.suggested_action,
        ) from exc
    except SQLAlchemyError as exc:
        raise ApiError(
            status_code=503,
            code="DATABASE_UNAVAILABLE",
            message="Study materials require a working PostgreSQL database.",
            retryable=True,
            suggested_action="Start PostgreSQL and apply the current migrations.",
        ) from exc


@router.post("/videos/{video_id}/chapters", status_code=202, responses=_ERRORS)
async def start_chapters(video_id: UUID, body: ChapterRequest, repository: Repository) -> StudyRun:
    return await _safe(repository.enqueue(video_id, StudyKind.CHAPTERS, body.request_id))


@router.post("/videos/{video_id}/artifacts", status_code=202, responses=_ERRORS)
async def start_artifact(
    video_id: UUID, body: StudyArtifactRequest, repository: Repository
) -> StudyRun:
    return await _safe(
        repository.enqueue(video_id, StudyKind(body.kind), body.request_id, body.chapter_version_id)
    )


@router.get("/videos/{video_id}/artifacts", responses=_ERRORS)
async def list_artifacts(
    video_id: UUID,
    repository: Repository,
    kind: StudyKind | None = None,
    before_version: Annotated[int | None, Query(gt=0)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> StudyIndex:
    await _safe(repository.require_video(video_id))
    latest = await _safe(repository.latest_versions(video_id))
    runs = await _safe(repository.latest_runs(video_id))
    versions, cursor = (
        await _safe(repository.history(video_id, kind, before_version, limit))
        if kind is not None
        else ((), None)
    )
    return StudyIndex(latest=latest, runs=runs, versions=versions, next_before_version=cursor)


@router.get("/videos/{video_id}/artifacts/{artifact_id}", responses=_ERRORS)
async def read_artifact(video_id: UUID, artifact_id: UUID, repository: Repository) -> StudyVersion:
    return await _safe(repository.artifact(video_id, artifact_id))


@router.post("/artifacts/{artifact_id}/regenerate", status_code=202, responses=_ERRORS)
async def regenerate_artifact(
    artifact_id: UUID, body: RegenerateRequest, repository: Repository
) -> StudyRun:
    return await _safe(repository.regenerate(artifact_id, body.request_id, body.chapter_version_id))


@router.get("/videos/{video_id}/artifact-runs/{run_id}", responses=_ERRORS)
async def read_artifact_run(video_id: UUID, run_id: UUID, repository: Repository) -> StudyRun:
    return await _safe(repository.run(video_id, run_id))


@router.get(
    "/videos/{video_id}/artifacts/{artifact_id}/export",
    response_model=StudyVersion,
    responses={
        **_ERRORS,
        200: {
            "content": {"text/markdown": {"schema": {"type": "string"}}},
            "headers": {"Content-Disposition": {"schema": {"type": "string"}}},
        },
    },
)
async def export_artifact(
    video_id: UUID, artifact_id: UUID, format: Literal["markdown", "json"], repository: Repository
) -> Response:
    version = await _safe(repository.artifact(video_id, artifact_id))
    suffix = "md" if format == "markdown" else "json"
    return Response(
        to_markdown(version) if format == "markdown" else version.model_dump_json(),
        media_type="text/markdown" if format == "markdown" else "application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="galaxy-frog-{video_id}-{version.kind.value}-v{version.version}.{suffix}"'
            ),
            "Cache-Control": "no-store",
        },
    )
