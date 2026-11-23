import type { WorkspaceTurnIntent } from "@/lib/api/workspace"

export type DraftRecovery = { text: string; revision: number | null }
type DraftStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">

export function setWorkspaceLocation(accountId: string, url: URL, method: "pushState" | "replaceState") {
  window.history[method]({ ...window.history.state, galaxyFrogAccount: accountId }, "", url)
}

export function clearWorkspaceLocation() {
  const url = new URL(window.location.href)
  const state = window.history.state
  if (!url.searchParams.has("notebook") && !url.searchParams.has("chat") && !state?.galaxyFrogAccount) return
  url.searchParams.delete("notebook")
  url.searchParams.delete("chat")
  const nextState = { ...state }
  delete nextState.galaxyFrogAccount
  window.history.replaceState(nextState, "", url)
}

export function workspaceDraftKey(accountId: string, notebookId: string, chatId?: string) {
  const scope = [accountId, notebookId, chatId ?? "question"].map(encodeURIComponent).join(":")
  return `galaxy-frog:prompt-draft:${scope}`
}

function pendingTurnKey(accountId: string, notebookId: string, chatId: string) {
  return `galaxy-frog:pending-turn:${[accountId, notebookId, chatId].map(encodeURIComponent).join(":")}`
}

export function readPendingTurn(accountId: string, notebookId: string, chatId: string, storage?: DraftStorage): WorkspaceTurnIntent | null {
  try {
    const key = pendingTurnKey(accountId, notebookId, chatId)
    const raw = (storage ?? window.sessionStorage).getItem(key)
    const record = raw && JSON.parse(raw)
    const body = record?.body
    const uuid = (value: unknown) => typeof value === "string" && /^[a-f0-9-]{36}$/i.test(value)
    if (!body || !Number.isFinite(record.expiresAt) || record.expiresAt <= Date.now()
      || !uuid(body.submission_key) || typeof body.question !== "string" || !body.question.trim()
      || body.question.length > 1_000 || !Number.isSafeInteger(body.draft_revision) || body.draft_revision < 0
      || !uuid(body.passage?.source_id) || !uuid(body.passage?.version_id) || !uuid(body.passage?.block_id)) return null
    return {
      submission_key: body.submission_key, question: body.question, draft_revision: body.draft_revision,
      passage: { source_id: body.passage.source_id, version_id: body.passage.version_id, block_id: body.passage.block_id },
    }
  } catch {
    return null
  }
}

export function writePendingTurn(accountId: string, notebookId: string, chatId: string, body: WorkspaceTurnIntent | null, storage?: DraftStorage) {
  try {
    const target = storage ?? window.sessionStorage
    const key = pendingTurnKey(accountId, notebookId, chatId)
    if (body) target.setItem(key, JSON.stringify({ body, expiresAt: Date.now() + 10 * 60 * 1_000 }))
    else target.removeItem(key)
  } catch {
    // The in-memory submission still supports checking a lost response.
  }
}

function locationKey(accountId: string) {
  return `galaxy-frog:composer-location:${encodeURIComponent(accountId)}`
}

export function readComposerLocation(accountId: string, storage?: DraftStorage): URLSearchParams {
  try {
    return new URLSearchParams((storage ?? window.localStorage).getItem(locationKey(accountId)) ?? "")
  } catch {
    return new URLSearchParams()
  }
}

export function rememberComposerLocation(accountId: string, notebook: string, chat: string, storage?: DraftStorage) {
  try {
    const entry = new URLSearchParams({ notebook, chat })
    const target = storage ?? window.localStorage
    target.setItem(locationKey(accountId), entry.toString())
  } catch {
    // The current URL still identifies the composer when device storage is blocked.
  }
}

function parseRecovery(value: string | null): DraftRecovery | null {
  if (value === null) return null
  const parsed: unknown = JSON.parse(value)
  if (typeof parsed !== "object" || parsed === null || !("text" in parsed) || !("revision" in parsed)) return null
  const { text, revision } = parsed
  if (typeof text !== "string" || text.length > 1_000 || text.includes("\0")) return null
  if (revision !== null && (typeof revision !== "number" || !Number.isSafeInteger(revision) || revision < 0)) return null
  return { text, revision }
}

export function readDraftRecovery(key: string, storage?: DraftStorage): DraftRecovery | null {
  try {
    return parseRecovery((storage ?? window.localStorage).getItem(key))
  } catch {
    return null
  }
}

export function writeDraftRecovery(
  key: string,
  draft: DraftRecovery | null,
  { previous, storage }: { previous?: DraftRecovery; storage?: DraftStorage } = {},
): boolean {
  try {
    const target = storage ?? window.localStorage
    if (previous) {
      const current = readDraftRecovery(key, target)
      if (current?.text !== previous.text || current.revision !== previous.revision) return false
    }
    if (draft) target.setItem(key, JSON.stringify(draft))
    else target.removeItem(key)
    return true
  } catch {
    return false
  }
}
