"""Persist Phase 7 generation runs and immutable artifact versions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261107_0013"
down_revision: str | None = "20261102_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KINDS = "kind IN ('chapters', 'summary', 'notes', 'flashcards', 'quiz')"


def upgrade() -> None:
    op.create_table(
        "generated_artifacts",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "video_id", sa.Uuid, sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("chapter_version_id", sa.Uuid, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.Integer, nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("document", sa.JSON, nullable=False),
        sa.UniqueConstraint("video_id", "kind", "version", name="uq_study_version"),
        sa.UniqueConstraint("video_id", "id", name="uq_study_video_artifact"),
        sa.ForeignKeyConstraint(
            ["video_id", "chapter_version_id"],
            ["generated_artifacts.video_id", "generated_artifacts.id"],
        ),
        sa.CheckConstraint(_KINDS, name="ck_study_artifact_kind"),
        sa.CheckConstraint(
            "(kind = 'chapters') = (chapter_version_id IS NULL)", name="ck_study_artifact_chapters"
        ),
        sa.CheckConstraint("version > 0 AND schema_version > 0", name="ck_study_version_positive"),
        sa.CheckConstraint("octet_length(document::text) <= 524288", name="ck_study_artifact_size"),
    )
    op.create_table(
        "artifact_generation_runs",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "video_id", sa.Uuid, sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("request_id", sa.Uuid, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("chapter_version_id", sa.Uuid, nullable=True),
        sa.Column("parent_id", sa.Uuid, nullable=True),
        sa.Column("result_id", sa.Uuid, nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.Uuid, nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("completed_chapters", sa.Integer, nullable=False),
        sa.Column("total_chapters", sa.Integer, nullable=False),
        sa.Column("warnings", sa.JSON, nullable=False),
        sa.Column("failure", sa.JSON, nullable=True),
        sa.UniqueConstraint("video_id", "request_id", name="uq_study_request"),
        sa.ForeignKeyConstraint(
            ["video_id", "chapter_version_id"],
            ["generated_artifacts.video_id", "generated_artifacts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["video_id", "parent_id"], ["generated_artifacts.video_id", "generated_artifacts.id"]
        ),
        sa.ForeignKeyConstraint(
            ["video_id", "result_id"], ["generated_artifacts.video_id", "generated_artifacts.id"]
        ),
        sa.CheckConstraint(_KINDS, name="ck_study_run_kind"),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed')", name="ck_study_run_state"
        ),
        sa.CheckConstraint(
            "attempts BETWEEN 0 AND 2 AND completed_chapters BETWEEN 0 AND total_chapters "
            "AND total_chapters BETWEEN 0 AND 20",
            name="ck_study_run_progress",
        ),
        sa.CheckConstraint(
            "(state = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(state <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_study_run_lease",
        ),
    )
    op.create_index(
        "uq_study_active_run",
        "artifact_generation_runs",
        ["video_id", "kind"],
        unique=True,
        postgresql_where=sa.text("state IN ('queued', 'running')"),
    )
    op.create_index("ix_study_run_claim", "artifact_generation_runs", ["state", "created_at"])
    op.create_index(
        "ix_study_run_latest", "artifact_generation_runs", ["video_id", "kind", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("artifact_generation_runs")
    op.drop_table("generated_artifacts")
