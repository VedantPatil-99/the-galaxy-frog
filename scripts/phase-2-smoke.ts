const webBaseUrl = (process.env.WEB_BASE_URL ?? "http://localhost:3000").replace(/\/$/, "")
const sourceUrl = process.env.PHASE2_VIDEO_URL
const timeoutSeconds = Number(process.env.PHASE2_TIMEOUT_SECONDS ?? "3600")
const expectedDevice = process.env.PHASE2_EXPECTED_DEVICE

if (!sourceUrl) {
  throw new Error("Set PHASE2_VIDEO_URL to an approved public captionless YouTube video.")
}
if (!Number.isInteger(timeoutSeconds) || timeoutSeconds < 30 || timeoutSeconds > 7200) {
  throw new Error("PHASE2_TIMEOUT_SECONDS must be an integer between 30 and 7200.")
}
if (expectedDevice !== undefined && expectedDevice !== "cpu" && expectedDevice !== "cuda") {
  throw new Error("PHASE2_EXPECTED_DEVICE must be cpu or cuda when set.")
}

type JsonObject = Record<string, unknown>

async function jsonRequest(path: string, init?: RequestInit): Promise<JsonObject> {
  const response = await fetch(`${webBaseUrl}/api/proxy${path}`, {
    ...init,
    headers: {
      accept: "application/json",
      ...(init?.body === undefined ? {} : { "content-type": "application/json" }),
    },
    signal: AbortSignal.timeout(30_000),
  })
  const body: unknown = await response.json()
  if (!response.ok || typeof body !== "object" || body === null || Array.isArray(body)) {
    throw new Error(`${path} failed with ${response.status}: ${JSON.stringify(body)}`)
  }
  return body as JsonObject
}

function object(value: unknown, label: string): JsonObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${label} is not an object`)
  }
  return value as JsonObject
}

function string(value: unknown, label: string): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`${label} is not a non-empty string`)
  }
  return value
}

function integer(value: unknown, label: string): number {
  if (!Number.isInteger(value)) {
    throw new Error(`${label} is not an integer`)
  }
  return value as number
}

function number(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${label} is not a finite number`)
  }
  return value
}

function array(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new Error(`${label} is not an array`)
  }
  return value
}

function assertPositiveInterval(start: unknown, end: unknown, label: string): [number, number] {
  const startMs = integer(start, `${label}.start_ms`)
  const endMs = integer(end, `${label}.end_ms`)
  if (startMs < 0 || endMs <= startMs) {
    throw new Error(`${label} does not preserve a positive half-open interval`)
  }
  return [startMs, endMs]
}

function assertOptionalConfidence(
  confidence: unknown,
  method: unknown,
  label: string,
): void {
  if (confidence === null) {
    if (method !== null) {
      throw new Error(`${label} has a confidence method without a value`)
    }
    return
  }
  const value = number(confidence, `${label}.confidence`)
  if (value < 0 || value > 1 || typeof method !== "string" || method.trim() === "") {
    throw new Error(`${label} has invalid confidence provenance`)
  }
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}

async function waitForTerminalJob(initial: JsonObject): Promise<JsonObject> {
  const jobId = string(initial.job_id, "job.job_id")
  const deadline = Date.now() + timeoutSeconds * 1000
  let job = initial
  let lastProjection = ""
  while (Date.now() < deadline) {
    const status = string(job.status, "job.status")
    const stage = string(job.stage, "job.stage")
    const attempt = integer(job.attempt, "job.attempt")
    const projection = `${status}/${stage}/attempt-${attempt}`
    if (projection !== lastProjection) {
      console.log(`Phase 2 job ${jobId}: ${projection}`)
      lastProjection = projection
    }
    if (status === "succeeded") {
      return job
    }
    if (status === "failed") {
      throw new Error(
        `ingestion failed at ${stage}: ${String(job.last_error_code)} - ${String(job.last_error_message)}`,
      )
    }
    if (status === "cancelled") {
      throw new Error(`ingestion was cancelled at ${stage}`)
    }
    await sleep(2_000)
    job = await jsonRequest(`/v1/jobs/${encodeURIComponent(jobId)}`)
  }
  throw new Error(`ingestion did not finish within ${timeoutSeconds} seconds`)
}

function assertOrderedEvents(payload: JsonObject, jobId: string): JsonObject[] {
  if (payload.job_id !== jobId) {
    throw new Error("event history belongs to a different job")
  }
  const events = array(payload.events, "events")
  if (events.length === 0) {
    throw new Error("durable event history is empty")
  }
  const eventIds = new Set<string>()
  return events.map((rawEvent, index) => {
    const event = object(rawEvent, `events[${index}]`)
    const eventId = string(event.event_id, `events[${index}].event_id`)
    if (event.job_id !== jobId || integer(event.sequence, `events[${index}].sequence`) !== index + 1) {
      throw new Error(`events[${index}] lost job ownership or contiguous durable ordering`)
    }
    if (eventIds.has(eventId)) {
      throw new Error(`events[${index}] duplicates event_id ${eventId}`)
    }
    eventIds.add(eventId)
    string(event.event_type, `events[${index}].event_type`)
    string(event.stage, `events[${index}].stage`)
    integer(event.attempt, `events[${index}].attempt`)
    string(event.occurred_at, `events[${index}].occurred_at`)
    return event
  })
}

function assertAsrTranscript(payload: JsonObject, jobId: string, videoId: string): {
  cueIds: string[]
  unitIds: string[]
  runId: string
} {
  const video = object(payload.video, "transcript.video")
  if (video.video_id !== videoId || video.transcript_ready !== true || video.index_ready !== true) {
    throw new Error("completed video is not transcript/index ready")
  }

  const transcription = object(payload.transcription, "transcript.transcription")
  const runId = string(transcription.run_id, "transcription.run_id")
  if (transcription.job_id !== jobId) {
    throw new Error("transcription provenance belongs to a different durable job")
  }
  const audioStartMs = integer(transcription.audio_start_ms, "transcription.audio_start_ms")
  const audioEndMs = integer(transcription.audio_end_ms, "transcription.audio_end_ms")
  if (audioStartMs < 0 || audioEndMs <= audioStartMs) {
    throw new Error("transcription lost its exact source-audio interval")
  }
  if (
    transcription.fallback_reason !== "captions_unavailable" &&
    transcription.fallback_reason !== "captions_unusable"
  ) {
    throw new Error("transcription does not record an explicit caption fallback reason")
  }
  for (const key of ["provider", "provider_revision", "model", "model_revision", "language_code"]) {
    string(transcription[key], `transcription.${key}`)
  }
  const device = string(transcription.device, "transcription.device")
  string(transcription.compute_type, "transcription.compute_type")
  if (expectedDevice !== undefined && device !== expectedDevice) {
    throw new Error(`expected ASR device ${expectedDevice}, but the run recorded ${device}`)
  }
  assertOptionalConfidence(
    transcription.language_confidence,
    transcription.language_confidence_method,
    "transcription.language",
  )
  if (number(transcription.processing_seconds, "transcription.processing_seconds") < 0) {
    throw new Error("transcription processing time is negative")
  }
  string(transcription.transcribed_at, "transcription.transcribed_at")

  const cues = array(payload.cues, "transcript.cues")
  if (cues.length === 0) {
    throw new Error("ASR transcript contains no cues")
  }
  const cueIds: string[] = []
  const cueIntervals = new Map<string, [number, number]>()
  let previousStartMs = -1
  cues.forEach((rawCue, index) => {
    const cue = object(rawCue, `cues[${index}]`)
    const cueId = string(cue.cue_id, `cues[${index}].cue_id`)
    if (
      cue.origin !== "asr" ||
      cue.transcription_run_id !== runId ||
      cue.track_id !== null ||
      cue.caption_kind !== null ||
      integer(cue.source_order, `cues[${index}].source_order`) !== index
    ) {
      throw new Error(`cues[${index}] lost ASR lineage or source order`)
    }
    const interval = assertPositiveInterval(cue.start_ms, cue.end_ms, `cues[${index}]`)
    if (interval[0] < audioStartMs || interval[1] > audioEndMs || interval[0] < previousStartMs) {
      throw new Error(`cues[${index}] falls outside or reorders the source-audio interval`)
    }
    previousStartMs = interval[0]
    string(cue.text, `cues[${index}].text`)
    string(cue.language_code, `cues[${index}].language_code`)
    assertOptionalConfidence(cue.confidence, cue.confidence_method, `cues[${index}]`)
    cueIds.push(cueId)
    cueIntervals.set(cueId, interval)
  })
  if (new Set(cueIds).size !== cueIds.length) {
    throw new Error("ASR transcript contains duplicate cue IDs")
  }

  const units = array(payload.retrieval_units, "transcript.retrieval_units")
  if (units.length === 0) {
    throw new Error("ASR transcript contains no retrieval units")
  }
  const unitIds = units.map((rawUnit, index) => {
    const unit = object(rawUnit, `retrieval_units[${index}]`)
    const unitId = string(unit.retrieval_unit_id, `retrieval_units[${index}].retrieval_unit_id`)
    const [startMs, endMs] = assertPositiveInterval(
      unit.start_ms,
      unit.end_ms,
      `retrieval_units[${index}]`,
    )
    const linkedCueIds = array(unit.cue_ids, `retrieval_units[${index}].cue_ids`).map(
      (cueId, cueIndex) => string(cueId, `retrieval_units[${index}].cue_ids[${cueIndex}]`),
    )
    if (linkedCueIds.length === 0 || linkedCueIds.some((cueId) => !cueIntervals.has(cueId))) {
      throw new Error(`retrieval_units[${index}] has invalid cue provenance`)
    }
    const linkedIntervals = linkedCueIds.map((cueId) => cueIntervals.get(cueId)!)
    if (startMs !== linkedIntervals[0][0] || endMs !== linkedIntervals.at(-1)![1]) {
      throw new Error(`retrieval_units[${index}] does not reconstruct its exact cue interval`)
    }
    string(unit.text, `retrieval_units[${index}].text`)
    return unitId
  })
  if (new Set(unitIds).size !== unitIds.length) {
    throw new Error("ASR transcript contains duplicate retrieval-unit IDs")
  }
  return { cueIds, unitIds, runId }
}

await jsonRequest("/health/ready")
const first = await jsonRequest("/v1/videos/import", {
  method: "POST",
  body: JSON.stringify({ source_url: sourceUrl }),
})
const second = await jsonRequest("/v1/videos/import", {
  method: "POST",
  body: JSON.stringify({ source_url: sourceUrl }),
})
const firstJob = object(first.job, "first import job")
const secondJob = object(second.job, "second import job")
const jobId = string(firstJob.job_id, "first import job_id")
if (secondJob.job_id !== jobId || second.reused !== true) {
  throw new Error("concurrent duplicate import did not reuse the durable job")
}

const completed = await waitForTerminalJob(firstJob)
const videoId = string(completed.video_id, "completed job video_id")
const firstEventsPayload = await jsonRequest(`/v1/jobs/${encodeURIComponent(jobId)}/events`)
const firstEvents = assertOrderedEvents(firstEventsPayload, jobId)
if (!firstEvents.some((event) => event.event_type === "completed")) {
  throw new Error("durable event history does not contain job completion")
}
const firstTranscript = await jsonRequest(`/v1/videos/${encodeURIComponent(videoId)}/transcript`)
const evidence = assertAsrTranscript(firstTranscript, jobId, videoId)

const third = await jsonRequest("/v1/videos/import", {
  method: "POST",
  body: JSON.stringify({ source_url: sourceUrl }),
})
const thirdJob = object(third.job, "completed re-import job")
if (thirdJob.job_id !== jobId || third.reused !== true || thirdJob.status !== "succeeded") {
  throw new Error("completed re-import did not reuse the succeeded durable job")
}
await sleep(1_000)
const secondEventsPayload = await jsonRequest(`/v1/jobs/${encodeURIComponent(jobId)}/events`)
const secondEvents = assertOrderedEvents(secondEventsPayload, jobId)
const secondTranscript = await jsonRequest(`/v1/videos/${encodeURIComponent(videoId)}/transcript`)
const repeatedEvidence = assertAsrTranscript(secondTranscript, jobId, videoId)
if (
  JSON.stringify(secondEvents) !== JSON.stringify(firstEvents) ||
  JSON.stringify(repeatedEvidence) !== JSON.stringify(evidence)
) {
  throw new Error("duplicate dispatch changed durable events or ASR output identity")
}

console.log(
  `PASS Phase 2: ${firstEvents.length} ordered events, ${evidence.cueIds.length} exact ASR cues, ` +
    `${evidence.unitIds.length} retrieval units, run ${evidence.runId}, duplicate-free re-import.`,
)

export {}
