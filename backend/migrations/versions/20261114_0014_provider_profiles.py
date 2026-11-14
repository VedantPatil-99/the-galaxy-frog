"""Persist immutable provider profile versions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261114_0014"
down_revision: str | None = "20261107_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_profile_versions",
        sa.Column("profile_id", sa.String(40), primary_key=True),
        sa.Column("version", sa.Integer, primary_key=True),
        sa.Column("policy", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_provider_profile_version"),
    )


def downgrade() -> None:
    op.drop_table("provider_profile_versions")
