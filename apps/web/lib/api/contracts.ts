import type { components } from "@/lib/api/generated/schema"

export type ApiErrorResponse = components["schemas"]["ErrorResponse"]
export type AnswerResponse = components["schemas"]["AnswerResponse"]
export type HealthResponse = components["schemas"]["HealthResponse"]
export type ImportVideoResponse = components["schemas"]["ImportVideoResponse"]
export type ReadinessResponse = components["schemas"]["ReadinessResponse"]
export type TranscriptCueResponse = components["schemas"]["TranscriptCueResponse"]
export type TranscriptResponse = components["schemas"]["TranscriptResponse"]
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

function isTranscriptCue(value: unknown): value is TranscriptCueResponse {
  return (
    isRecord(value) &&
    typeof value.cue_id === "string" &&
    typeof value.source_order === "number" &&
    typeof value.start_ms === "number" &&
    typeof value.end_ms === "number" &&
    typeof value.text === "string" &&
    typeof value.language_code === "string" &&
    (value.caption_kind === "manual" || value.caption_kind === "automatic")
  )
}

export function isImportVideoResponse(
  value: unknown,
): value is ImportVideoResponse {
  return (
    isRecord(value) &&
    isVideoResponse(value.video) &&
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
    )
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
