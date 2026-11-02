"""Preserve exact capture time separately from the original scene interval.

Revision ID: 20261102_0012
Revises: 20261102_0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261102_0012"
down_revision: str | None = "20261102_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Legacy captures remain unknown; never infer a capture from a scene start.
    op.add_column("video_frames", sa.Column("capture_ms", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        "ck_video_frames_capture",
        "video_frames",
        "capture_ms IS NULL OR (capture_ms >= start_ms AND capture_ms < end_ms)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_video_frames_capture", "video_frames", type_="check")
    op.drop_column("video_frames", "capture_ms")
