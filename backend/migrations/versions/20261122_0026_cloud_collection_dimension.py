"""Allow notebook cloud collection metadata at 1,536 dimensions."""

from collections.abc import Sequence

from alembic import op

revision: str = "20261122_0026"
down_revision: str | None = "20261122_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_embedding_collection_dimension", "embedding_collections", type_="check")
    op.create_check_constraint(
        "ck_embedding_collection_dimension",
        "embedding_collections",
        "dimension IN (768, 1024, 1536)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_embedding_collection_dimension", "embedding_collections", type_="check")
    op.create_check_constraint(
        "ck_embedding_collection_dimension", "embedding_collections", "dimension IN (768, 1024)"
    )
