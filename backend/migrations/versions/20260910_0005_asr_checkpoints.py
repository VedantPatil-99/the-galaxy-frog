"""Add durable audio and transcription checkpoints with final cue provenance.

Revision ID: 20260910_0005
Revises: 20260905_0004
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_0005"
down_revision: str | None = "20260905_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create internal retry checkpoints and distinguish caption cues from ASR evidence."""

    op.create_table(
        "media_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("asset_kind", sa.String(length=32), nullable=False),
        sa.Column("fallback_reason", sa.String(length=32), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("codec", sa.String(length=64), nullable=False),
        sa.Column("sample_rate_hz", sa.Integer(), nullable=False),
        sa.Column("channels", sa.Integer(), nullable=False),
        sa.Column("downloader", sa.String(length=64), nullable=False),
        sa.Column("downloader_revision", sa.String(length=255), nullable=False),
        sa.Column("normalizer", sa.String(length=64), nullable=False),
        sa.Column("normalizer_revision", sa.String(length=255), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt > 0", name="ck_media_assets_attempt_positive"),
        sa.CheckConstraint("start_ms >= 0 AND end_ms > start_ms", name="ck_media_assets_interval"),
        sa.CheckConstraint("size_bytes > 0", name="ck_media_assets_size_positive"),
        sa.CheckConstraint("sample_rate_hz > 0", name="ck_media_assets_sample_rate_positive"),
        sa.CheckConstraint("channels > 0", name="ck_media_assets_channels_positive"),
        sa.ForeignKeyConstraint(["job_id"], ["ingestion_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "attempt", "asset_kind", name="uq_media_assets_attempt_kind"),
    )
    op.create_index("ix_media_assets_job_id", "media_assets", ["job_id"])

    op.create_table(
        "transcription_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("audio_asset_id", sa.Uuid(), nullable=False),
        sa.Column("audio_attempt", sa.Integer(), nullable=False),
        sa.Column("fallback_reason", sa.String(length=32), nullable=False),
        sa.Column("audio_start_ms", sa.BigInteger(), nullable=False),
        sa.Column("audio_end_ms", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_revision", sa.String(length=255), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("model_revision", sa.String(length=255), nullable=False),
        sa.Column("device", sa.String(length=32), nullable=False),
        sa.Column("compute_type", sa.String(length=32), nullable=False),
        sa.Column("language_code", sa.String(length=64), nullable=False),
        sa.Column("language_confidence", sa.Float(), nullable=True),
        sa.Column("language_confidence_method", sa.String(length=64), nullable=True),
        sa.Column("processing_seconds", sa.Float(), nullable=False),
        sa.Column("transcribed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("audio_attempt > 0", name="ck_transcription_runs_attempt_positive"),
        sa.CheckConstraint(
            "audio_start_ms >= 0 AND audio_end_ms > audio_start_ms",
            name="ck_transcription_runs_interval",
        ),
        sa.CheckConstraint(
            "language_confidence IS NULL OR "
            "(language_confidence >= 0 AND language_confidence <= 1)",
            name="ck_transcription_runs_language_confidence",
        ),
        sa.CheckConstraint(
            "(language_confidence IS NULL AND language_confidence_method IS NULL) OR "
            "(language_confidence IS NOT NULL AND language_confidence_method IS NOT NULL)",
            name="ck_transcription_runs_language_confidence_method",
        ),
        sa.CheckConstraint(
            "processing_seconds >= 0", name="ck_transcription_runs_processing_nonnegative"
        ),
        sa.ForeignKeyConstraint(["job_id"], ["ingestion_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["audio_asset_id"], ["media_assets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_transcription_runs_job"),
        sa.UniqueConstraint("audio_asset_id", name="uq_transcription_runs_audio_asset"),
    )
    op.create_index("ix_transcription_runs_job_id", "transcription_runs", ["job_id"])
    op.create_table(
        "transcription_run_cues",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("source_order", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.BigInteger(), nullable=False),
        sa.Column("end_ms", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("confidence_method", sa.String(length=64), nullable=True),
        sa.CheckConstraint("source_order >= 0", name="ck_transcription_run_cues_order_nonnegative"),
        sa.CheckConstraint(
            "start_ms >= 0 AND end_ms > start_ms",
            name="ck_transcription_run_cues_interval",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_transcription_run_cues_confidence",
        ),
        sa.CheckConstraint(
            "(confidence IS NULL AND confidence_method IS NULL) OR "
            "(confidence IS NOT NULL AND confidence_method IS NOT NULL)",
            name="ck_transcription_run_cues_confidence_method",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["transcription_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "source_order"),
        sa.UniqueConstraint("run_id", "source_order", name="uq_transcription_run_cues_order"),
    )

    op.add_column(
        "transcript_cues",
        sa.Column("origin", sa.String(length=32), server_default="caption", nullable=False),
    )
    op.add_column("transcript_cues", sa.Column("transcription_run_id", sa.Uuid(), nullable=True))
    op.add_column("transcript_cues", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column(
        "transcript_cues", sa.Column("confidence_method", sa.String(length=64), nullable=True)
    )
    op.alter_column("transcript_cues", "track_id", existing_type=sa.String(255), nullable=True)
    op.alter_column("transcript_cues", "caption_kind", existing_type=sa.String(32), nullable=True)
    op.create_foreign_key(
        "fk_transcript_cues_transcription_run",
        "transcript_cues",
        "transcription_runs",
        ["transcription_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_transcript_cues_transcription_run_id",
        "transcript_cues",
        ["transcription_run_id"],
    )
    op.create_check_constraint(
        "ck_transcript_cues_origin",
        "transcript_cues",
        "(origin = 'caption' AND track_id IS NOT NULL AND caption_kind IS NOT NULL "
        "AND transcription_run_id IS NULL) OR "
        "(origin = 'asr' AND track_id IS NULL AND caption_kind IS NULL "
        "AND transcription_run_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_transcript_cues_confidence",
        "transcript_cues",
        "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
    )
    op.create_check_constraint(
        "ck_transcript_cues_confidence_method",
        "transcript_cues",
        "(confidence IS NULL AND confidence_method IS NULL) OR "
        "(confidence IS NOT NULL AND confidence_method IS NOT NULL)",
    )


def downgrade() -> None:
    """Remove ASR checkpoints while retaining caption-compatible transcript rows."""

    op.execute(
        "UPDATE transcript_cues SET track_id = 'legacy-asr', caption_kind = 'automatic' "
        "WHERE origin = 'asr'"
    )
    op.drop_constraint("ck_transcript_cues_confidence_method", "transcript_cues", type_="check")
    op.drop_constraint("ck_transcript_cues_confidence", "transcript_cues", type_="check")
    op.drop_constraint("ck_transcript_cues_origin", "transcript_cues", type_="check")
    op.drop_index("ix_transcript_cues_transcription_run_id", table_name="transcript_cues")
    op.drop_constraint(
        "fk_transcript_cues_transcription_run", "transcript_cues", type_="foreignkey"
    )
    op.alter_column("transcript_cues", "caption_kind", existing_type=sa.String(32), nullable=False)
    op.alter_column("transcript_cues", "track_id", existing_type=sa.String(255), nullable=False)
    op.drop_column("transcript_cues", "confidence_method")
    op.drop_column("transcript_cues", "confidence")
    op.drop_column("transcript_cues", "transcription_run_id")
    op.drop_column("transcript_cues", "origin")
    op.drop_table("transcription_run_cues")
    op.drop_index("ix_transcription_runs_job_id", table_name="transcription_runs")
    op.drop_table("transcription_runs")
    op.drop_index("ix_media_assets_job_id", table_name="media_assets")
    op.drop_table("media_assets")
