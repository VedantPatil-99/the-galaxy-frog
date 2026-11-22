"""Owner-scoped, revision-bound vectors for the current notebook evidence."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, String, bindparam, delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from galaxy_frog.db.models import Base, EmbeddingCollectionRow
from galaxy_frog.db.workspace import WorkspaceNotebookRow
from galaxy_frog.db.workspace_search import (
    WORKSPACE_EVIDENCE_PROJECTION,
    WORKSPACE_PASSAGE_INVENTORY,
)
from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec
from galaxy_frog.domain.workspace import NotebookNotFound

INDEX_BATCH_SIZE = 16
EMBEDDING_PASSAGE_CHARACTERS = 2_000

_COVERAGE = text(
    WORKSPACE_PASSAGE_INVENTORY
    + """
    SELECT count(*) AS total, count(e.passage_key) AS indexed
    FROM passages p LEFT JOIN workspace_semantic_embeddings e
    ON e.notebook_id = :notebook_id AND e.passage_key = p.passage_key
    """
)

_PENDING = text(
    WORKSPACE_PASSAGE_INVENTORY
    + """
    SELECT p.passage_key, p.text FROM passages p
    WHERE NOT EXISTS (
        SELECT 1 FROM workspace_semantic_embeddings e
        WHERE e.notebook_id = :notebook_id AND e.collection_id = :collection_id
        AND e.passage_key = p.passage_key
    ) ORDER BY p.passage_key LIMIT :batch_size
    """
)

_REMAINING = text(
    WORKSPACE_PASSAGE_INVENTORY
    + """
    SELECT count(*), coalesce(sum(least(octet_length(p.text), :max_bytes)), 0)
    FROM passages p WHERE NOT EXISTS (
        SELECT 1 FROM workspace_semantic_embeddings e
        WHERE e.notebook_id = :notebook_id AND e.collection_id = :collection_id
        AND e.passage_key = p.passage_key
    )
    """
)

_SEMANTIC_SEARCH = text(
    WORKSPACE_PASSAGE_INVENTORY
    + """
    , matches AS MATERIALIZED (
        SELECT p.*, e.embedding <=> CAST(:query_vector AS vector) AS distance
        FROM workspace_semantic_embeddings e JOIN passages p ON p.passage_key = e.passage_key
        WHERE e.notebook_id = :notebook_id AND e.collection_id = :collection_id
        ORDER BY distance, p.passage_key LIMIT 20
    )
    """
    + WORKSPACE_EVIDENCE_PROJECTION
    + " ORDER BY m.distance, m.passage_key"
).bindparams(bindparam("query_vector", type_=Vector()))


class WorkspaceSemanticIndexRow(Base):
    __tablename__ = "workspace_semantic_indexes"

    notebook_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspace_notebooks.id", ondelete="CASCADE"), primary_key=True
    )
    collection_id: Mapped[UUID] = mapped_column(
        ForeignKey("embedding_collections.id", ondelete="RESTRICT")
    )


class WorkspaceSemanticEmbeddingRow(Base):
    __tablename__ = "workspace_semantic_embeddings"

    notebook_id: Mapped[UUID] = mapped_column(primary_key=True)
    passage_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    collection_id: Mapped[UUID]
    embedding: Mapped[list[float]] = mapped_column(Vector())


@dataclass(frozen=True, slots=True)
class ActiveWorkspaceIndex:
    collection_id: UUID
    spec: EmbeddingCollectionSpec


class WorkspaceIndexChanged(Exception):
    """The selected collection changed while a batch was in flight."""


class WorkspaceSemanticRepository:
    def __init__(self, session: AsyncSession, owner_id: UUID, notebook_id: UUID) -> None:
        self.session = session
        self.owner_id = owner_id
        self.notebook_id = notebook_id

    def _scope(self) -> dict[str, UUID]:
        return {"owner_id": self.owner_id, "notebook_id": self.notebook_id}

    async def active(self) -> ActiveWorkspaceIndex | None:
        row = await self.session.scalar(
            select(EmbeddingCollectionRow)
            .join(
                WorkspaceSemanticIndexRow,
                WorkspaceSemanticIndexRow.collection_id == EmbeddingCollectionRow.id,
            )
            .join(
                WorkspaceNotebookRow,
                WorkspaceNotebookRow.id == WorkspaceSemanticIndexRow.notebook_id,
            )
            .where(
                WorkspaceNotebookRow.owner_id == self.owner_id,
                WorkspaceNotebookRow.id == self.notebook_id,
            )
        )
        return (
            ActiveWorkspaceIndex(
                row.id,
                EmbeddingCollectionSpec(
                    row.provider, row.model, row.revision, row.dimension, row.normalization
                ),
            )
            if row
            else None
        )

    async def coverage(self) -> tuple[int, int]:
        total, indexed = (await self.session.execute(_COVERAGE, self._scope())).one()
        return int(total), int(indexed)

    async def pending(self, collection_id: UUID | None) -> tuple[tuple[str, str], ...]:
        rows = await self.session.execute(
            _PENDING,
            {**self._scope(), "collection_id": collection_id, "batch_size": INDEX_BATCH_SIZE},
        )
        return tuple((str(key), str(content)) for key, content in rows)

    async def remaining_size(self, collection_id: UUID | None, max_bytes: int) -> tuple[int, int]:
        total, size = (
            await self.session.execute(
                _REMAINING,
                {
                    **self._scope(),
                    "collection_id": collection_id,
                    "max_bytes": max_bytes,
                },
            )
        ).one()
        return int(total), int(size)

    async def search(self, collection_id: UUID, vector: tuple[float, ...]) -> Sequence[RowMapping]:
        rows = await self.session.execute(
            _SEMANTIC_SEARCH,
            {**self._scope(), "collection_id": collection_id, "query_vector": list(vector)},
        )
        return rows.mappings().all()

    async def lock_notebook(self) -> None:
        """Serialize changes within the owner scope without holding locks during inference."""
        notebook = await self.session.scalar(
            select(WorkspaceNotebookRow.id)
            .where(
                WorkspaceNotebookRow.id == self.notebook_id,
                WorkspaceNotebookRow.owner_id == self.owner_id,
            )
            .with_for_update()
        )
        if notebook is None:
            raise NotebookNotFound

    async def store(
        self,
        expected_collection: UUID | None,
        spec: EmbeddingCollectionSpec,
        entries: tuple[tuple[str, tuple[float, ...]], ...],
    ) -> int:
        await self.lock_notebook()
        current = await self.active()
        if (current.collection_id if current else None) != expected_collection:
            raise WorkspaceIndexChanged
        if current and current.spec == spec:
            collection_id = current.collection_id
        else:
            collection_id = await self._collection(spec)
            await self.session.execute(
                delete(WorkspaceSemanticIndexRow).where(
                    WorkspaceSemanticIndexRow.notebook_id == self.notebook_id
                )
            )
            await self.session.execute(
                insert(WorkspaceSemanticIndexRow).values(
                    notebook_id=self.notebook_id,
                    collection_id=collection_id,
                )
            )
        written = await self.session.scalars(
            insert(WorkspaceSemanticEmbeddingRow)
            .values(
                [
                    {
                        "notebook_id": self.notebook_id,
                        "collection_id": collection_id,
                        "passage_key": key,
                        "embedding": list(vector),
                    }
                    for key, vector in entries
                ]
            )
            .on_conflict_do_nothing()
            .returning(WorkspaceSemanticEmbeddingRow.passage_key)
        )
        return len(written.all())

    async def _collection(self, spec: EmbeddingCollectionSpec) -> UUID:
        row = EmbeddingCollectionRow
        identity = {
            "provider": spec.provider,
            "model": spec.model,
            "revision": spec.revision,
            "dimension": spec.dimension,
            "normalization": spec.normalization,
        }
        await self.session.execute(
            insert(row)
            .values(**identity, id=uuid4(), created_at=datetime.now(UTC))
            .on_conflict_do_nothing(constraint="uq_embedding_collection_identity")
        )
        return (await self.session.scalars(select(row.id).filter_by(**identity))).one()
