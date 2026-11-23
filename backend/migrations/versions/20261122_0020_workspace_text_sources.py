"""Persist private text and Markdown pastes as immutable source versions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261122_0020"
down_revision: str | None = "20261120_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_text_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column(
            "notebook_id",
            sa.Uuid(),
            sa.ForeignKey("workspace_notebooks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("spans", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("format IN ('plain', 'markdown')", name="ck_workspace_text_format"),
        sa.CheckConstraint(
            "octet_length(text) BETWEEN 1 AND 131072", name="ck_workspace_text_size"
        ),
        sa.CheckConstraint(
            "jsonb_array_length(spans) BETWEEN 1 AND 2048", name="ck_workspace_text_spans"
        ),
        sa.UniqueConstraint(
            "notebook_id", "title", "format", "content_sha256", name="uq_workspace_text_paste"
        ),
    )
    op.create_index(
        "ix_workspace_text_notebook_source", "workspace_text_sources", ["notebook_id", "id"]
    )
    op.execute("ALTER TABLE workspace_text_sources ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("workspace_text_sources")
