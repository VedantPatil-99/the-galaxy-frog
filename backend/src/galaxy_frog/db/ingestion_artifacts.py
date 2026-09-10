"""PostgreSQL persistence for resumable audio and transcription outputs."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.db.models import (
    IngestionJobRow,
    MediaAssetRow,
    TranscriptionRunCueRow,
    TranscriptionRunRow,
)
from galaxy_frog.domain.media import AcquiredAudio, AudioAsset, AudioFallbackReason
from galaxy_frog.domain.transcription import (
    TranscriptionCheckpoint,
    TranscriptionComputeType,
    TranscriptionCue,
    TranscriptionDevice,
    TranscriptionProviderSpec,
    TranscriptionResult,
)
from galaxy_frog.domain.videos.models import SourceReference, VideoSourceKind

_AUDIO_KIND = "audio"


class IngestionArtifactRepositoryError(RuntimeError):
    """Raised when a durable stage output is missing or inconsistent."""


class PostgresAudioAssetRepository:
    """Store local media metadata while keeping filesystem paths out of job events."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, audio: AcquiredAudio) -> AudioAsset:
        asset_id = uuid4()
        statement = (
            insert(MediaAssetRow)
            .values(
                id=asset_id,
                job_id=audio.job_id,
                attempt=audio.attempt,
                asset_kind=_AUDIO_KIND,
                fallback_reason=audio.fallback_reason,
                storage_path=str(audio.path),
                start_ms=audio.start_ms,
                end_ms=audio.end_ms,
                size_bytes=audio.size_bytes,
                media_type=audio.media_type,
                codec=audio.codec,
                sample_rate_hz=audio.sample_rate_hz,
                channels=audio.channels,
                downloader=audio.downloader,
                downloader_revision=audio.downloader_revision,
                normalizer=audio.normalizer,
                normalizer_revision=audio.normalizer_revision,
                acquired_at=audio.acquired_at,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    MediaAssetRow.job_id,
                    MediaAssetRow.attempt,
                    MediaAssetRow.asset_kind,
                ]
            )
            .returning(MediaAssetRow.id)
        )
        inserted_id = await self._session.scalar(statement)
        if inserted_id is not None:
            await self._session.commit()
            return AudioAsset(inserted_id, audio)

        row = await self._session.scalar(
            select(MediaAssetRow).where(
                MediaAssetRow.job_id == audio.job_id,
                MediaAssetRow.attempt == audio.attempt,
                MediaAssetRow.asset_kind == _AUDIO_KIND,
            )
        )
        await self._session.commit()
        if row is None:
            raise IngestionArtifactRepositoryError(
                "The idempotent audio checkpoint could not be loaded."
            )
        return self._audio_asset(row, audio.source)

    async def get_latest_available(self, job_id: UUID) -> AudioAsset | None:
        row = await self._session.scalar(
            select(MediaAssetRow)
            .where(
                MediaAssetRow.job_id == job_id,
                MediaAssetRow.asset_kind == _AUDIO_KIND,
                MediaAssetRow.deleted_at.is_(None),
            )
            .order_by(MediaAssetRow.attempt.desc())
            .limit(1)
        )
        if row is None:
            return None
        return self._audio_asset(row, await self._source(job_id))

    async def mark_deleted(
        self,
        asset_id: UUID,
        *,
        deleted_at: datetime | None = None,
    ) -> AudioAsset:
        row = await self._session.get(MediaAssetRow, asset_id)
        if row is None:
            raise IngestionArtifactRepositoryError("The audio checkpoint does not exist.")
        row.deleted_at = row.deleted_at or deleted_at or datetime.now(UTC)
        source = await self._source(row.job_id)
        await self._session.commit()
        return self._audio_asset(row, source)

    async def _source(self, job_id: UUID) -> SourceReference:
        row = await self._session.get(IngestionJobRow, job_id)
        if row is None:
            raise IngestionArtifactRepositoryError(
                "The ingestion job for this stage output does not exist."
            )
        return SourceReference(
            VideoSourceKind(row.source_kind),
            row.external_id,
            row.canonical_url,
        )

    @staticmethod
    def _audio_asset(row: MediaAssetRow, source: SourceReference) -> AudioAsset:
        return AudioAsset(
            asset_id=row.id,
            audio=AcquiredAudio(
                job_id=row.job_id,
                attempt=row.attempt,
                source=source,
                fallback_reason=AudioFallbackReason(row.fallback_reason),
                path=Path(row.storage_path),
                start_ms=row.start_ms,
                end_ms=row.end_ms,
                size_bytes=row.size_bytes,
                media_type=row.media_type,
                codec=row.codec,
                sample_rate_hz=row.sample_rate_hz,
                channels=row.channels,
                downloader=row.downloader,
                downloader_revision=row.downloader_revision,
                normalizer=row.normalizer,
                normalizer_revision=row.normalizer_revision,
                acquired_at=row.acquired_at,
            ),
            deleted_at=row.deleted_at,
        )


class PostgresTranscriptionCheckpointRepository:
    """Store one complete ASR result per durable ingestion job."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(
        self,
        audio_asset_id: UUID,
        result: TranscriptionResult,
    ) -> TranscriptionCheckpoint:
        run_id = uuid4()
        statement = (
            insert(TranscriptionRunRow)
            .values(
                id=run_id,
                job_id=result.job_id,
                audio_asset_id=audio_asset_id,
                audio_attempt=result.attempt,
                fallback_reason=result.fallback_reason,
                audio_start_ms=result.audio_start_ms,
                audio_end_ms=result.audio_end_ms,
                provider=result.spec.provider,
                provider_revision=result.spec.provider_revision,
                model=result.spec.model,
                model_revision=result.spec.model_revision,
                device=result.spec.device,
                compute_type=result.spec.compute_type,
                language_code=result.language_code,
                language_confidence=result.language_confidence,
                language_confidence_method=result.language_confidence_method,
                processing_seconds=result.processing_seconds,
                transcribed_at=result.transcribed_at,
            )
            .on_conflict_do_nothing(index_elements=[TranscriptionRunRow.job_id])
            .returning(TranscriptionRunRow.id)
        )
        inserted_id = await self._session.scalar(statement)
        if inserted_id is not None:
            self._session.add_all(
                TranscriptionRunCueRow(
                    run_id=inserted_id,
                    source_order=cue.source_order,
                    start_ms=cue.start_ms,
                    end_ms=cue.end_ms,
                    text=cue.text,
                    confidence=cue.confidence,
                    confidence_method=cue.confidence_method,
                )
                for cue in result.cues
            )
            await self._session.commit()
            return TranscriptionCheckpoint(inserted_id, audio_asset_id, result)

        await self._session.commit()
        existing = await self.get(result.job_id)
        if existing is None:
            raise IngestionArtifactRepositoryError(
                "The idempotent transcription checkpoint could not be loaded."
            )
        return existing

    async def get(self, job_id: UUID) -> TranscriptionCheckpoint | None:
        row = await self._session.scalar(
            select(TranscriptionRunRow).where(TranscriptionRunRow.job_id == job_id)
        )
        if row is None:
            return None
        cue_rows = tuple(
            (
                await self._session.scalars(
                    select(TranscriptionRunCueRow)
                    .where(TranscriptionRunCueRow.run_id == row.id)
                    .order_by(TranscriptionRunCueRow.source_order)
                )
            ).all()
        )
        source = await self._source(job_id)
        result = TranscriptionResult(
            job_id=row.job_id,
            attempt=row.audio_attempt,
            source=source,
            fallback_reason=AudioFallbackReason(row.fallback_reason),
            audio_start_ms=row.audio_start_ms,
            audio_end_ms=row.audio_end_ms,
            spec=TranscriptionProviderSpec(
                provider=row.provider,
                provider_revision=row.provider_revision,
                model=row.model,
                model_revision=row.model_revision,
                device=TranscriptionDevice(row.device),
                compute_type=TranscriptionComputeType(row.compute_type),
            ),
            language_code=row.language_code,
            language_confidence=row.language_confidence,
            language_confidence_method=row.language_confidence_method,
            cues=tuple(
                TranscriptionCue(
                    source_order=cue.source_order,
                    start_ms=cue.start_ms,
                    end_ms=cue.end_ms,
                    text=cue.text,
                    confidence=cue.confidence,
                    confidence_method=cue.confidence_method,
                )
                for cue in cue_rows
            ),
            processing_seconds=row.processing_seconds,
            transcribed_at=row.transcribed_at,
        )
        return TranscriptionCheckpoint(row.id, row.audio_asset_id, result)

    async def _source(self, job_id: UUID) -> SourceReference:
        row = await self._session.get(IngestionJobRow, job_id)
        if row is None:
            raise IngestionArtifactRepositoryError(
                "The ingestion job for this stage output does not exist."
            )
        return SourceReference(
            VideoSourceKind(row.source_kind),
            row.external_id,
            row.canonical_url,
        )
