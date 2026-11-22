"""Preview and explicitly confirm one notebook Gemini embedding operation."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from time import perf_counter
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, field_validator
from sqlalchemy import DateTime, ForeignKey, String, delete, select, update
from sqlalchemy.orm import Mapped, mapped_column

from galaxy_frog.adapters.embeddings.gemini import (
    GEMINI_DOCUMENT_PREFIX,
    GEMINI_EMBEDDING_SPEC,
    GEMINI_QUERY_PREFIX,
    GeminiEmbeddingProvider,
)
from galaxy_frog.api.errors import ApiError
from galaxy_frog.api.provider_execution import provider_api_error
from galaxy_frog.api.routes.workspace import notebook_not_found
from galaxy_frog.api.routes.workspace_semantic import (
    SemanticRepository,
    WorkspaceSemanticBatchResponse,
    WorkspaceSemanticResults,
    semantic_index_status,
)
from galaxy_frog.api.schemas import ErrorResponse
from galaxy_frog.api.workspace_auth import prevent_workspace_caching, workspace_settings
from galaxy_frog.api.workspace_schemas import WorkspaceSearchQuery, workspace_search_hit
from galaxy_frog.application.provider_policy import ProviderCode, ProviderFailure
from galaxy_frog.application.provider_registry import gemini_quota
from galaxy_frog.db.models import Base
from galaxy_frog.db.provider_quota import reserve_quota
from galaxy_frog.db.workspace_semantic import WorkspaceIndexChanged
from galaxy_frog.domain.retrieval.pipeline import QueryKind
from galaxy_frog.domain.retrieval.query import analyze_query
from galaxy_frog.domain.workspace import NotebookNotFound

router = APIRouter(
    prefix="/v1/workspace/notebooks",
    tags=["workspace"],
    dependencies=[Depends(prevent_workspace_caching)],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 429, 503)},
)
_USD_PER_MILLION = Decimal("0.20")
_MAX_PASSAGE_BYTES = 2_000
_PREVIEW_DURATION = timedelta(minutes=5)
_DISPATCH_LEASE = timedelta(minutes=2)


class WorkspaceEmbeddingPreviewRow(Base):
    __tablename__ = "workspace_embedding_previews"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    notebook_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_notebooks.id", ondelete="CASCADE")
    )
    fingerprint: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CloudEmbeddingIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["index"]
    expected_collection: UUID | None
    replace: StrictBool = False


class CloudEmbeddingSearch(WorkspaceSearchQuery):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["search"]
    collection_id: UUID


CloudEmbeddingAction = Annotated[
    CloudEmbeddingIndex | CloudEmbeddingSearch, Field(discriminator="kind")
]


class CloudEmbeddingPreview(BaseModel):
    preview_id: UUID
    kind: Literal["index", "search"]
    model: Literal["gemini-embedding-2"] = "gemini-embedding-2"
    inputs: int
    estimated_input_tokens: int
    estimated_usd: str
    remaining_inputs: int
    estimated_remaining_usd: str
    expires_at: datetime


class CloudEmbeddingConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_id: UUID
    action: CloudEmbeddingAction
    allow_cloud: Literal[True]

    @field_validator("allow_cloud", mode="before")
    @classmethod
    def exact_consent(cls, value: object) -> Literal[True]:
        if value is not True:
            raise ValueError("Choose explicit permission to send text to Gemini.")
        return True


class CloudIndexResult(BaseModel):
    kind: Literal["index"] = "index"
    index: WorkspaceSemanticBatchResponse


class CloudSearchResult(BaseModel):
    kind: Literal["search"] = "search"
    results: WorkspaceSemanticResults


CloudEmbeddingResult = Annotated[CloudIndexResult | CloudSearchResult, Field(discriminator="kind")]


@dataclass(frozen=True, slots=True)
class _PreparedEmbedding:
    payload: tuple[str, ...]
    passage_keys: tuple[str, ...]
    remaining_inputs: int
    remaining_bytes: int

    @property
    def input_bytes(self) -> int:
        return sum(len(content.encode("utf-8")) for content in self.payload)


def _credentials(request: Request) -> tuple[SecretStr, str]:
    settings = workspace_settings(request)
    key, project = settings.gemini_embedding_api_key, settings.gemini_embedding_project_id
    if not settings.workspace_gemini_embedding_enabled or key is None or project is None:
        raise ApiError(
            status_code=503,
            code="WORKSPACE_CLOUD_EMBEDDING_UNAVAILABLE",
            message="Configure and enable the Gemini embedding profile.",
            retryable=False,
        )
    return key, project


def _preview_changed() -> ApiError:
    return ApiError(
        status_code=409,
        code="WORKSPACE_CLOUD_PREVIEW_CHANGED",
        message="Request a new Gemini preview. The previous one expired, was used, or its inputs changed.",
        retryable=False,
    )


async def _prepare(
    repository: SemanticRepository, action: CloudEmbeddingAction
) -> _PreparedEmbedding:
    current = await repository.active()
    if action.kind == "search":
        if analyze_query(action.query).kind == QueryKind.TEMPORAL:
            raise ApiError(
                status_code=422,
                code="WORKSPACE_TEMPORAL_SCOPE_REQUIRED",
                message="Search a topic here or choose the video workflow for a time or event.",
                retryable=False,
            )
        if (
            current is None
            or current.collection_id != action.collection_id
            or current.spec != GEMINI_EMBEDDING_SPEC
        ):
            raise _preview_changed()
        payload = GEMINI_QUERY_PREFIX + action.query
        return _PreparedEmbedding((payload,), (), 1, len(payload.encode("utf-8")))

    expected = current.collection_id if current else None
    if expected != action.expected_collection:
        raise _preview_changed()
    compatible = current is not None and current.spec == GEMINI_EMBEDDING_SPEC
    if current is not None and not compatible and not action.replace:
        raise ApiError(
            status_code=409,
            code="WORKSPACE_SEMANTIC_REBUILD_REQUIRED",
            message="Select a rebuild to use Gemini for this notebook.",
            retryable=False,
        )
    collection = expected if compatible else None
    pending = await repository.pending(collection)
    if not pending:
        raise ApiError(
            status_code=409,
            code="WORKSPACE_CLOUD_INDEX_CURRENT",
            message="There are no passages awaiting Gemini indexing.",
            retryable=False,
        )
    total, size = await repository.remaining_size(collection, _MAX_PASSAGE_BYTES)
    return _PreparedEmbedding(
        tuple(
            GEMINI_DOCUMENT_PREFIX
            + passage.encode("utf-8")[:_MAX_PASSAGE_BYTES].decode("utf-8", errors="ignore")
            for _, passage in pending
        ),
        tuple(identity for identity, _ in pending),
        total,
        size + total * len(GEMINI_DOCUMENT_PREFIX),
    )


def _fingerprint(action: CloudEmbeddingAction, prepared: _PreparedEmbedding, project: str) -> str:
    data = json.dumps(
        (
            action.model_dump(mode="json"),
            prepared.passage_keys,
            prepared.payload,
            GEMINI_EMBEDDING_SPEC.revision,
            project,
            str(_USD_PER_MILLION),
        ),
        ensure_ascii=False,
        sort_keys=True,
    )
    return sha256(data.encode("utf-8")).hexdigest()


def _estimate(input_bytes: int) -> str:
    return format(input_bytes * _USD_PER_MILLION / Decimal(1_000_000), ".8f")


@router.post("/{notebook_id}/semantic-cloud-preview")
async def preview_cloud_embedding(
    action: CloudEmbeddingAction,
    request: Request,
    repository: SemanticRepository,
) -> CloudEmbeddingPreview:
    _, project = _credentials(request)
    prepared = await _prepare(repository, action)
    now = datetime.now(UTC)
    row = WorkspaceEmbeddingPreviewRow(
        id=uuid4(),
        notebook_id=repository.notebook_id,
        fingerprint=_fingerprint(action, prepared, project),
        expires_at=now + _PREVIEW_DURATION,
    )
    # Retain spent receipts long enough to explain a recent dispatch, without keeping old queries.
    await repository.session.execute(
        delete(WorkspaceEmbeddingPreviewRow).where(
            WorkspaceEmbeddingPreviewRow.notebook_id == repository.notebook_id,
            WorkspaceEmbeddingPreviewRow.expires_at < now - timedelta(days=1),
        )
    )
    repository.session.add(row)
    preview = CloudEmbeddingPreview(
        preview_id=row.id,
        kind=action.kind,
        inputs=len(prepared.payload),
        estimated_input_tokens=prepared.input_bytes,
        estimated_usd=_estimate(prepared.input_bytes),
        remaining_inputs=prepared.remaining_inputs,
        estimated_remaining_usd=_estimate(prepared.remaining_bytes),
        expires_at=row.expires_at,
    )
    await repository.session.commit()
    return preview


async def _claim(
    body: CloudEmbeddingConfirmation,
    request: Request,
    repository: SemanticRepository,
    project: str,
) -> _PreparedEmbedding:
    try:
        await repository.lock_notebook()
    except NotebookNotFound:
        raise notebook_not_found() from None
    now = datetime.now(UTC)
    preview_row = WorkspaceEmbeddingPreviewRow
    if await repository.session.scalar(
        select(preview_row.id)
        .where(
            preview_row.notebook_id == repository.notebook_id,
            preview_row.finished_at.is_(None),
            preview_row.started_at > now - _DISPATCH_LEASE,
        )
        .limit(1)
    ):
        raise ApiError(
            status_code=409,
            code="WORKSPACE_CLOUD_EMBEDDING_BUSY",
            message="A recent Gemini request is still pending. Check its outcome before confirming another.",
            retryable=False,
        )
    prepared = await _prepare(repository, body.action)
    matched = await repository.session.scalar(
        update(preview_row)
        .where(
            preview_row.id == body.preview_id,
            preview_row.notebook_id == repository.notebook_id,
            preview_row.expires_at > now,
            preview_row.started_at.is_(None),
            preview_row.fingerprint == _fingerprint(body.action, prepared, project),
        )
        .values(started_at=now)
        .returning(preview_row.id)
    )
    if matched is None:
        raise _preview_changed()
    quotas = gemini_quota(workspace_settings(request), project)
    group = quotas["quota_group"]
    assert group is not None
    try:
        await reserve_quota(repository.session, group, quotas["quota_limits"], prepared.input_bytes)
    except ProviderFailure as exc:
        raise provider_api_error(exc, body.preview_id) from None
    await repository.session.commit()
    return prepared


async def _finish(repository: SemanticRepository, preview_id: UUID) -> None:
    await repository.session.execute(
        update(WorkspaceEmbeddingPreviewRow)
        .where(
            WorkspaceEmbeddingPreviewRow.id == preview_id,
            WorkspaceEmbeddingPreviewRow.notebook_id == repository.notebook_id,
        )
        .values(finished_at=datetime.now(UTC))
    )
    await repository.session.commit()


@router.post("/{notebook_id}/semantic-cloud-confirm")
async def confirm_cloud_embedding(
    body: CloudEmbeddingConfirmation,
    request: Request,
    repository: SemanticRepository,
) -> CloudEmbeddingResult:
    key, project = _credentials(request)
    started = perf_counter()
    prepared = await _claim(body, request, repository, project)
    prepare_ms = (perf_counter() - started) * 1_000
    started = perf_counter()
    try:
        vectors = await GeminiEmbeddingProvider(key).embed(prepared.payload)
    except ProviderFailure as exc:
        if exc.code == ProviderCode.OUTCOME_UNKNOWN:
            raise ApiError(
                status_code=503,
                code=exc.code.value,
                message="Gemini may have processed this request. Check your project usage before confirming another one.",
                retryable=False,
            ) from None
        await _finish(repository, body.preview_id)
        raise provider_api_error(exc, body.preview_id) from None
    embedding_ms = (perf_counter() - started) * 1_000
    started = perf_counter()
    action = body.action
    if action.kind == "search":
        rows = await repository.search(action.collection_id, vectors[0])
        current = await repository.active()
        query_ms = (perf_counter() - started) * 1_000
        await _finish(repository, body.preview_id)
        if current is None or current.collection_id != action.collection_id:
            raise _preview_changed()
        return CloudSearchResult(
            results=WorkspaceSemanticResults(
                collection_id=action.collection_id,
                hits=tuple(map(workspace_search_hit, rows)),
                embedding_ms=round(embedding_ms, 3),
                query_ms=round(query_ms, 3),
            )
        )
    try:
        added = await repository.store(
            action.expected_collection,
            GEMINI_EMBEDDING_SPEC,
            tuple(zip(prepared.passage_keys, vectors, strict=True)),
        )
    except WorkspaceIndexChanged:
        await repository.session.rollback()
        await _finish(repository, body.preview_id)
        raise _preview_changed() from None
    except NotebookNotFound:
        await repository.session.rollback()
        await _finish(repository, body.preview_id)
        raise notebook_not_found() from None
    await _finish(repository, body.preview_id)
    save_ms = (perf_counter() - started) * 1_000
    current = await semantic_index_status(request, repository)
    return CloudIndexResult(
        index=WorkspaceSemanticBatchResponse(
            **current.model_dump(),
            added=added,
            prepare_ms=round(prepare_ms, 3),
            embedding_ms=round(embedding_ms, 3),
            save_ms=round(save_ms, 3),
        )
    )
