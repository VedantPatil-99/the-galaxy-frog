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
