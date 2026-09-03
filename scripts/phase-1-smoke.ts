const webBaseUrl = (process.env.WEB_BASE_URL ?? "http://localhost:3000").replace(/\/$/, "")
const sourceUrl = process.env.PHASE1_VIDEO_URL
const question = process.env.PHASE1_QUESTION

if (!sourceUrl || !question) {
  throw new Error(
    "Set PHASE1_VIDEO_URL and PHASE1_QUESTION to an approved public captioned video and grounded question.",
  )
}

async function jsonRequest(path: string, init?: RequestInit): Promise<Record<string, unknown>> {
  const response = await fetch(`${webBaseUrl}/api/proxy${path}`, {
    ...init,
    headers: {
      accept: "application/json",
      ...(init?.body === undefined ? {} : { "content-type": "application/json" }),
    },
  })
  const body: unknown = await response.json()
  if (!response.ok || typeof body !== "object" || body === null) {
    throw new Error(`${path} failed with ${response.status}: ${JSON.stringify(body)}`)
  }
  return body as Record<string, unknown>
}

function object(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${label} is not an object`)
  }
  return value as Record<string, unknown>
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
const firstVideo = object(first.video, "first import video")
const secondVideo = object(second.video, "second import video")
const videoId = firstVideo.video_id
if (typeof videoId !== "string" || secondVideo.video_id !== videoId || second.reused !== true) {
  throw new Error("re-import did not reuse the canonical video record")
}

const transcript = await jsonRequest(`/v1/videos/${encodeURIComponent(videoId)}/transcript`)
if (!Array.isArray(transcript.cues) || transcript.cues.length === 0) {
  throw new Error("the imported transcript contains no cues")
}
const units = transcript.retrieval_units
if (!Array.isArray(units) || units.length === 0) {
  throw new Error("the imported transcript contains no retrieval units")
}

const answer = await jsonRequest(`/v1/videos/${encodeURIComponent(videoId)}/questions`, {
  method: "POST",
  body: JSON.stringify({ question }),
})
if (!Array.isArray(answer.evidence) || answer.evidence.length === 0) {
  throw new Error(`the grounded answer returned no evidence: ${JSON.stringify(answer)}`)
}
for (const rawEvidence of answer.evidence) {
  const evidence = object(rawEvidence, "answer evidence")
  if (
    evidence.video_id !== videoId ||
    evidence.modality !== "transcript" ||
    typeof evidence.start_ms !== "number" ||
    typeof evidence.end_ms !== "number" ||
    evidence.end_ms <= evidence.start_ms ||
    !Array.isArray(evidence.cue_ids) ||
    evidence.cue_ids.length === 0
  ) {
    throw new Error(`invalid timestamp citation: ${JSON.stringify(evidence)}`)
  }
}

console.log(
  `PASS Phase 1: ${transcript.cues.length} cues, ${units.length} retrieval units, ${answer.evidence.length} validated citations, idempotent re-import.`,
)
