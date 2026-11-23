"""Store owner-scoped metadata and extraction coordinates for uploaded documents."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261122_0021"
down_revision: str | None = "20261122_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column(
            "notebook_id",
            sa.Uuid(),
            sa.ForeignKey("workspace_notebooks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(160), nullable=False),
        sa.Column("format", sa.String(8), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("section_count", sa.Integer(), nullable=False),
        sa.Column("pages_without_text", sa.Integer(), nullable=True),
        sa.Column("sections", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "format IN ('pdf', 'docx', 'plain', 'markdown')", name="ck_workspace_document_format"
        ),
        sa.CheckConstraint("size_bytes BETWEEN 1 AND 8388608", name="ck_workspace_document_size"),
        sa.CheckConstraint(
            "section_count BETWEEN 1 AND 80 AND section_count = jsonb_array_length(sections)",
            name="ck_workspace_document_sections",
        ),
        sa.CheckConstraint(
            "(format = 'pdf' AND pages_without_text IS NOT NULL "
            "AND pages_without_text BETWEEN 0 AND section_count) "
            "OR (format <> 'pdf' AND section_count = 1 AND pages_without_text IS NULL)",
            name="ck_workspace_document_pages",
        ),
        sa.UniqueConstraint("notebook_id", "filename", "file_sha256", name="uq_workspace_document"),
    )
    op.create_index(
        "ix_workspace_document_notebook_source", "workspace_documents", ["notebook_id", "id"]
    )
    op.execute("ALTER TABLE workspace_documents ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("workspace_documents")
