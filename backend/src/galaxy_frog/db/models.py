"""SQLAlchemy mappings for caption-first video persistence."""

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
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
    __table_args__ = (UniqueConstraint("video_id", "source_order", name="uq_cues_video_order"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    video_id: Mapped[UUID] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    track_id: Mapped[str] = mapped_column(String(255), nullable=False)
    language_code: Mapped[str] = mapped_column(String(64), nullable=False)
    caption_kind: Mapped[str] = mapped_column(String(32), nullable=False)
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
