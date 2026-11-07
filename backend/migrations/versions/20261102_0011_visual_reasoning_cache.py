"""Add bounded video-scoped visual reasoning cache records.

Revision ID: 20261102_0011
Revises: 20261102_0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261102_0011"
down_revision: str | None = "20261102_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "visual_reasoning_cache",
        sa.Column("cache_key", sa.String(64), primary_key=True),
        sa.Column(
            "video_id", sa.Uuid(), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version = 1", name="ck_visual_cache_version"),
        sa.CheckConstraint("octet_length(payload::text) <= 65536", name="ck_visual_cache_size"),
    )
    op.create_index("ix_visual_reasoning_cache_video_id", "visual_reasoning_cache", ["video_id"])


def downgrade() -> None:
    op.drop_table("visual_reasoning_cache")
