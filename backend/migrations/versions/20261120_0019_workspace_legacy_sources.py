"""Record explicit notebook assignments without moving legacy data."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261120_0019"
down_revision: str | None = "20261119_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_legacy_sources",
        sa.Column(
            "video_id",
            sa.Uuid(),
            sa.ForeignKey("videos.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "notebook_id",
            sa.Uuid(),
            sa.ForeignKey("workspace_notebooks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_workspace_legacy_sources_notebook_video",
        "workspace_legacy_sources",
        ["notebook_id", "video_id"],
    )
    op.execute("ALTER TABLE workspace_legacy_sources ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("workspace_legacy_sources")
