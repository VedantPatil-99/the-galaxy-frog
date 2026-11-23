"""Persist a local grounded chat turn with its independently fenced run."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261123_0028"
down_revision: str | None = "20261122_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_chat_turns",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "chat_id",
            sa.Uuid(),
            sa.ForeignKey("workspace_chats.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("submission_key", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("question", sa.String(1000), nullable=False),
        sa.Column("pin", JSONB(), nullable=False),
        sa.Column("passage_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("chat_id", "submission_key", name="uq_workspace_turn_submission"),
        sa.UniqueConstraint("chat_id", "sequence", name="uq_workspace_turn_sequence"),
        sa.UniqueConstraint("chat_id", "id", name="uq_workspace_turn_chat_id"),
    )
    op.create_table(
        "workspace_chat_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("turn_id", sa.Uuid(), unique=True, nullable=False),
        sa.Column("chat_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("creator_email", sa.String(320), nullable=False),
        sa.Column("authorization_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settings", JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("dispatch_at", sa.DateTime(timezone=True)),
        sa.Column("model_revision_at_dispatch", sa.String(128)),
        sa.Column("failure", sa.String(24)),
        sa.Column("evidence", JSONB()),
        sa.Column("answer", JSONB()),
        sa.ForeignKeyConstraint(
            ["chat_id", "turn_id"],
            ["workspace_chat_turns.chat_id", "workspace_chat_turns.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'uncertain')",
            name="ck_workspace_run_status",
        ),
    )
    op.create_index(
        "ux_workspace_chat_active_run",
        "workspace_chat_runs",
        ["chat_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index("ix_workspace_runs_queue", "workspace_chat_runs", ["status", "queued_at", "id"])
    op.execute("ALTER TABLE workspace_chat_turns ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workspace_chat_runs ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("workspace_chat_runs")
    op.drop_table("workspace_chat_turns")
