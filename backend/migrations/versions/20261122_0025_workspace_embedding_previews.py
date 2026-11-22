"""Scope one-use cloud embedding previews to private notebooks."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261122_0025"
down_revision: str | None = "20261122_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_embedding_previews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "notebook_id",
            sa.Uuid(),
            sa.ForeignKey("workspace_notebooks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "finished_at IS NULL OR started_at IS NOT NULL",
            name="ck_workspace_embedding_preview_started",
        ),
    )
    op.create_index(
        "ix_workspace_embedding_previews_notebook",
        "workspace_embedding_previews",
        ["notebook_id", "expires_at"],
    )
    op.execute("ALTER TABLE workspace_embedding_previews ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("workspace_embedding_previews")
