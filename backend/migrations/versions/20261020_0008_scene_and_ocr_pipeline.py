"""Add scenes, video frames, OCR blocks with lexical search, and OCR embeddings.

Revision ID: 20261020_0008
Revises: 20260927_0007
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "20261020_0008"
down_revision: str | None = "20260927_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create vision scene, frame, OCR block, and embedding tables."""

    op.execute("SET LOCAL search_path TO public, extensions")

    op.create_table(
        "scenes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "video_id",
            sa.Uuid(),
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scene_order", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("detector", sa.String(length=64), nullable=False),
        sa.Column("detector_revision", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("video_id", "scene_order", name="uq_scenes_video_order"),
        sa.CheckConstraint("scene_order >= 0", name="ck_scenes_order_nonnegative"),
        sa.CheckConstraint("start_ms >= 0 AND end_ms > start_ms", name="ck_scenes_interval"),
        sa.CheckConstraint("score >= 0.0 AND score <= 1.0", name="ck_scenes_score"),
    )
    op.create_index("ix_scenes_video_id", "scenes", ["video_id"])

    op.create_table(
        "video_frames",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "video_id",
            sa.Uuid(),
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "scene_id",
            sa.Uuid(),
            sa.ForeignKey("scenes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("dhash", sa.String(length=64), nullable=True),
        sa.Column("is_duplicate", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("image_path", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("video_id", "sequence", name="uq_video_frames_video_sequence"),
        sa.CheckConstraint("sequence >= 0", name="ck_video_frames_sequence_nonnegative"),
        sa.CheckConstraint("start_ms >= 0 AND end_ms > start_ms", name="ck_video_frames_interval"),
        sa.CheckConstraint(
            "source IN ('scene_boundary', 'periodic')", name="ck_video_frames_source"
        ),
    )
    op.create_index("ix_video_frames_video_id", "video_frames", ["video_id"])
    op.create_index("ix_video_frames_scene_id", "video_frames", ["scene_id"])

    op.create_table(
        "ocr_blocks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "video_id",
            sa.Uuid(),
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "frame_id",
            sa.Uuid(),
            sa.ForeignKey("video_frames.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("confidence_method", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_revision", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple'::regconfig, text)", persisted=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("start_ms >= 0 AND end_ms > start_ms", name="ck_ocr_blocks_interval"),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_ocr_blocks_confidence",
        ),
        sa.CheckConstraint(
            "(confidence IS NULL AND confidence_method IS NULL) OR "
            "(confidence IS NOT NULL AND confidence_method IS NOT NULL)",
            name="ck_ocr_blocks_confidence_method",
        ),
    )
    op.create_index("ix_ocr_blocks_video_id", "ocr_blocks", ["video_id"])
    op.create_index("ix_ocr_blocks_frame_id", "ocr_blocks", ["frame_id"])
    op.create_index(
        "ix_ocr_blocks_search_vector",
        "ocr_blocks",
        ["search_vector"],
        postgresql_using="gin",
    )

    op.create_table(
        "ocr_embeddings",
        sa.Column(
            "block_id",
            sa.Uuid(),
            sa.ForeignKey("ocr_blocks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "collection_id",
            sa.Uuid(),
            sa.ForeignKey("embedding_collections.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.PrimaryKeyConstraint("block_id", "collection_id"),
    )
    op.create_index(
        "ix_ocr_embeddings_hnsw_cosine",
        "ocr_embeddings",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    """Drop vision scene, frame, OCR block, and embedding tables."""

    op.drop_index("ix_ocr_embeddings_hnsw_cosine", table_name="ocr_embeddings")
    op.drop_table("ocr_embeddings")
    op.drop_index("ix_ocr_blocks_search_vector", table_name="ocr_blocks")
    op.drop_index("ix_ocr_blocks_frame_id", table_name="ocr_blocks")
    op.drop_index("ix_ocr_blocks_video_id", table_name="ocr_blocks")
    op.drop_table("ocr_blocks")
    op.drop_index("ix_video_frames_scene_id", table_name="video_frames")
    op.drop_index("ix_video_frames_video_id", table_name="video_frames")
    op.drop_table("video_frames")
    op.drop_index("ix_scenes_video_id", table_name="scenes")
    op.drop_table("scenes")
