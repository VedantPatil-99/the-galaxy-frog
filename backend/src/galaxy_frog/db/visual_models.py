"""Versioned frame embedding rows; source identities remain in video_frames/scenes."""

from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from galaxy_frog.db.models import Base


class VisualEmbeddingRow(Base):
    __tablename__ = "visual_embeddings"

    frame_id: Mapped[UUID] = mapped_column(
        ForeignKey("video_frames.id", ondelete="CASCADE"), primary_key=True
    )
    collection_id: Mapped[UUID] = mapped_column(
        ForeignKey("embedding_collections.id", ondelete="RESTRICT"), primary_key=True
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=False)
