"""Add durable ingestion jobs, leases, and append-only events.

Revision ID: 20260905_0004
Revises: 20260831_0003
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260905_0004"
down_revision: str | None = "20260831_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the Phase 2 durable job projection and immutable event log."""

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("video_id", sa.Uuid(), nullable=True),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("last_error_retryable", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt >= 0", name="ck_ingestion_jobs_attempt_nonnegative"),
        sa.CheckConstraint(
            "(status = 'running' AND lease_owner IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL) "
            "OR (status <> 'running' AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL AND heartbeat_at IS NULL)",
            name="ck_ingestion_jobs_lease_state",
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'failed', 'cancelled') AND completed_at IS NOT NULL) "
            "OR (status NOT IN ('succeeded', 'failed', 'cancelled') AND completed_at IS NULL)",
            name="ck_ingestion_jobs_completion_state",
        ),
        sa.CheckConstraint(
            "status <> 'succeeded' OR (stage = 'completed' AND video_id IS NOT NULL)",
            name="ck_ingestion_jobs_success_stage",
        ),
        sa.CheckConstraint(
            "(status = 'failed' AND last_error_code IS NOT NULL "
            "AND last_error_message IS NOT NULL AND last_error_retryable IS NOT NULL) "
            "OR (status <> 'failed' AND last_error_code IS NULL "
            "AND last_error_message IS NULL AND last_error_retryable IS NULL)",
            name="ck_ingestion_jobs_error_state",
        ),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("input_fingerprint", name="uq_ingestion_jobs_input_fingerprint"),
    )
    op.create_index(
        "ix_ingestion_jobs_claimable",
        "ingestion_jobs",
        ["status", "lease_expires_at", "created_at"],
    )
    op.create_index("ix_ingestion_jobs_video_id", "ingestion_jobs", ["video_id"])
    op.create_table(
        "job_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.CheckConstraint("sequence > 0", name="ck_job_events_sequence_positive"),
        sa.CheckConstraint("attempt >= 0", name="ck_job_events_attempt_nonnegative"),
        sa.CheckConstraint(
            "(event_type = 'failed' AND error_code IS NOT NULL AND retryable IS NOT NULL) "
            "OR (event_type <> 'failed' AND error_code IS NULL AND retryable IS NULL)",
            name="ck_job_events_error_state",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["ingestion_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "sequence", name="uq_job_events_sequence"),
    )
    op.create_index("ix_job_events_job_id", "job_events", ["job_id"])


def downgrade() -> None:
    """Remove durable ingestion records without touching Phase 1 data."""

    op.drop_index("ix_job_events_job_id", table_name="job_events")
    op.drop_table("job_events")
    op.drop_index("ix_ingestion_jobs_video_id", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_claimable", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
