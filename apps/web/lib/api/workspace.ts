import { requestApi } from "@/lib/api/client"
import type { components } from "@/lib/api/generated/schema"

type Schema = components["schemas"]
type Status = Schema["WorkspaceStatusResponse"]
type Session = Schema["WorkspaceSessionResponse"]
export type Notebook = Schema["Notebook"]
export type WorkspaceSource = Schema["WorkspaceVideoSourceResponse"]
export type WorkspaceTranscript = Schema["WorkspaceTranscriptResponse"]

const base = "/v1/workspace"

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}

function notebook(value: unknown): value is Notebook {
  return object(value) && typeof value.notebook_id === "string" && typeof value.title === "string"
}

function source(value: unknown): value is WorkspaceSource {
  return object(value) && typeof value.video_id === "string" && typeof value.title === "string"
    && typeof value.duration_ms === "number"
    && (typeof value.canonical_url === "string" || value.canonical_url === null)
}

export function getWorkspaceStatus() {
  return requestApi(`${base}/status`, (value): value is Status =>
    object(value) && (value.mode === "local" || value.mode === "workspace")
      && typeof value.authentication_configured === "boolean"
      && typeof value.creator_access_configured === "boolean"
      && typeof value.legacy_resources_available === "boolean", fetch)
}

export function getWorkspaceSession() {
  return requestApi(`${base}/session`, (value): value is Session =>
    object(value) && typeof value.email === "string" && value.role === "creator"
      && object(value.account) && typeof value.account.account_id === "string", fetch)
}

export async function getNotebooks(signal: AbortSignal) {
  const data = await requestApi(`${base}/notebooks`, (value): value is Schema["NotebookListResponse"] =>
    object(value) && Array.isArray(value.notebooks) && value.notebooks.every(notebook), fetch, { signal })
  return data.notebooks
}

export function createNotebook(title: string) {
  const draft: Schema["NotebookDraft"] = { title }
  return requestApi(`${base}/notebooks`, notebook, fetch, { method: "POST", body: JSON.stringify(draft) })
}

export function listSources(notebookId: string, after: string | null, signal: AbortSignal) {
  const suffix = after ? `?after=${encodeURIComponent(after)}` : ""
  return requestApi(`${base}/notebooks/${encodeURIComponent(notebookId)}/sources${suffix}`, (value): value is Schema["WorkspaceSourceListResponse"] =>
    object(value) && Array.isArray(value.sources) && value.sources.every(source)
      && (typeof value.next_after === "string" || value.next_after === null), fetch, { signal })
}

export function getOwnedTranscript(notebookId: string, videoId: string, signal: AbortSignal) {
  return requestApi(`${base}/notebooks/${encodeURIComponent(notebookId)}/sources/${encodeURIComponent(videoId)}/transcript`, (value): value is WorkspaceTranscript =>
    object(value) && source(value.source) && Array.isArray(value.cues)
      && value.cues.every(cue => object(cue) && typeof cue.cue_id === "string"
        && typeof cue.start_ms === "number" && typeof cue.end_ms === "number" && typeof cue.text === "string"),
  fetch, { signal })
}
