"""Index notebook text sources, document extractions, and completed page OCR."""

from collections.abc import Sequence

from alembic import op

revision: str = "20261122_0023"
down_revision: str | None = "20261122_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX ix_workspace_text_keyword ON workspace_text_sources
        USING gin (to_tsvector('simple'::regconfig, text))
        """)
    op.execute("""
        CREATE INDEX ix_workspace_document_keyword ON workspace_documents
        USING gin (jsonb_to_tsvector('simple'::regconfig, sections, '["string"]'::jsonb))
        """)
    op.execute("""
        CREATE INDEX ix_workspace_page_ocr_keyword ON workspace_page_ocr
        USING gin (to_tsvector('simple'::regconfig, text)) WHERE status = 'succeeded'
        """)


def downgrade() -> None:
    op.drop_index("ix_workspace_page_ocr_keyword")
    op.drop_index("ix_workspace_document_keyword")
    op.drop_index("ix_workspace_text_keyword")
