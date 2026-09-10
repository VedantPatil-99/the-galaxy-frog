import type { components } from "@/lib/api/generated/schema"

export type ApiErrorResponse = components["schemas"]["ErrorResponse"]
export type AnswerResponse = components["schemas"]["AnswerResponse"]
export type HealthResponse = components["schemas"]["HealthResponse"]
export type ImportVideoResponse = components["schemas"]["ImportVideoResponse"]
export type IngestionEventsResponse = components["schemas"]["IngestionEventsResponse"]
export type IngestionJobResponse = components["schemas"]["IngestionJobResponse"]
export type ReadinessResponse = components["schemas"]["ReadinessResponse"]
export type TranscriptCueResponse = components["schemas"]["TranscriptCueResponse"]
export type TranscriptResponse = components["schemas"]["TranscriptResponse"]
export type TranscriptionRunResponse = components["schemas"]["TranscriptionRunResponse"]
export type VideoResponse = components["schemas"]["VideoResponse"]

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}

export function isApiErrorResponse(value: unknown): value is ApiErrorResponse {
  if (!isRecord(value) || !isRecord(value.error)) {
    return false
  }

  const { error } = value
  return (
    typeof error.code === "string" &&
    typeof error.message === "string" &&
    typeof error.correlation_id === "string" &&
    typeof error.retryable === "boolean" &&
    (error.details === undefined ||
      error.details === null ||
      isRecord(error.details)) &&
    (error.suggested_action === undefined ||
      error.suggested_action === null ||
      typeof error.suggested_action === "string")
  )
}

export function isHealthResponse(value: unknown): value is HealthResponse {
  return isRecord(value) && value.status === "ok"
}

export function isReadinessResponse(
  value: unknown,
): value is ReadinessResponse {
  if (!isRecord(value) || value.status !== "ready") {
    return false
  }

  if (value.dependencies === undefined) {
    return true
  }

  return (
    isRecord(value.dependencies) &&
    Object.values(value.dependencies).every((status) => status === "ready")
  )
}

function isVideoResponse(value: unknown): value is VideoResponse {
  return (
    isRecord(value) &&
    typeof value.video_id === "string" &&
    typeof value.external_id === "string" &&
    typeof value.canonical_url === "string" &&
    typeof value.title === "string" &&
    typeof value.duration_ms === "number" &&
    typeof value.transcript_ready === "boolean" &&
    typeof value.index_ready === "boolean"
  )
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string")
}

function isNullableString(value: unknown): value is string | null | undefined {
  return value === undefined || value === null || typeof value === "string"
}

function isNullableBoolean(value: unknown): value is boolean | null | undefined {
  return value === undefined || value === null || typeof value === "boolean"
}

const ingestionStatuses = new Set(["queued", "running", "succeeded", "failed", "cancelled"])
const ingestionStages = new Set([
  "source_resolution",
  "metadata",
  "caption_retrieval",
  "audio_acquisition",
  "transcription",
  "chunking",
  "persistence",
  "embedding",
  "indexing",
  "cleanup",
  "completed",
])
const ingestionEventTypes = new Set([
  "created",
  "claimed",
  "heartbeat",
  "stage_started",
  "stage_completed",
  "retry_requested",
  "cancel_requested",
  "cancelled",
  "failed",
  "completed",
])

export function isIngestionJobResponse(
  value: unknown,
): value is IngestionJobResponse {
  return (
    isRecord(value) &&
    typeof value.job_id === "string" &&
    (value.source_kind === "youtube" || value.source_kind === "local_file") &&
    typeof value.external_id === "string" &&
    typeof value.canonical_url === "string" &&
    typeof value.input_fingerprint === "string" &&
    ingestionStatuses.has(String(value.status)) &&
    ingestionStages.has(String(value.stage)) &&
    typeof value.attempt === "number" &&
    isNullableString(value.video_id) &&
    isNullableString(value.cancel_requested_at) &&
    isNullableString(value.started_at) &&
    isNullableString(value.completed_at) &&
    isNullableString(value.last_error_code) &&
    isNullableString(value.last_error_message) &&
    isNullableBoolean(value.last_error_retryable) &&
    typeof value.created_at === "string" &&
    typeof value.updated_at === "string"
  )
}

export function isIngestionEventsResponse(
  value: unknown,
): value is IngestionEventsResponse {
  return (
    isRecord(value) &&
    typeof value.job_id === "string" &&
    Array.isArray(value.events) &&
    value.events.every(
      (event) =>
        isRecord(event) &&
        typeof event.event_id === "string" &&
        event.job_id === value.job_id &&
        typeof event.sequence === "number" &&
        ingestionEventTypes.has(String(event.event_type)) &&
        ingestionStages.has(String(event.stage)) &&
        typeof event.attempt === "number" &&
        typeof event.occurred_at === "string" &&
        isNullableString(event.message) &&
        isNullableString(event.error_code) &&
        isNullableBoolean(event.retryable) &&
        (event.details === undefined || event.details === null || isRecord(event.details)),
    )
  )
}

function isTranscriptCue(value: unknown): value is TranscriptCueResponse {
  if (
    !(
      isRecord(value) &&
      typeof value.cue_id === "string" &&
      typeof value.source_order === "number" &&
      typeof value.start_ms === "number" &&
      typeof value.end_ms === "number" &&
      typeof value.text === "string" &&
      typeof value.language_code === "string" &&
      (value.origin === "caption" || value.origin === "asr") &&
      (value.track_id === null || typeof value.track_id === "string") &&
      (value.caption_kind === null ||
        value.caption_kind === "manual" ||
        value.caption_kind === "automatic") &&
      (value.transcription_run_id === null ||
        typeof value.transcription_run_id === "string") &&
      (value.confidence === null || typeof value.confidence === "number") &&
      (value.confidence_method === null ||
        typeof value.confidence_method === "string")
    )
  ) {
    return false
  }
  const sourceMatches =
    value.origin === "caption"
      ? typeof value.track_id === "string" &&
        (value.caption_kind === "manual" ||
          value.caption_kind === "automatic") &&
        value.transcription_run_id === null
      : value.track_id === null &&
        value.caption_kind === null &&
        typeof value.transcription_run_id === "string"
  const confidenceMatches =
    value.confidence === null
      ? value.confidence_method === null
      : typeof value.confidence_method === "string"
  return sourceMatches && confidenceMatches
}

function isTranscriptionRun(
  value: unknown,
): value is TranscriptionRunResponse {
  return (
    isRecord(value) &&
    typeof value.run_id === "string" &&
    typeof value.job_id === "string" &&
    typeof value.audio_asset_id === "string" &&
    typeof value.audio_attempt === "number" &&
    (value.fallback_reason === "captions_unavailable" ||
      value.fallback_reason === "captions_unusable") &&
    typeof value.audio_start_ms === "number" &&
    typeof value.audio_end_ms === "number" &&
    typeof value.provider === "string" &&
    typeof value.provider_revision === "string" &&
    typeof value.model === "string" &&
    typeof value.model_revision === "string" &&
    (value.device === "cpu" || value.device === "cuda") &&
    ["int8", "int8_float16", "float16", "float32"].includes(
      String(value.compute_type),
    ) &&
    typeof value.language_code === "string" &&
    (value.language_confidence === null ||
      typeof value.language_confidence === "number") &&
    (value.language_confidence_method === null ||
      typeof value.language_confidence_method === "string") &&
    (value.language_confidence === null
      ? value.language_confidence_method === null
      : typeof value.language_confidence_method === "string") &&
    typeof value.processing_seconds === "number" &&
    typeof value.transcribed_at === "string"
  )
}

export function isImportVideoResponse(
  value: unknown,
): value is ImportVideoResponse {
  return (
    isRecord(value) &&
    isIngestionJobResponse(value.job) &&
    typeof value.reused === "boolean"
  )
}

export function isTranscriptResponse(value: unknown): value is TranscriptResponse {
  return (
    isRecord(value) &&
    isVideoResponse(value.video) &&
    Array.isArray(value.cues) &&
    value.cues.every(isTranscriptCue) &&
    Array.isArray(value.retrieval_units) &&
    value.retrieval_units.every(
      (unit) =>
        isRecord(unit) &&
        typeof unit.retrieval_unit_id === "string" &&
        typeof unit.start_ms === "number" &&
        typeof unit.end_ms === "number" &&
        typeof unit.text === "string" &&
        isStringArray(unit.cue_ids),
    ) &&
    (value.transcription === null || isTranscriptionRun(value.transcription))
  )
}

export function isAnswerResponse(value: unknown): value is AnswerResponse {
  return (
    isRecord(value) &&
    typeof value.answer === "string" &&
    ["low", "medium", "high"].includes(String(value.confidence)) &&
    typeof value.degraded_mode === "boolean" &&
    isStringArray(value.warnings) &&
    Array.isArray(value.evidence) &&
    value.evidence.every(
      (item) =>
        isRecord(item) &&
        typeof item.video_id === "string" &&
        typeof item.retrieval_unit_id === "string" &&
        isStringArray(item.cue_ids) &&
        typeof item.quote === "string" &&
        typeof item.start_ms === "number" &&
        typeof item.end_ms === "number" &&
        item.modality === "transcript",
    )
  )
}
