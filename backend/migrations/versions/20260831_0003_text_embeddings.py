"""Add versioned BGE-M3 transcript embeddings.

Revision ID: 20260831_0003
Revises: 20260831_0002
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "20260831_0003"
down_revision: str | None = "20260831_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create immutable collection metadata and transcript vector rows."""

    op.execute("SET LOCAL search_path TO public, extensions")
    op.create_table(
        "embedding_collections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("revision", sa.String(length=255), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("normalization", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("dimension = 1024", name="ck_embedding_collection_dimension"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "model",
            "revision",
            "dimension",
            "normalization",
            name="uq_embedding_collection_identity",
        ),
    )
    op.create_table(
        "text_embeddings",
        sa.Column("retrieval_unit_id", sa.String(length=64), nullable=False),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.ForeignKeyConstraint(
            ["collection_id"], ["embedding_collections.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["retrieval_unit_id"], ["retrieval_units.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("retrieval_unit_id", "collection_id"),
    )
    op.create_index(
        "ix_text_embeddings_hnsw_cosine",
        "text_embeddings",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    """Remove transcript embeddings while preserving transcript records."""

    op.drop_index("ix_text_embeddings_hnsw_cosine", table_name="text_embeddings")
    op.drop_table("text_embeddings")
    op.drop_table("embedding_collections")
