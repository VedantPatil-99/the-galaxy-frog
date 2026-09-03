"""Add transcript-first video persistence.

Revision ID: 20260831_0002
Revises: 20260830_0001
Create Date: 2026-08-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0002"
down_revision: str | None = "20260830_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create canonical video, cue, unit, and provenance-link tables."""

    op.create_table(
        "videos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("duration_ms", sa.BigInteger(), nullable=False),
        sa.Column("channel_name", sa.Text(), nullable=True),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("duration_ms > 0", name="ck_videos_duration_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_kind", "external_id", name="uq_videos_source"),
    )
    op.create_table(
        "transcript_cues",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("video_id", sa.Uuid(), nullable=False),
        sa.Column("track_id", sa.String(length=255), nullable=False),
        sa.Column("language_code", sa.String(length=64), nullable=False),
        sa.Column("caption_kind", sa.String(length=32), nullable=False),
        sa.Column("source_order", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.CheckConstraint("source_order >= 0", name="ck_cues_order_nonnegative"),
        sa.CheckConstraint("start_ms >= 0 AND end_ms > start_ms", name="ck_cues_interval"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("video_id", "source_order", name="uq_cues_video_order"),
    )
    op.create_index("ix_transcript_cues_video_id", "transcript_cues", ["video_id"])
    op.create_table(
        "retrieval_units",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("video_id", sa.Uuid(), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.CheckConstraint("start_ms >= 0 AND end_ms > start_ms", name="ck_units_interval"),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_retrieval_units_video_id", "retrieval_units", ["video_id"])
    op.create_table(
        "retrieval_unit_cues",
        sa.Column("retrieval_unit_id", sa.String(length=64), nullable=False),
        sa.Column("cue_id", sa.String(length=64), nullable=False),
        sa.Column("cue_order", sa.Integer(), nullable=False),
        sa.CheckConstraint("cue_order >= 0", name="ck_unit_cues_order_nonnegative"),
        sa.ForeignKeyConstraint(["retrieval_unit_id"], ["retrieval_units.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cue_id"], ["transcript_cues.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("retrieval_unit_id", "cue_id"),
        sa.UniqueConstraint("retrieval_unit_id", "cue_order", name="uq_unit_cue_order"),
    )


def downgrade() -> None:
    """Remove transcript-first tables while preserving the pgvector extension."""

    op.drop_table("retrieval_unit_cues")
    op.drop_index("ix_retrieval_units_video_id", table_name="retrieval_units")
    op.drop_table("retrieval_units")
    op.drop_index("ix_transcript_cues_video_id", table_name="transcript_cues")
    op.drop_table("transcript_cues")
    op.drop_table("videos")
