"""Add automatically maintained lexical transcript search.

Revision ID: 20260927_0006
Revises: 20260910_0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260927_0006"
down_revision: str | None = "20260910_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Backfill existing units and maintain new text without rerunning ingestion."""

    op.add_column(
        "retrieval_units",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple'::regconfig, text)", persisted=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_retrieval_units_search_vector",
        "retrieval_units",
        ["search_vector"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    """Remove only the derived lexical index and column."""

    op.drop_index("ix_retrieval_units_search_vector", table_name="retrieval_units")
    op.drop_column("retrieval_units", "search_vector")
