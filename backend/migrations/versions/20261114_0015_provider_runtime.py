"""Persist scoped cloud consent, admission state and provider attempts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261114_0015"
down_revision: str | None = "20261114_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ingestion_jobs", sa.Column("provider_selection", sa.JSON))
    op.create_table(
        "provider_consents",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("profile_id", sa.String(40), nullable=False),
        sa.Column("profile_version", sa.Integer, nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("scopes", sa.JSON, nullable=False),
        sa.Column("run_id", sa.Uuid),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("dispatch_count", sa.Integer, nullable=False),
    )
    op.create_table(
        "provider_runtime",
        sa.Column("provider_id", sa.String(240), primary_key=True),
        sa.Column("failure_count", sa.Integer, nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True)),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "provider_attempts",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("run_id", sa.Uuid, nullable=False),
        sa.Column("profile_id", sa.String(40), nullable=False),
        sa.Column("profile_version", sa.Integer, nullable=False),
        sa.Column("capability", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_provider_attempts_run", "provider_attempts", ["operation", "run_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("provider_attempts")
    op.drop_table("provider_runtime")
    op.drop_table("provider_consents")
    op.drop_column("ingestion_jobs", "provider_selection")
