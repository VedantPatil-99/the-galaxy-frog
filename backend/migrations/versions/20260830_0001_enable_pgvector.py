"""Enable pgvector in a Supabase-compatible schema.

Revision ID: 20260830_0001
Revises:
Create Date: 2026-08-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260830_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the extensions schema and enable pgvector."""

    op.execute("CREATE SCHEMA IF NOT EXISTS extensions")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions")


def downgrade() -> None:
    """Disable pgvector while preserving the shared extensions schema."""

    op.execute("DROP EXTENSION IF EXISTS vector")
