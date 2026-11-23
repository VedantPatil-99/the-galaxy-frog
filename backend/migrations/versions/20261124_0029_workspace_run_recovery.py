"""Extend durable chat runs with preparing, Stop, bounded reclaim, and retry lineage."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261124_0029"
down_revision: str | None = "20261123_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "workspace_chat_runs"
STATUS = "ck_workspace_run_status"
ACTIVE = "ux_workspace_chat_active_run"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("claim_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column(TABLE, sa.Column("retry_of_run_id", sa.Uuid(), nullable=True))
    op.create_unique_constraint("uq_workspace_run_chat_id", TABLE, ["chat_id", "id"])
    op.create_foreign_key(
        "fk_workspace_run_retry_in_chat",
        TABLE,
        TABLE,
        ["chat_id", "retry_of_run_id"],
        ["chat_id", "id"],
    )
    op.execute("UPDATE workspace_chat_runs SET claim_count = 1 WHERE status <> 'queued'")
    op.drop_constraint(STATUS, TABLE, type_="check")
    op.create_check_constraint(
        STATUS,
        TABLE,
        "status IN ('queued', 'preparing', 'running', 'completed', 'stopped', 'failed', 'uncertain')",
    )
    op.drop_index(ACTIVE, table_name=TABLE)
    op.create_index(
        ACTIVE,
        TABLE,
        ["chat_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'preparing', 'running')"),
    )


def downgrade() -> None:
    op.execute("UPDATE workspace_chat_runs SET status = 'running' WHERE status = 'preparing'")
    op.execute("UPDATE workspace_chat_runs SET status = 'failed' WHERE status = 'stopped'")
    op.drop_index(ACTIVE, table_name=TABLE)
    op.create_index(
        ACTIVE,
        TABLE,
        ["chat_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.drop_constraint(STATUS, TABLE, type_="check")
    op.create_check_constraint(
        STATUS, TABLE, "status IN ('queued', 'running', 'completed', 'failed', 'uncertain')"
    )
    op.drop_constraint("fk_workspace_run_retry_in_chat", TABLE, type_="foreignkey")
    op.drop_constraint("uq_workspace_run_chat_id", TABLE, type_="unique")
    op.drop_column(TABLE, "retry_of_run_id")
    op.drop_column(TABLE, "claim_count")
