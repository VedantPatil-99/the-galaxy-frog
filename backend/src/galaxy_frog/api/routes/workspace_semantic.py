"""Explicit local semantic indexing and retrieval for private notebook evidence."""

from time import perf_counter
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from galaxy_frog.adapters.embeddings.cohere import COHERE_EMBEDDING_SPEC
from galaxy_frog.adapters.embeddings.gemini import GEMINI_EMBEDDING_SPEC
from galaxy_frog.adapters.embeddings.ollama import OllamaBgeM3EmbeddingProvider
from galaxy_frog.api.errors import ApiError
from galaxy_frog.api.routes.workspace import Creator, Repository, notebook_not_found
from galaxy_frog.api.schemas import ErrorResponse
from galaxy_frog.api.workspace_auth import prevent_workspace_caching, workspace_settings
from galaxy_frog.api.workspace_schemas import (
    WorkspaceSearchHit,
    WorkspaceSearchQuery,
    workspace_search_hit,
)
from galaxy_frog.application.provider_registry import probe_ollama_models
from galaxy_frog.db.workspace_semantic import (
    EMBEDDING_PASSAGE_CHARACTERS,
    ActiveWorkspaceIndex,
    WorkspaceIndexChanged,
    WorkspaceSemanticRepository,
)
from galaxy_frog.domain.retrieval.errors import EmbeddingProviderError
from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec
from galaxy_frog.domain.retrieval.pipeline import QueryKind
from galaxy_frog.domain.retrieval.query import analyze_query
from galaxy_frog.domain.workspace import NotebookNotFound

router = APIRouter(
    prefix="/v1/workspace/notebooks",
    tags=["workspace"],
    dependencies=[Depends(prevent_workspace_caching)],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 503)},
)
_MODEL = "bge-m3:latest"


class WorkspaceSemanticStatus(BaseModel):
    active_collection: UUID | None
    active_profile: EmbeddingCollectionSpec | None
    available_profile: EmbeddingCollectionSpec | None
    cloud_profile: EmbeddingCollectionSpec | None = None
    cohere_profile: EmbeddingCollectionSpec | None = None
    indexed: int = Field(ge=0)
    total: int = Field(ge=0)


class WorkspaceSemanticBuild(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: Literal["local-bge-m3"]
    revision: str = Field(min_length=1, max_length=255)
    expected_collection: UUID | None
    replace: bool = False


class WorkspaceSemanticBatchResponse(WorkspaceSemanticStatus):
    added: int = Field(ge=0)
    prepare_ms: float = Field(ge=0)
    embedding_ms: float = Field(ge=0)
    save_ms: float = Field(ge=0)


class WorkspaceSemanticQuery(WorkspaceSearchQuery):
    model_config = ConfigDict(extra="forbid")

    collection_id: UUID


class WorkspaceSemanticResults(BaseModel):
    collection_id: UUID
    embedding_ms: float = Field(ge=0)
    query_ms: float = Field(ge=0)
    hits: tuple[WorkspaceSearchHit, ...]


async def _semantic_repository(
    notebook_id: UUID, identity: Creator, repository: Repository
) -> WorkspaceSemanticRepository:
    try:
        await repository.get_notebook(identity.account_id, notebook_id)
    except NotebookNotFound:
        raise notebook_not_found() from None
    return WorkspaceSemanticRepository(repository.session, identity.account_id, notebook_id)


SemanticRepository = Annotated[WorkspaceSemanticRepository, Depends(_semantic_repository)]


async def _available_spec(request: Request) -> EmbeddingCollectionSpec | None:
    settings = workspace_settings(request)
    if settings.embedding_model not in {"bge-m3", _MODEL}:
        return None
    installed = await probe_ollama_models(settings.ollama_base_url)
    revision = installed.get(_MODEL) or installed.get("bge-m3")
    if not revision or settings.embedding_model_revision not in {"ollama", revision}:
        return None
    return EmbeddingCollectionSpec("ollama", _MODEL, revision, 1024, "l2")


def _cloud_profile(request: Request) -> EmbeddingCollectionSpec | None:
    settings = workspace_settings(request)
    return (
        GEMINI_EMBEDDING_SPEC
        if (
            settings.workspace_gemini_embedding_enabled
            and settings.gemini_embedding_api_key is not None
            and settings.gemini_embedding_project_id is not None
        )
        else None
    )


def _cohere_profile(request: Request) -> EmbeddingCollectionSpec | None:
    return (
        COHERE_EMBEDDING_SPEC
        if workspace_settings(request).cohere_embedding_rate is not None
        else None
    )


def _changed() -> ApiError:
    return ApiError(
        status_code=409,
        code="WORKSPACE_SEMANTIC_INDEX_CHANGED",
        message="Refresh the index status and select its current model revision.",
        retryable=False,
    )


def _require_model(spec: EmbeddingCollectionSpec | None) -> EmbeddingCollectionSpec:
    if spec is None:
        raise ApiError(
            status_code=503,
            code="WORKSPACE_EMBEDDING_MODEL_NOT_READY",
            message="Start Ollama with the configured local BGE-M3 model available.",
            retryable=True,
        )
    return spec


async def _embed(
    request: Request, spec: EmbeddingCollectionSpec, passages: tuple[str, ...]
) -> tuple[tuple[float, ...], ...]:
    provider = OllamaBgeM3EmbeddingProvider(
        base_url=workspace_settings(request).ollama_base_url,
        model=spec.model,
        revision=spec.revision,
    )
    try:
        vectors = await provider.embed(passages)
    except EmbeddingProviderError:
        raise ApiError(
            status_code=503,
            code="WORKSPACE_EMBEDDING_UNAVAILABLE",
            message="The local model could not embed the text.",
            retryable=True,
        ) from None
    if await _available_spec(request) != spec:
        raise _changed()
    return vectors


async def _status(
    repository: WorkspaceSemanticRepository,
    active: ActiveWorkspaceIndex | None,
    available: EmbeddingCollectionSpec | None,
) -> WorkspaceSemanticStatus:
    total, indexed = await repository.coverage()
    return WorkspaceSemanticStatus(
        active_collection=active.collection_id if active else None,
        active_profile=active.spec if active else None,
        available_profile=available,
        indexed=indexed,
        total=total,
    )


@router.get("/{notebook_id}/semantic-index")
async def semantic_index_status(
    request: Request, repository: SemanticRepository
) -> WorkspaceSemanticStatus:
    active = await repository.active()
    status = await _status(repository, active, None)
    await repository.session.rollback()
    return status.model_copy(
        update={
            "available_profile": await _available_spec(request),
            "cloud_profile": _cloud_profile(request),
            "cohere_profile": _cohere_profile(request),
        }
    )


@router.post("/{notebook_id}/semantic-index")
async def build_semantic_index(
    body: WorkspaceSemanticBuild,
    request: Request,
    repository: SemanticRepository,
) -> WorkspaceSemanticBatchResponse:
    started = perf_counter()
    active = await repository.active()
    expected = active.collection_id if active else None
    await repository.session.rollback()
    if body.expected_collection != expected:
        raise _changed()
    spec = _require_model(await _available_spec(request))
    if body.revision != spec.revision:
        raise _changed()
    same_profile = active is not None and active.spec == spec
    if active and not same_profile and not body.replace:
        raise ApiError(
            status_code=409,
            code="WORKSPACE_SEMANTIC_REBUILD_REQUIRED",
            message="Rebuild the index to use this model revision.",
            retryable=False,
        )
    pending = await repository.pending(expected if same_profile else None)
    await repository.session.rollback()
    prepare_ms = (perf_counter() - started) * 1_000
    embedding_ms = save_ms = 0.0
    added = 0
    if pending:
        started = perf_counter()
        vectors = await _embed(
            request, spec, tuple(content[:EMBEDDING_PASSAGE_CHARACTERS] for _, content in pending)
        )
        embedding_ms = (perf_counter() - started) * 1_000
        started = perf_counter()
        try:
            entries = tuple(
                (item[0], vector) for item, vector in zip(pending, vectors, strict=True)
            )
            added = await repository.store(expected, spec, entries)
            await repository.session.commit()
        except WorkspaceIndexChanged:
            raise _changed() from None
        except NotebookNotFound:
            raise notebook_not_found() from None
        save_ms = (perf_counter() - started) * 1_000
    status = (await _status(repository, await repository.active(), spec)).model_copy(
        update={
            "cloud_profile": _cloud_profile(request),
            "cohere_profile": _cohere_profile(request),
        }
    )
    return WorkspaceSemanticBatchResponse(
        **status.model_dump(),
        added=added,
        prepare_ms=round(prepare_ms, 3),
        embedding_ms=round(embedding_ms, 3),
        save_ms=round(save_ms, 3),
    )


@router.post("/{notebook_id}/semantic-search")
async def semantic_search(
    body: WorkspaceSemanticQuery,
    request: Request,
    repository: SemanticRepository,
) -> WorkspaceSemanticResults:
    if analyze_query(body.query).kind == QueryKind.TEMPORAL:
        raise ApiError(
            status_code=422,
            code="WORKSPACE_TEMPORAL_SCOPE_REQUIRED",
            message="Search a topic here or choose the video workflow for a time or event.",
            retryable=False,
        )
    active = await repository.active()
    await repository.session.rollback()
    if active is None or active.collection_id != body.collection_id:
        raise _changed()
    started = perf_counter()
    spec = _require_model(await _available_spec(request))
    if active.spec != spec:
        raise _changed()
    (vector,) = await _embed(request, spec, (body.query,))
    embedding_ms = (perf_counter() - started) * 1_000
    started = perf_counter()
    rows = await repository.search(active.collection_id, vector)
    if await repository.active() != active:
        raise _changed()
    return WorkspaceSemanticResults(
        collection_id=active.collection_id,
        hits=tuple(map(workspace_search_hit, rows)),
        embedding_ms=round(embedding_ms, 3),
        query_ms=round((perf_counter() - started) * 1_000, 3),
    )
