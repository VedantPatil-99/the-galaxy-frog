"""Keep notebook vectors under one active embedding collection."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "20261122_0024"
down_revision: str | None = "20261122_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_semantic_indexes",
        sa.Column(
            "notebook_id",
            sa.Uuid(),
            sa.ForeignKey("workspace_notebooks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "collection_id",
            sa.Uuid(),
            sa.ForeignKey("embedding_collections.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "notebook_id", "collection_id", name="uq_workspace_semantic_collection"
        ),
    )
    op.create_table(
        "workspace_semantic_embeddings",
        sa.Column("notebook_id", sa.Uuid(), primary_key=True),
        sa.Column("passage_key", sa.String(64), primary_key=True),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.ForeignKeyConstraint(
            ["notebook_id", "collection_id"],
            ["workspace_semantic_indexes.notebook_id", "workspace_semantic_indexes.collection_id"],
            ondelete="CASCADE",
        ),
    )
    op.execute("ALTER TABLE workspace_semantic_indexes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workspace_semantic_embeddings ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("workspace_semantic_embeddings")
    op.drop_table("workspace_semantic_indexes")
