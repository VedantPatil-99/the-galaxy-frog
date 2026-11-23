"""Add private account and notebook roots without transferring legacy data."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261119_0018"
down_revision: str | None = "20261118_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "workspace_notebooks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("workspace_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(title)) BETWEEN 1 AND 120", name="ck_workspace_notebook_title"
        ),
    )
    op.create_index(
        "ix_workspace_notebooks_owner_updated",
        "workspace_notebooks",
        ["owner_id", "updated_at", "id"],
    )
    # Supabase's browser roles get no direct access: no RLS policies are created.
    # FastAPI will use its private database connection and enforce verified ownership.
    op.execute("ALTER TABLE workspace_accounts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workspace_notebooks ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index("ix_workspace_notebooks_owner_updated", table_name="workspace_notebooks")
    op.drop_table("workspace_notebooks")
    op.drop_table("workspace_accounts")
