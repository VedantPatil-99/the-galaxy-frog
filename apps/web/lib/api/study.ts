import { requestApi } from "@/lib/api/client"
import type { components } from "@/lib/api/generated/schema"

type Schemas = components["schemas"]
export type StudyKind = Schemas["StudyKind"]
export type StudyIndex = Schemas["StudyIndex"]
export type StudyVersion = Schemas["StudyVersion"]
export type StudyVersionSummary = Schemas["StudyVersionSummary"]
export type StudyRun = Schemas["StudyRun"]
export type StudyCitation = Schemas["StudyCitation"]
export type StudyItem = Schemas["StudySection"]["items"][number]

const videoPath = (videoId: string) => "/v1/videos/" + encodeURIComponent(videoId)

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}

function isStudyRun(value: unknown): value is StudyRun {
  return record(value) && typeof value.run_id === "string" && typeof value.state === "string"
}

function isStudyIndex(value: unknown): value is StudyIndex {
  return record(value) && Array.isArray(value.latest) && Array.isArray(value.runs)
}

function isStudyVersion(value: unknown): value is StudyVersion {
  return record(value) && typeof value.artifact_id === "string" &&
    typeof value.version === "number" && record(value.document)
}

export function listStudy(
  videoId: string, kind: StudyKind, signal: AbortSignal, before?: number,
): Promise<StudyIndex> {
  const query = new URLSearchParams({ kind })
  if (before !== undefined) query.set("before_version", String(before))
  return requestApi(videoPath(videoId) + "/artifacts?" + query, isStudyIndex, fetch, { signal })
}

export function readStudyVersion(videoId: string, artifactId: string, signal: AbortSignal) {
  return requestApi(videoPath(videoId) + "/artifacts/" + encodeURIComponent(artifactId), isStudyVersion, fetch, { signal })
}

export function readStudyRun(videoId: string, runId: string, signal: AbortSignal) {
  return requestApi(videoPath(videoId) + "/artifact-runs/" + encodeURIComponent(runId), isStudyRun, fetch, { signal })
}

export function startStudy(
  videoId: string, kind: StudyKind, requestId: string, chapterVersion: string | null,
  parentId?: string,
) {
  let path: string
  let body: Schemas["ChapterRequest"] | Schemas["StudyArtifactRequest"] | Schemas["RegenerateRequest"]
  if (parentId !== undefined) {
    path = "/v1/artifacts/" + encodeURIComponent(parentId) + "/regenerate"
    body = { request_id: requestId, chapter_version_id: chapterVersion }
  } else if (kind === "chapters") {
    path = videoPath(videoId) + "/chapters"
    body = { request_id: requestId }
  } else {
    if (chapterVersion === null) throw new Error("Generate chapters first.")
    path = videoPath(videoId) + "/artifacts"
    body = { request_id: requestId, kind, chapter_version_id: chapterVersion }
  }
  return requestApi(path, isStudyRun, fetch, { method: "POST", body: JSON.stringify(body) })
}

export function studyExportUrl(videoId: string, artifactId: string, format: "markdown" | "json") {
  return "/api/proxy" + videoPath(videoId) + "/artifacts/" + encodeURIComponent(artifactId) + "/export?format=" + format
}
