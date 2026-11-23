"""Store private notebook chats and revisioned prompt drafts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261122_0027"
down_revision: str | None = "20261122_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_chats",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "notebook_id",
            sa.Uuid(),
            sa.ForeignKey("workspace_notebooks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("draft_text", sa.String(1000), nullable=False),
        sa.Column("draft_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_workspace_chat_title"),
        sa.CheckConstraint("draft_revision >= 0", name="ck_workspace_chat_draft_revision"),
    )
    op.create_index(
        "ix_workspace_chats_notebook_updated",
        "workspace_chats",
        ["notebook_id", "updated_at", "id"],
    )
    op.execute("ALTER TABLE workspace_chats ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("workspace_chats")
