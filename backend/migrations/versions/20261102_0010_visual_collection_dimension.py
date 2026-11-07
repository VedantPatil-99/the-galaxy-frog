"""Permit the separately versioned 768-dimensional visual collection metadata.

Existing transcript/OCR vector columns remain exactly 1024-dimensional.
Revision ID: 20261102_0010
Revises: 20261102_0009
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261102_0010"
down_revision: str | None = "20261102_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_embedding_collection_dimension", "embedding_collections", type_="check")
    op.create_check_constraint(
        "ck_embedding_collection_dimension", "embedding_collections", "dimension IN (768, 1024)"
    )


def downgrade() -> None:
    # PostgreSQL rejects this downgrade while incompatible collection rows remain.
    op.drop_constraint("ck_embedding_collection_dimension", "embedding_collections", type_="check")
    op.create_check_constraint(
        "ck_embedding_collection_dimension", "embedding_collections", "dimension = 1024"
    )
