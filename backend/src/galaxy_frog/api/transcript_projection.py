"""Map stored evidence to the shared transcript response fields."""

from galaxy_frog.api.video_schemas import (
    RetrievalUnitResponse,
    TranscriptCueResponse,
    TranscriptEvidenceResponse,
    TranscriptionRunResponse,
)
from galaxy_frog.domain.videos.records import TranscriptRecord


def transcript_evidence(record: TranscriptRecord) -> TranscriptEvidenceResponse:
    transcription = record.transcription
    return TranscriptEvidenceResponse(
        cues=[
            TranscriptCueResponse(
                cue_id=cue.cue_id,
                source_order=cue.source_order,
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                text=cue.text,
                language_code=cue.language_code,
                origin=cue.origin.value,
                track_id=cue.track_id,
                caption_kind=cue.caption_kind.value if cue.caption_kind is not None else None,
                transcription_run_id=cue.transcription_run_id,
                confidence=cue.confidence,
                confidence_method=cue.confidence_method,
            )
            for cue in record.cues
        ],
        retrieval_units=[
            RetrievalUnitResponse(
                retrieval_unit_id=unit.unit_id,
                start_ms=unit.start_ms,
                end_ms=unit.end_ms,
                text=unit.text,
                cue_ids=list(unit.cue_ids),
            )
            for unit in record.units
        ],
        transcription=(
            TranscriptionRunResponse(
                run_id=transcription.run_id,
                job_id=transcription.result.job_id,
                audio_asset_id=transcription.audio_asset_id,
                audio_attempt=transcription.result.attempt,
                fallback_reason=transcription.result.fallback_reason.value,
                audio_start_ms=transcription.result.audio_start_ms,
                audio_end_ms=transcription.result.audio_end_ms,
                provider=transcription.result.spec.provider,
                provider_revision=transcription.result.spec.provider_revision,
                model=transcription.result.spec.model,
                model_revision=transcription.result.spec.model_revision,
                device=transcription.result.spec.device.value,
                compute_type=transcription.result.spec.compute_type.value,
                language_code=transcription.result.language_code,
                language_confidence=transcription.result.language_confidence,
                language_confidence_method=transcription.result.language_confidence_method,
                processing_seconds=transcription.result.processing_seconds,
                transcribed_at=transcription.result.transcribed_at,
            )
            if transcription is not None
            else None
        ),
    )
