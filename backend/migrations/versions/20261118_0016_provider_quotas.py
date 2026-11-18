"""Persist bounded project quota reservations without storing credentials or text."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261118_0016"
down_revision: str | None = "20261114_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_quota_reservations",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("group_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_tokens", sa.BigInteger, nullable=False),
        sa.CheckConstraint("input_tokens > 0", name="ck_provider_quota_positive"),
    )
    op.create_index(
        "ix_provider_quota_group_time", "provider_quota_reservations", ["group_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("provider_quota_reservations")
