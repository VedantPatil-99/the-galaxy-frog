import { requestApi } from "@/lib/api/client"
import type { components, paths } from "@/lib/api/generated/schema"

type Schema = components["schemas"]
type Status = Schema["WorkspaceStatusResponse"]
type Session = Schema["WorkspaceSessionResponse"]
export type Notebook = Schema["Notebook"]
export type WorkspaceSource = Schema["WorkspaceVideoSourceResponse"]
export type WorkspaceTranscript = Schema["WorkspaceTranscriptResponse"]
export type WorkspaceTextSource = Schema["WorkspaceTextSource"]
export type WorkspaceTextVersion = Schema["WorkspaceTextVersion"]
export type WorkspaceTextDraft = paths["/v1/workspace/notebooks/{notebook_id}/text-sources"]["post"]["requestBody"]["content"]["application/json"]
export type WorkspaceDocument = Schema["WorkspaceDocumentSource"]
export type WorkspaceDocumentSection = Schema["WorkspaceDocumentSection"]
export type WorkspacePageOCR = Schema["WorkspacePageOCR"]
export type WorkspaceSearchResult = Schema["WorkspaceSearchResponse"]
export type WorkspaceSearchHit = WorkspaceSearchResult["hits"][number]
export type WorkspaceAnswer = Schema["WorkspaceAnswerResponse"]
export type WorkspaceChat = Schema["WorkspaceChat"]
export type WorkspaceChatSummary = Schema["WorkspaceChatSummary"]
export type WorkspacePromptDraft = Schema["WorkspacePromptDraft"]
export type WorkspaceSemanticStatus = Schema["WorkspaceSemanticStatus"]
export type CloudEmbeddingAction = Schema["CloudEmbeddingIndex"] | Schema["CloudEmbeddingSearch"]
export type CloudEmbeddingPreview = Schema["CloudEmbeddingPreview"]

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

function chatSummary(value: unknown): value is WorkspaceChatSummary {
  return object(value) && typeof value.chat_id === "string" && typeof value.title === "string"
    && typeof value.has_draft === "boolean" && typeof value.created_at === "string" && typeof value.updated_at === "string"
}

function promptDraft(value: unknown): value is WorkspacePromptDraft {
  return object(value) && typeof value.text === "string" && typeof value.revision === "number"
    && Number.isSafeInteger(value.revision) && value.revision >= 0
}

function chatDetails(value: unknown): value is WorkspaceChat {
  return object(value) && promptDraft(value.draft) && chatSummary(value)
}

function chatPath(notebookId: string, chatId?: string) {
  return `${base}/notebooks/${encodeURIComponent(notebookId)}/chats${chatId ? `/${encodeURIComponent(chatId)}` : ""}`
}

export function listWorkspaceChats(notebookId: string, after: string | null, signal: AbortSignal) {
  const search = after ? `?after=${encodeURIComponent(after)}` : ""
  return requestApi(chatPath(notebookId) + search, (value): value is Schema["WorkspaceChatList"] =>
    object(value) && Array.isArray(value.chats) && value.chats.every(chatSummary)
      && (value.next_after === null || typeof value.next_after === "string"), fetch, { signal })
}

export function getWorkspaceChat(notebookId: string, chatId: string, signal?: AbortSignal) {
  return requestApi(chatPath(notebookId, chatId), chatDetails, fetch, { signal })
}

export function createWorkspaceChat(notebookId: string, body: Schema["WorkspaceChatCreate"], signal: AbortSignal) {
  return requestApi(chatPath(notebookId), chatDetails, fetch, { method: "POST", body: JSON.stringify(body), signal })
}

export function renameWorkspaceChat(notebookId: string, chatId: string, title: string, signal: AbortSignal) {
  const body: Schema["WorkspaceChatName"] = { title }
  return requestApi(chatPath(notebookId, chatId), chatSummary, fetch, { method: "PATCH", body: JSON.stringify(body), signal })
}

export function saveWorkspaceDraft(notebookId: string, chatId: string, draft: WorkspacePromptDraft) {
  return requestApi(`${chatPath(notebookId, chatId)}/draft`, (value): value is WorkspacePromptDraft =>
    promptDraft(value) && value.text === draft.text && value.revision >= draft.revision, fetch, {
    method: "PUT", body: JSON.stringify(draft), keepalive: true,
  })
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

function textSource(value: unknown): value is WorkspaceTextSource {
  return object(value) && typeof value.source_id === "string" && typeof value.version_id === "string"
    && typeof value.title === "string" && typeof value.created_at === "string"
    && (value.format === "plain" || value.format === "markdown")
    && typeof value.block_count === "number" && Number.isInteger(value.block_count) && value.block_count > 0
}

function textVersion(value: unknown): value is WorkspaceTextVersion {
  if (!object(value)) return false
  const { text, blocks } = value
  return textSource(value) && typeof text === "string" && Array.isArray(blocks)
    && blocks.length === value.block_count
    && blocks.every(textBlock)
}

function textBlock(block: unknown): block is Schema["WorkspaceTextBlock"] {
  return object(block) && typeof block.block_id === "string"
    && typeof block.start_char === "number" && typeof block.end_char === "number"
    && Number.isInteger(block.start_char) && Number.isInteger(block.end_char)
    && block.start_char >= 0 && block.end_char > block.start_char && typeof block.text === "string"
}

function textPath(notebookId: string) {
  return `${base}/notebooks/${encodeURIComponent(notebookId)}/text-sources`
}

export function listTextSources(notebookId: string, after: string | null, signal: AbortSignal) {
  const suffix = after ? `?after=${encodeURIComponent(after)}` : ""
  return requestApi(`${textPath(notebookId)}${suffix}`, (value): value is Schema["WorkspaceTextSourceListResponse"] =>
    object(value) && Array.isArray(value.sources) && value.sources.every(textSource)
      && (value.next_after === null || typeof value.next_after === "string"), fetch, { signal })
}

export function pasteTextSource(notebookId: string, draft: WorkspaceTextDraft, signal: AbortSignal) {
  return requestApi(textPath(notebookId), textVersion, fetch, { method: "POST", body: JSON.stringify(draft), signal })
}

export function getTextVersion(notebookId: string, source: WorkspaceTextSource, signal: AbortSignal) {
  const path = `${textPath(notebookId)}/${encodeURIComponent(source.source_id)}/versions/${encodeURIComponent(source.version_id)}`
  return requestApi(path, textVersion, fetch, { signal })
}

function documentSource(value: unknown): value is WorkspaceDocument {
  return object(value) && typeof value.source_id === "string" && typeof value.version_id === "string"
    && typeof value.filename === "string" && typeof value.created_at === "string"
    && ["pdf", "docx", "plain", "markdown"].includes(String(value.format))
    && typeof value.size_bytes === "number" && value.size_bytes > 0
    && typeof value.section_count === "number" && Number.isInteger(value.section_count) && value.section_count > 0
    && (value.pages_without_text === null || typeof value.pages_without_text === "number")
}

function documentPath(notebookId: string) {
  return `${base}/notebooks/${encodeURIComponent(notebookId)}/documents`
}

function documentVersionPath(notebookId: string, source: WorkspaceDocument) {
  return `${documentPath(notebookId)}/${encodeURIComponent(source.source_id)}/versions/${encodeURIComponent(source.version_id)}`
}

export function listDocuments(notebookId: string, after: string | null, signal: AbortSignal) {
  const suffix = after ? `?after=${encodeURIComponent(after)}` : ""
  return requestApi(`${documentPath(notebookId)}${suffix}`, (value): value is Schema["WorkspaceDocumentListResponse"] =>
    object(value) && Array.isArray(value.sources) && value.sources.every(documentSource)
      && (value.next_after === null || typeof value.next_after === "string"), fetch, { signal })
}

export function importDocument(notebookId: string, file: File, signal: AbortSignal) {
  const query = new URLSearchParams({ filename: file.name })
  return requestApi(`${documentPath(notebookId)}?${query}`, documentSource, fetch, {
    method: "POST", headers: { "content-type": "application/octet-stream" }, body: file, signal,
  })
}

export function getDocumentSection(notebookId: string, source: WorkspaceDocument, section: number, signal: AbortSignal) {
  return requestApi(`${documentVersionPath(notebookId, source)}/sections/${section}`, (value): value is WorkspaceDocumentSection =>
    object(value) && value.source_id === source.source_id && value.version_id === source.version_id
      && value.section_number === section && value.page_number === (source.format === "pdf" ? section : null)
      && typeof value.text === "string" && Array.isArray(value.blocks) && value.blocks.every(textBlock), fetch, { signal })
}

export function documentPageImageUrl(notebookId: string, source: WorkspaceDocument, page: number) {
  return `/api/proxy${documentVersionPath(notebookId, source)}/pages/${page}/image`
}

const pageOCRStatuses = new Set<string>(["unavailable", "not_needed", "ready", "running", "succeeded", "no_text", "failed", "unknown"])

export function getPageOCR(notebookId: string, source: WorkspaceDocument, page: number, signal: AbortSignal) {
  return requestPageOCR(notebookId, source, page, { signal })
}

export function startPageOCR(notebookId: string, source: WorkspaceDocument, page: number, signal: AbortSignal) {
  const consent: Schema["WorkspacePageOCRConsent"] = { allow_external_page_image: true }
  return requestPageOCR(notebookId, source, page, { method: "POST", body: JSON.stringify(consent), signal })
}

function requestPageOCR(notebookId: string, source: WorkspaceDocument, page: number, init: RequestInit) {
  return requestApi(`${documentVersionPath(notebookId, source)}/pages/${page}/ocr`, (value): value is WorkspacePageOCR =>
    object(value) && value.source_id === source.source_id && value.version_id === source.version_id
      && value.page_number === page && pageOCRStatuses.has(String(value.status))
      && value.provider === "llamaparse" && value.profile === "parse-v2-fast"
      && (value.text === null || typeof value.text === "string")
      && Array.isArray(value.blocks) && value.blocks.every(textBlock), fetch, init)
}

function searchHit(value: unknown): value is WorkspaceSearchHit {
  if (!object(value)) return false
  if (value.kind === "video") {
    return source(value.source) && typeof value.unit_id === "string" && typeof value.text === "string"
      && typeof value.start_ms === "number" && Number.isSafeInteger(value.start_ms) && value.start_ms >= 0
      && typeof value.end_ms === "number" && Number.isSafeInteger(value.end_ms) && value.end_ms > value.start_ms
      && Array.isArray(value.cues) && value.cues.length > 0
      && value.cues.every(cue => object(cue) && typeof cue.cue_id === "string" && ["caption", "asr"].includes(String(cue.origin)))
  }
  if (!textBlock(value.block)) return false
  if (value.kind === "text") return textSource(value.source)
  return (value.kind === "document" || value.kind === "page_ocr") && documentSource(value.source)
    && typeof value.section_number === "number" && Number.isInteger(value.section_number)
    && value.section_number >= 1 && value.section_number <= value.source.section_count
    && (value.kind !== "page_ocr" || value.source.format === "pdf")
    && value.page_number === (value.source.format === "pdf" ? value.section_number : null)
}

export function searchWorkspaceSources(notebookId: string, query: string, signal: AbortSignal) {
  const input: Schema["WorkspaceSearchQuery"] = { query }
  return requestApi(`${base}/notebooks/${encodeURIComponent(notebookId)}/search`, (value): value is WorkspaceSearchResult =>
    object(value) && value.profile === "postgres-keywords-v1"
      && typeof value.query_ms === "number" && Number.isFinite(value.query_ms) && value.query_ms >= 0
      && Array.isArray(value.hits) && value.hits.every(searchHit), fetch, {
    method: "POST", body: JSON.stringify(input), signal,
  })
}

export function answerWorkspaceQuestion(
  notebookId: string, query: string, question: string, evidenceIds: string[], signal: AbortSignal,
) {
  const input: Schema["WorkspaceAnswerRequest"] = { query, question, evidence_ids: evidenceIds }
  return requestApi(`${base}/notebooks/${encodeURIComponent(notebookId)}/answers`, (value): value is WorkspaceAnswer =>
    object(value) && value.provider === "ollama" && typeof value.model === "string" && Array.isArray(value.blocks)
      && value.blocks.every(block => object(block) && typeof block.text === "string"
        && Array.isArray(block.citations) && block.citations.length > 0
        && block.citations.every(citation => object(citation) && typeof citation.quote === "string" && searchHit(citation.evidence))),
  fetch, { method: "POST", body: JSON.stringify(input), signal })
}

function embeddingProfile(value: unknown) {
  return value === null || object(value) && typeof value.provider === "string" && typeof value.model === "string"
    && typeof value.revision === "string" && typeof value.dimension === "number" && typeof value.normalization === "string"
}

function semanticStatus(value: unknown): value is WorkspaceSemanticStatus {
  return object(value) && (value.active_collection === null || typeof value.active_collection === "string")
    && embeddingProfile(value.active_profile) && embeddingProfile(value.available_profile)
    && embeddingProfile(value.cloud_profile ?? null) && embeddingProfile(value.cohere_profile ?? null)
    && typeof value.indexed === "number" && typeof value.total === "number"
    && Number.isInteger(value.indexed) && Number.isInteger(value.total) && value.indexed >= 0 && value.indexed <= value.total
}

export function getWorkspaceSemanticIndex(notebookId: string, signal: AbortSignal) {
  return requestApi(`${base}/notebooks/${encodeURIComponent(notebookId)}/semantic-index`, semanticStatus, fetch, { signal })
}

export function buildWorkspaceSemanticIndex(notebookId: string, input: Schema["WorkspaceSemanticBuild"], signal: AbortSignal) {
  return requestApi(`${base}/notebooks/${encodeURIComponent(notebookId)}/semantic-index`, semanticStatus, fetch, {
    method: "POST", body: JSON.stringify(input), signal,
  })
}

export function searchWorkspaceMeaning(notebookId: string, input: Schema["WorkspaceSemanticQuery"], signal: AbortSignal) {
  return requestApi(`${base}/notebooks/${encodeURIComponent(notebookId)}/semantic-search`, (value): value is Schema["WorkspaceSemanticResults"] =>
    object(value) && value.collection_id === input.collection_id && Array.isArray(value.hits) && value.hits.every(searchHit),
  fetch, { method: "POST", body: JSON.stringify(input), signal })
}

export function previewWorkspaceCloudEmbedding(notebookId: string, action: CloudEmbeddingAction, signal: AbortSignal) {
  return requestApi(`${base}/notebooks/${encodeURIComponent(notebookId)}/semantic-cloud-preview`, (value): value is CloudEmbeddingPreview =>
    object(value) && value.kind === action.kind && value.model === (action.profile ?? "gemini-embedding-2")
      && (value.model === "embed-v4.0"
        ? value.pricing_basis === "trial" || value.pricing_basis === "configured"
        : value.pricing_basis === "list" || value.pricing_basis === undefined)
      && typeof value.preview_id === "string" && typeof value.expires_at === "string"
      && typeof value.inputs === "number" && typeof value.remaining_inputs === "number"
      && typeof value.estimated_usd === "string" && typeof value.estimated_remaining_usd === "string",
  fetch, { method: "POST", body: JSON.stringify(action), signal })
}

export function confirmWorkspaceCloudEmbedding(notebookId: string, previewId: string, action: CloudEmbeddingAction, signal: AbortSignal) {
  const input: Schema["CloudEmbeddingConfirmation"] = { preview_id: previewId, action, allow_cloud: true }
  return requestApi(`${base}/notebooks/${encodeURIComponent(notebookId)}/semantic-cloud-confirm`, (value): value is Schema["CloudIndexResult"] | Schema["CloudSearchResult"] =>
    object(value) && (value.kind === "index" && semanticStatus(value.index)
      || value.kind === "search" && object(value.results) && Array.isArray(value.results.hits) && value.results.hits.every(searchHit)),
  fetch, { method: "POST", body: JSON.stringify(input), signal })
}
