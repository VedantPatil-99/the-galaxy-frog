"""SQLAlchemy mappings for caption-first video persistence."""

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative metadata root used by migrations and repositories."""


class VideoRow(Base):
    __tablename__ = "videos"
    __table_args__ = (UniqueConstraint("source_kind", "external_id", name="uq_videos_source"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    duration_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_name: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TranscriptCueRow(Base):
    __tablename__ = "transcript_cues"
    __table_args__ = (
        UniqueConstraint("video_id", "source_order", name="uq_cues_video_order"),
        CheckConstraint(
            "(origin = 'caption' AND track_id IS NOT NULL AND caption_kind IS NOT NULL "
            "AND transcription_run_id IS NULL) OR "
            "(origin = 'asr' AND track_id IS NULL AND caption_kind IS NULL "
            "AND transcription_run_id IS NOT NULL)",
            name="ck_transcript_cues_origin",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_transcript_cues_confidence",
        ),
        CheckConstraint(
            "(confidence IS NULL AND confidence_method IS NULL) OR "
            "(confidence IS NOT NULL AND confidence_method IS NOT NULL)",
            name="ck_transcript_cues_confidence_method",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    video_id: Mapped[UUID] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    origin: Mapped[str] = mapped_column(String(32), nullable=False, default="caption")
    track_id: Mapped[str | None] = mapped_column(String(255))
    language_code: Mapped[str] = mapped_column(String(64), nullable=False)
    caption_kind: Mapped[str | None] = mapped_column(String(32))
    transcription_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("transcription_runs.id", ondelete="RESTRICT"), index=True
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    confidence_method: Mapped[str | None] = mapped_column(String(64))
    source_order: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)


class RetrievalUnitRow(Base):
    __tablename__ = "retrieval_units"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    video_id: Mapped[UUID] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    start_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)


class RetrievalUnitCueRow(Base):
    __tablename__ = "retrieval_unit_cues"

    retrieval_unit_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_units.id", ondelete="CASCADE"), primary_key=True
    )
    cue_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_cues.id", ondelete="CASCADE"), primary_key=True
    )
    cue_order: Mapped[int] = mapped_column(Integer, nullable=False)


class EmbeddingCollectionRow(Base):
    __tablename__ = "embedding_collections"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "model",
            "revision",
            "dimension",
            "normalization",
            name="uq_embedding_collection_identity",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    revision: Mapped[str] = mapped_column(String(255), nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    normalization: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TextEmbeddingRow(Base):
    __tablename__ = "text_embeddings"

    retrieval_unit_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_units.id", ondelete="CASCADE"), primary_key=True
    )
    collection_id: Mapped[UUID] = mapped_column(
        ForeignKey("embedding_collections.id", ondelete="RESTRICT"), primary_key=True
    )
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)


class IngestionJobRow(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        UniqueConstraint("input_fingerprint", name="uq_ingestion_jobs_input_fingerprint"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    video_id: Mapped[UUID | None] = mapped_column(ForeignKey("videos.id", ondelete="SET NULL"))
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    last_error_retryable: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JobEventRow(Base):
    __tablename__ = "job_events"
    __table_args__ = (UniqueConstraint("job_id", "sequence", name="uq_job_events_sequence"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("ingestion_jobs.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
    retryable: Mapped[bool | None] = mapped_column(Boolean)
    details: Mapped[dict[str, object] | None] = mapped_column(JSON)


class MediaAssetRow(Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        UniqueConstraint("job_id", "attempt", "asset_kind", name="uq_media_assets_attempt_kind"),
        CheckConstraint("attempt > 0", name="ck_media_assets_attempt_positive"),
        CheckConstraint("start_ms >= 0 AND end_ms > start_ms", name="ck_media_assets_interval"),
        CheckConstraint("size_bytes > 0", name="ck_media_assets_size_positive"),
        CheckConstraint("sample_rate_hz > 0", name="ck_media_assets_sample_rate_positive"),
        CheckConstraint("channels > 0", name="ck_media_assets_channels_positive"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("ingestion_jobs.id", ondelete="CASCADE"), index=True
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    asset_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    fallback_reason: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    start_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    codec: Mapped[str] = mapped_column(String(64), nullable=False)
    sample_rate_hz: Mapped[int] = mapped_column(Integer, nullable=False)
    channels: Mapped[int] = mapped_column(Integer, nullable=False)
    downloader: Mapped[str] = mapped_column(String(64), nullable=False)
    downloader_revision: Mapped[str] = mapped_column(String(255), nullable=False)
    normalizer: Mapped[str] = mapped_column(String(64), nullable=False)
    normalizer_revision: Mapped[str] = mapped_column(String(255), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TranscriptionRunRow(Base):
    __tablename__ = "transcription_runs"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_transcription_runs_job"),
        UniqueConstraint("audio_asset_id", name="uq_transcription_runs_audio_asset"),
        CheckConstraint("audio_attempt > 0", name="ck_transcription_runs_attempt_positive"),
        CheckConstraint(
            "audio_start_ms >= 0 AND audio_end_ms > audio_start_ms",
            name="ck_transcription_runs_interval",
        ),
        CheckConstraint(
            "language_confidence IS NULL OR "
            "(language_confidence >= 0 AND language_confidence <= 1)",
            name="ck_transcription_runs_language_confidence",
        ),
        CheckConstraint(
            "(language_confidence IS NULL AND language_confidence_method IS NULL) OR "
            "(language_confidence IS NOT NULL AND language_confidence_method IS NOT NULL)",
            name="ck_transcription_runs_language_confidence_method",
        ),
        CheckConstraint(
            "processing_seconds >= 0", name="ck_transcription_runs_processing_nonnegative"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("ingestion_jobs.id", ondelete="CASCADE"), index=True
    )
    audio_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("media_assets.id", ondelete="RESTRICT"), nullable=False
    )
    audio_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    fallback_reason: Mapped[str] = mapped_column(String(32), nullable=False)
    audio_start_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    audio_end_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_revision: Mapped[str] = mapped_column(String(255), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    model_revision: Mapped[str] = mapped_column(String(255), nullable=False)
    device: Mapped[str] = mapped_column(String(32), nullable=False)
    compute_type: Mapped[str] = mapped_column(String(32), nullable=False)
    language_code: Mapped[str] = mapped_column(String(64), nullable=False)
    language_confidence: Mapped[float | None] = mapped_column(Float)
    language_confidence_method: Mapped[str | None] = mapped_column(String(64))
    processing_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    transcribed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TranscriptionRunCueRow(Base):
    __tablename__ = "transcription_run_cues"
    __table_args__ = (
        UniqueConstraint("run_id", "source_order", name="uq_transcription_run_cues_order"),
        CheckConstraint("source_order >= 0", name="ck_transcription_run_cues_order_nonnegative"),
        CheckConstraint(
            "start_ms >= 0 AND end_ms > start_ms",
            name="ck_transcription_run_cues_interval",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_transcription_run_cues_confidence",
        ),
        CheckConstraint(
            "(confidence IS NULL AND confidence_method IS NULL) OR "
            "(confidence IS NOT NULL AND confidence_method IS NOT NULL)",
            name="ck_transcription_run_cues_confidence_method",
        ),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("transcription_runs.id", ondelete="CASCADE"), primary_key=True
    )
    source_order: Mapped[int] = mapped_column(Integer, primary_key=True)
    start_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    confidence_method: Mapped[str | None] = mapped_column(String(64))
