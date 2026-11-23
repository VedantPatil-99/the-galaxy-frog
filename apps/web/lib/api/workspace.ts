import { requestApi } from "@/lib/api/client"
import type { components, paths } from "@/lib/api/generated/schema"
import { workspaceSessionHeader } from "@/lib/workspace-session-marker"

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
export type WorkspaceTurn = Schema["WorkspaceTurnView"]
export type WorkspaceTurnIntent = Schema["WorkspaceTurnSubmit"]
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

export function getWorkspaceStatus(signal?: AbortSignal) {
  return requestApi(`${base}/status`, (value): value is Status =>
    object(value) && (value.mode === "local" || value.mode === "workspace")
      && typeof value.authentication_configured === "boolean"
      && typeof value.creator_access_configured === "boolean"
      && typeof value.legacy_resources_available === "boolean", fetch, { signal })
}

export function getWorkspaceSession(marker: string, signal: AbortSignal) {
  return requestApi(`${base}/session`, (value): value is Session =>
    object(value) && typeof value.email === "string" && value.role === "creator"
      && object(value.account) && typeof value.account.account_id === "string", fetch, {
    headers: { [workspaceSessionHeader]: marker },
    signal,
  })
}

type WorkspaceRequest = <T>(path: string, guard: (value: unknown) => value is T, init?: RequestInit) => Promise<T>
type WorkspaceImageRequest = (path: string, signal: AbortSignal) => Promise<Blob>

export function createWorkspaceApi(request: WorkspaceRequest, requestImage: WorkspaceImageRequest) {
  async function getNotebooks(signal: AbortSignal) {
    const data = await request(`${base}/notebooks`, (value): value is Schema["NotebookListResponse"] =>
      object(value) && Array.isArray(value.notebooks) && value.notebooks.every(notebook), { signal })
    return data.notebooks
  }

  function createNotebook(title: string) {
    const draft: Schema["NotebookDraft"] = { title }
    return request(`${base}/notebooks`, notebook, { method: "POST", body: JSON.stringify(draft) })
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

  function listWorkspaceChats(notebookId: string, after: string | null, signal: AbortSignal) {
    const search = after ? `?after=${encodeURIComponent(after)}` : ""
    return request(chatPath(notebookId) + search, (value): value is Schema["WorkspaceChatList"] =>
      object(value) && Array.isArray(value.chats) && value.chats.every(chatSummary)
        && (value.next_after === null || typeof value.next_after === "string"), { signal })
  }

  function getWorkspaceChat(notebookId: string, chatId: string, signal?: AbortSignal) {
    return request(chatPath(notebookId, chatId), chatDetails, { signal })
  }

  function createWorkspaceChat(notebookId: string, body: Schema["WorkspaceChatCreate"], signal: AbortSignal) {
    return request(chatPath(notebookId), chatDetails, { method: "POST", body: JSON.stringify(body), signal })
  }

  function renameWorkspaceChat(notebookId: string, chatId: string, title: string, signal: AbortSignal) {
    const body: Schema["WorkspaceChatName"] = { title }
    return request(chatPath(notebookId, chatId), chatSummary, { method: "PATCH", body: JSON.stringify(body), signal })
  }

  function saveWorkspaceDraft(notebookId: string, chatId: string, draft: WorkspacePromptDraft) {
    return request(`${chatPath(notebookId, chatId)}/draft`, (value): value is WorkspacePromptDraft =>
      promptDraft(value) && value.text === draft.text && value.revision >= draft.revision, {
      method: "PUT", body: JSON.stringify(draft), keepalive: true,
    })
  }

  function savedTurn(value: unknown): value is WorkspaceTurn {
    if (!object(value) || typeof value.turn_id !== "string" || typeof value.question !== "string"
      || typeof value.created_at !== "string" || typeof value.sequence !== "number"
      || !Number.isSafeInteger(value.sequence) || value.sequence < 1) return false
    const { passage, run } = value
    if (!object(passage) || typeof passage.source_id !== "string" || typeof passage.version_id !== "string"
      || typeof passage.block_id !== "string" || !object(run) || typeof run.run_id !== "string"
      || typeof run.revision !== "number" || !Number.isInteger(run.revision) || run.revision < 0
      || !object(run.settings) || run.settings.provider !== "ollama" || typeof run.settings.model !== "string"
      || (run.retry_of_run_id !== null && typeof run.retry_of_run_id !== "string")
      || !object(run.execution) || typeof run.execution.claims !== "number" || !Number.isSafeInteger(run.execution.claims)
      || run.execution.claims < 0 || (run.execution.possible_dispatch_at !== null && typeof run.execution.possible_dispatch_at !== "string")
      || !["queued", "preparing", "running", "completed", "stopped", "failed", "uncertain"].some(status => status === run.status)) return false
    if (run.status !== "completed") return run.answer === null && run.evidence === null
    const { evidence, answer } = run
    if (!object(evidence) || !textSource(evidence.source) || evidence.source.source_id !== passage.source_id
      || evidence.source.version_id !== passage.version_id || evidence.block_id !== passage.block_id
      || typeof evidence.start_char !== "number" || typeof evidence.end_char !== "number"
      || !Number.isInteger(evidence.start_char) || !Number.isInteger(evidence.end_char)
      || evidence.start_char < 0 || evidence.end_char <= evidence.start_char) return false
    const excerpt = evidence.excerpt
    return typeof excerpt === "string" && object(answer) && Array.isArray(answer.blocks)
      && answer.blocks.every(block => object(block) && typeof block.text === "string"
        && Array.isArray(block.citations) && block.citations.length > 0 && block.citations.every(citation =>
          object(citation) && citation.reference === "E1" && typeof citation.quote === "string"
          && Boolean(citation.quote) && excerpt.includes(citation.quote)))
  }

  function submitWorkspaceTurn(notebookId: string, chatId: string, input: WorkspaceTurnIntent, signal: AbortSignal) {
    return request(`${chatPath(notebookId, chatId)}/turns`, savedTurn, {
      method: "POST", body: JSON.stringify(input), signal,
    })
  }

  function listWorkspaceTurns(notebookId: string, chatId: string, before: number | null, signal: AbortSignal) {
    const suffix = before === null ? "" : `?before=${before}`
    return request(`${chatPath(notebookId, chatId)}/turns${suffix}`, (value): value is Schema["WorkspaceTurnsPage"] =>
      object(value) && Array.isArray(value.turns) && value.turns.every(savedTurn)
        && (value.next_before === null || (typeof value.next_before === "number" && Number.isSafeInteger(value.next_before))),
    { signal })
  }

  function runPath(notebookId: string, chatId: string, runId: string) {
    return `${chatPath(notebookId, chatId)}/runs/${encodeURIComponent(runId)}`
  }

  function getWorkspaceRun(notebookId: string, chatId: string, runId: string, signal: AbortSignal) {
    return request(runPath(notebookId, chatId, runId), savedTurn, { signal })
  }

  function stopWorkspaceRun(notebookId: string, chatId: string, runId: string, signal: AbortSignal) {
    return request(`${runPath(notebookId, chatId, runId)}/stop`, savedTurn, { method: "POST", signal })
  }

  function retryWorkspaceRun(notebookId: string, chatId: string, runId: string, submissionKey: string, signal: AbortSignal) {
    const body: Schema["WorkspaceRunRetry"] = { submission_key: submissionKey }
    return request(`${runPath(notebookId, chatId, runId)}/retry`, savedTurn, {
      method: "POST", body: JSON.stringify(body), signal,
    })
  }

  function listSources(notebookId: string, after: string | null, signal: AbortSignal) {
    const suffix = after ? `?after=${encodeURIComponent(after)}` : ""
    return request(`${base}/notebooks/${encodeURIComponent(notebookId)}/sources${suffix}`, (value): value is Schema["WorkspaceSourceListResponse"] =>
      object(value) && Array.isArray(value.sources) && value.sources.every(source)
        && (typeof value.next_after === "string" || value.next_after === null), { signal })
  }

  function getOwnedTranscript(notebookId: string, videoId: string, signal: AbortSignal) {
    return request(`${base}/notebooks/${encodeURIComponent(notebookId)}/sources/${encodeURIComponent(videoId)}/transcript`, (value): value is WorkspaceTranscript =>
      object(value) && source(value.source) && Array.isArray(value.cues)
        && value.cues.every(cue => object(cue) && typeof cue.cue_id === "string"
          && typeof cue.start_ms === "number" && typeof cue.end_ms === "number" && typeof cue.text === "string"),
    { signal })
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

  function listTextSources(notebookId: string, after: string | null, signal: AbortSignal) {
    const suffix = after ? `?after=${encodeURIComponent(after)}` : ""
    return request(`${textPath(notebookId)}${suffix}`, (value): value is Schema["WorkspaceTextSourceListResponse"] =>
      object(value) && Array.isArray(value.sources) && value.sources.every(textSource)
        && (value.next_after === null || typeof value.next_after === "string"), { signal })
  }

  function pasteTextSource(notebookId: string, draft: WorkspaceTextDraft, signal: AbortSignal) {
    return request(textPath(notebookId), textVersion, { method: "POST", body: JSON.stringify(draft), signal })
  }

  function getTextVersion(notebookId: string, source: WorkspaceTextSource, signal: AbortSignal) {
    const path = `${textPath(notebookId)}/${encodeURIComponent(source.source_id)}/versions/${encodeURIComponent(source.version_id)}`
    return request(path, textVersion, { signal })
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

  function listDocuments(notebookId: string, after: string | null, signal: AbortSignal) {
    const suffix = after ? `?after=${encodeURIComponent(after)}` : ""
    return request(`${documentPath(notebookId)}${suffix}`, (value): value is Schema["WorkspaceDocumentListResponse"] =>
      object(value) && Array.isArray(value.sources) && value.sources.every(documentSource)
        && (value.next_after === null || typeof value.next_after === "string"), { signal })
  }

  function importDocument(notebookId: string, file: File, signal: AbortSignal) {
    const query = new URLSearchParams({ filename: file.name })
    return request(`${documentPath(notebookId)}?${query}`, documentSource, {
      method: "POST", headers: { "content-type": "application/octet-stream" }, body: file, signal,
    })
  }

  function getDocumentSection(notebookId: string, source: WorkspaceDocument, section: number, signal: AbortSignal) {
    return request(`${documentVersionPath(notebookId, source)}/sections/${section}`, (value): value is WorkspaceDocumentSection =>
      object(value) && value.source_id === source.source_id && value.version_id === source.version_id
        && value.section_number === section && value.page_number === (source.format === "pdf" ? section : null)
        && typeof value.text === "string" && Array.isArray(value.blocks) && value.blocks.every(textBlock), { signal })
  }

  function getDocumentPageImage(notebookId: string, source: WorkspaceDocument, page: number, signal: AbortSignal) {
    return requestImage(`${documentVersionPath(notebookId, source)}/pages/${page}/image`, signal)
  }

  const pageOCRStatuses = new Set<string>(["unavailable", "not_needed", "ready", "running", "succeeded", "no_text", "failed", "unknown"])

  function getPageOCR(notebookId: string, source: WorkspaceDocument, page: number, signal: AbortSignal) {
    return requestPageOCR(notebookId, source, page, { signal })
  }

  function startPageOCR(notebookId: string, source: WorkspaceDocument, page: number, signal: AbortSignal) {
    const consent: Schema["WorkspacePageOCRConsent"] = { allow_external_page_image: true }
    return requestPageOCR(notebookId, source, page, { method: "POST", body: JSON.stringify(consent), signal })
  }

  function requestPageOCR(notebookId: string, source: WorkspaceDocument, page: number, init: RequestInit) {
    return request(`${documentVersionPath(notebookId, source)}/pages/${page}/ocr`, (value): value is WorkspacePageOCR =>
      object(value) && value.source_id === source.source_id && value.version_id === source.version_id
        && value.page_number === page && pageOCRStatuses.has(String(value.status))
        && value.provider === "llamaparse" && value.profile === "parse-v2-fast"
        && (value.text === null || typeof value.text === "string")
        && Array.isArray(value.blocks) && value.blocks.every(textBlock), init)
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

  function searchWorkspaceSources(notebookId: string, query: string, signal: AbortSignal) {
    const input: Schema["WorkspaceSearchQuery"] = { query }
    return request(`${base}/notebooks/${encodeURIComponent(notebookId)}/search`, (value): value is WorkspaceSearchResult =>
      object(value) && value.profile === "postgres-keywords-v1"
        && typeof value.query_ms === "number" && Number.isFinite(value.query_ms) && value.query_ms >= 0
        && Array.isArray(value.hits) && value.hits.every(searchHit), {
      method: "POST", body: JSON.stringify(input), signal,
    })
  }

  function answerWorkspaceQuestion(
    notebookId: string, query: string, question: string, evidenceIds: string[], signal: AbortSignal,
  ) {
    const input: Schema["WorkspaceAnswerRequest"] = { query, question, evidence_ids: evidenceIds }
    return request(`${base}/notebooks/${encodeURIComponent(notebookId)}/answers`, (value): value is WorkspaceAnswer =>
      object(value) && value.provider === "ollama" && typeof value.model === "string" && Array.isArray(value.blocks)
        && value.blocks.every(block => object(block) && typeof block.text === "string"
          && Array.isArray(block.citations) && block.citations.length > 0
          && block.citations.every(citation => object(citation) && typeof citation.quote === "string" && searchHit(citation.evidence))),
    { method: "POST", body: JSON.stringify(input), signal })
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

  function getWorkspaceSemanticIndex(notebookId: string, signal: AbortSignal) {
    return request(`${base}/notebooks/${encodeURIComponent(notebookId)}/semantic-index`, semanticStatus, { signal })
  }

  function buildWorkspaceSemanticIndex(notebookId: string, input: Schema["WorkspaceSemanticBuild"], signal: AbortSignal) {
    return request(`${base}/notebooks/${encodeURIComponent(notebookId)}/semantic-index`, semanticStatus, {
      method: "POST", body: JSON.stringify(input), signal,
    })
  }

  function searchWorkspaceMeaning(notebookId: string, input: Schema["WorkspaceSemanticQuery"], signal: AbortSignal) {
    return request(`${base}/notebooks/${encodeURIComponent(notebookId)}/semantic-search`, (value): value is Schema["WorkspaceSemanticResults"] =>
      object(value) && value.collection_id === input.collection_id && Array.isArray(value.hits) && value.hits.every(searchHit),
    { method: "POST", body: JSON.stringify(input), signal })
  }

  function previewWorkspaceCloudEmbedding(notebookId: string, action: CloudEmbeddingAction, signal: AbortSignal) {
    return request(`${base}/notebooks/${encodeURIComponent(notebookId)}/semantic-cloud-preview`, (value): value is CloudEmbeddingPreview =>
      object(value) && value.kind === action.kind && value.model === (action.profile ?? "gemini-embedding-2")
        && (value.model === "embed-v4.0"
          ? value.pricing_basis === "trial" || value.pricing_basis === "configured"
          : value.pricing_basis === "list" || value.pricing_basis === undefined)
        && typeof value.preview_id === "string" && typeof value.expires_at === "string"
        && typeof value.inputs === "number" && typeof value.remaining_inputs === "number"
        && typeof value.estimated_usd === "string" && typeof value.estimated_remaining_usd === "string",
    { method: "POST", body: JSON.stringify(action), signal })
  }

  function confirmWorkspaceCloudEmbedding(notebookId: string, previewId: string, action: CloudEmbeddingAction, signal: AbortSignal) {
    const input: Schema["CloudEmbeddingConfirmation"] = { preview_id: previewId, action, allow_cloud: true }
    return request(`${base}/notebooks/${encodeURIComponent(notebookId)}/semantic-cloud-confirm`, (value): value is Schema["CloudIndexResult"] | Schema["CloudSearchResult"] =>
      object(value) && (value.kind === "index" && semanticStatus(value.index)
        || value.kind === "search" && object(value.results) && Array.isArray(value.results.hits) && value.results.hits.every(searchHit)),
    { method: "POST", body: JSON.stringify(input), signal })
  }

  return {
    getNotebooks,
    createNotebook,
    listWorkspaceChats,
    getWorkspaceChat,
    createWorkspaceChat,
    renameWorkspaceChat,
    saveWorkspaceDraft,
    submitWorkspaceTurn,
    listWorkspaceTurns,
    getWorkspaceRun,
    stopWorkspaceRun,
    retryWorkspaceRun,
    listSources,
    getOwnedTranscript,
    listTextSources,
    pasteTextSource,
    getTextVersion,
    listDocuments,
    importDocument,
    getDocumentSection,
    getDocumentPageImage,
    getPageOCR,
    startPageOCR,
    searchWorkspaceSources,
    answerWorkspaceQuestion,
    getWorkspaceSemanticIndex,
    buildWorkspaceSemanticIndex,
    searchWorkspaceMeaning,
    previewWorkspaceCloudEmbedding,
    confirmWorkspaceCloudEmbedding,
  }
}
