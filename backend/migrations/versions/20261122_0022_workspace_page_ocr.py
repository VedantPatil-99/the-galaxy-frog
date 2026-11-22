"""Record page-scoped OCR consent and immutable OCR evidence."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261122_0022"
down_revision: str | None = "20261122_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_page_ocr",
        sa.Column(
            "version_id",
            sa.Uuid(),
            sa.ForeignKey("workspace_documents.version_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("page_number", sa.Integer(), primary_key=True),
        sa.Column(
            "approved_by",
            sa.Uuid(),
            sa.ForeignKey("workspace_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_job_id", sa.String(100), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("spans", JSONB(), nullable=True),
        sa.CheckConstraint("page_number BETWEEN 1 AND 80", name="ck_workspace_ocr_page"),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'no_text', 'failed', 'unknown')",
            name="ck_workspace_ocr_status",
        ),
        sa.CheckConstraint(
            "(status = 'running') = (completed_at IS NULL)", name="ck_workspace_ocr_completed"
        ),
        sa.CheckConstraint(
            "(status = 'succeeded' AND text IS NOT NULL AND spans IS NOT NULL) "
            "OR (status <> 'succeeded' AND text IS NULL AND spans IS NULL)",
            name="ck_workspace_ocr_evidence",
        ),
    )
    op.execute("ALTER TABLE workspace_page_ocr ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("workspace_page_ocr")
