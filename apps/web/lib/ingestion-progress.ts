import type {
  IngestionEventsResponse,
  IngestionJobResponse,
  TranscriptionRunResponse,
} from "@/lib/api/contracts"

type IngestionStage = IngestionJobResponse["stage"]
type IngestionStatus = IngestionJobResponse["status"]
type IngestionEvent = IngestionEventsResponse["events"][number]

interface StagePresentation {
  label: string
  description: string
  progress: number
}

const stagePresentation: Record<IngestionStage, StagePresentation> = {
  source_resolution: {
    label: "Resolving source",
    description: "Checking the canonical video identity.",
    progress: 8,
  },
  metadata: {
    label: "Reading metadata",
    description: "Loading safe title and duration metadata.",
    progress: 16,
  },
  caption_retrieval: {
    label: "Checking captions",
    description: "Preferring viable timestamped captions before local ASR.",
    progress: 28,
  },
  audio_acquisition: {
    label: "Preparing audio",
    description: "Captions were insufficient; acquiring bounded local audio.",
    progress: 40,
  },
  transcription: {
    label: "Transcribing locally",
    description: "Generating multilingual timestamp cues with the configured ASR model.",
    progress: 58,
  },
  chunking: {
    label: "Building evidence units",
    description: "Grouping exact cues without losing their source intervals.",
    progress: 66,
  },
  persistence: {
    label: "Saving evidence",
    description: "Persisting cues, intervals, and their complete provenance.",
    progress: 74,
  },
  embedding: {
    label: "Indexing transcript",
    description: "Creating video-scoped retrieval vectors from persisted units.",
    progress: 86,
  },
  indexing: {
    label: "Finalizing index",
    description: "Completing the searchable transcript index.",
    progress: 92,
  },
  cleanup: {
    label: "Cleaning temporary media",
    description: "Removing local media while retaining its database lineage.",
    progress: 97,
  },
  completed: {
    label: "Evidence ready",
    description: "The transcript is persisted, indexed, and ready to inspect.",
    progress: 100,
  },
}

const statusLabels: Record<IngestionStatus, string> = {
  queued: "Queued",
  running: "Processing",
  succeeded: "Complete",
  failed: "Needs attention",
  cancelled: "Cancelled",
}

const eventLabels: Record<IngestionEvent["event_type"], string> = {
  created: "Job created",
  claimed: "Worker claimed job",
  heartbeat: "Worker lease refreshed",
  stage_started: "Stage started",
  stage_completed: "Stage completed",
  retry_requested: "Retry requested",
  cancel_requested: "Cancellation requested",
  cancelled: "Job cancelled",
  failed: "Stage failed",
  completed: "Job completed",
}

export function presentStage(stage: IngestionStage): StagePresentation {
  return stagePresentation[stage]
}

export function ingestionStatusLabel(status: IngestionStatus): string {
  return statusLabels[status]
}

export function ingestionEventLabel(event: IngestionEvent): string {
  const base = eventLabels[event.event_type]
  if (event.event_type === "stage_started" || event.event_type === "stage_completed") {
    return `${base}: ${presentStage(event.stage).label}`
  }
  return base
}

function stringDetail(event: IngestionEvent, key: string): string | null {
  const value = event.details?.[key]
  return typeof value === "string" && value.length > 0 ? value : null
}

function numberDetail(event: IngestionEvent, key: string): number | null {
  const value = event.details?.[key]
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function booleanDetail(event: IngestionEvent, key: string): boolean | null {
  const value = event.details?.[key]
  return typeof value === "boolean" ? value : null
}

function countLabel(value: number, noun: string): string {
  return `${value} ${noun}${value === 1 ? "" : "s"}`
}

export function ingestionEventDecision(event: IngestionEvent): string | null {
  if (event.event_type === "failed") {
    const retry = event.retryable ? "Retry allowed from checkpoint" : "Retry blocked"
    return event.error_code ? `${retry} · ${event.error_code}` : retry
  }
  if (event.event_type === "retry_requested") {
    return "Queued from the last completed checkpoint"
  }
  if (event.event_type !== "stage_completed") return null

  if (event.stage === "caption_retrieval") {
    const origin = stringDetail(event, "transcript_origin")
    const fallback = stringDetail(event, "fallback_reason")
    if (
      origin === "asr" &&
      (fallback === "captions_unavailable" || fallback === "captions_unusable")
    ) {
      return `Local ASR selected · ${fallbackReasonLabel(fallback)}`
    }
    const language = stringDetail(event, "language_code")
    const cueCount = numberDetail(event, "cue_count")
    if (origin === "caption" && language && cueCount !== null) {
      return `Captions selected · ${language} · ${countLabel(cueCount, "cue")}`
    }
  }

  if (event.stage === "audio_acquisition") {
    const startMs = numberDetail(event, "start_ms")
    const endMs = numberDetail(event, "end_ms")
    const reused = booleanDetail(event, "reused")
    if (startMs !== null && endMs !== null) {
      return `${reused ? "Reused" : "Created"} normalized audio · ${startMs}–${endMs} ms`
    }
  }

  if (event.stage === "transcription") {
    const provider = stringDetail(event, "provider")
    const model = stringDetail(event, "model")
    const device = stringDetail(event, "device")
    const computeType = stringDetail(event, "compute_type")
    const cueCount = numberDetail(event, "cue_count")
    const processingSeconds = numberDetail(event, "processing_seconds")
    const parts = [
      provider && model ? `${provider} ${model}` : provider,
      device && computeType ? `${device}/${computeType}` : device,
      cueCount === null ? null : countLabel(cueCount, "cue"),
      processingSeconds === null ? null : formatProcessingTime(processingSeconds),
      booleanDetail(event, "reused") ? "reused checkpoint" : null,
    ].filter((part): part is string => part !== null)
    return parts.length > 0 ? parts.join(" · ") : null
  }

  if (event.stage === "cleanup") {
    const audioAssetId = stringDetail(event, "audio_asset_id")
    const temporaryMediaPresent = booleanDetail(event, "temporary_media_present")
    if (!audioAssetId) return "No temporary audio was created"
    if (temporaryMediaPresent === false) return "Temporary audio removed; database lineage retained"
    if (temporaryMediaPresent === true) return "Temporary audio retained by configuration"
  }

  return null
}

export function fallbackReasonLabel(
  reason: TranscriptionRunResponse["fallback_reason"],
): string {
  return reason === "captions_unavailable"
    ? "No caption track was available"
    : "The available captions did not produce usable evidence"
}

export function formatProcessingTime(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(2)} s`
  const minutes = Math.floor(seconds / 60)
  const remainder = Math.round(seconds % 60)
  return `${minutes} min ${remainder} s`
}
