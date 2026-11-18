"""Pin provider profile and scoped consent to each durable Study run."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261118_0017"
down_revision: str | None = "20261118_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("artifact_generation_runs", sa.Column("provider_selection", sa.JSON))


def downgrade() -> None:
    op.drop_column("artifact_generation_runs", "provider_selection")
