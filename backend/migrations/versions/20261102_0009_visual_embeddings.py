"""Add baseline visual vectors and immutable frame content hashes.

Revision ID: 20261102_0009
Revises: 20261020_0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "20261102_0009"
down_revision: str | None = "20261020_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL search_path TO public, extensions")
    op.add_column("video_frames", sa.Column("content_sha256", sa.String(64), nullable=True))
    op.create_table(
        "visual_embeddings",
        sa.Column(
            "frame_id",
            sa.Uuid(),
            sa.ForeignKey("video_frames.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "collection_id",
            sa.Uuid(),
            sa.ForeignKey("embedding_collections.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.CheckConstraint("length(content_sha256) = 64", name="ck_visual_embedding_hash"),
    )
    op.create_index(
        "ix_visual_embeddings_cosine",
        "visual_embeddings",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("ix_visual_embeddings_collection", "visual_embeddings", ["collection_id"])


def downgrade() -> None:
    op.drop_table("visual_embeddings")
    op.drop_column("video_frames", "content_sha256")
