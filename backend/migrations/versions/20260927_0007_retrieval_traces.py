"""Persist bounded video-scoped retrieval traces.

Revision ID: 20260927_0007
Revises: 20260927_0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260927_0007"
down_revision: str | None = "20260927_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "retrieval_traces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "video_id", sa.Uuid(), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.CheckConstraint("version = 1", name="ck_retrieval_traces_version"),
        sa.CheckConstraint(
            "octet_length(payload::text) <= 262144", name="ck_retrieval_traces_size"
        ),
    )
    op.create_index(
        "ix_retrieval_traces_video_created", "retrieval_traces", ["video_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_retrieval_traces_video_created", table_name="retrieval_traces")
    op.drop_table("retrieval_traces")
