"use client"

import { useCallback, useEffect, useRef, useState, type RefObject } from "react"

import { ApiClientError } from "@/lib/api/client"
import { getWorkspaceChat, saveWorkspaceDraft, type WorkspacePromptDraft } from "@/lib/api/workspace"
import { readDraftRecovery, workspaceDraftKey, writeDraftRecovery } from "@/lib/workspace-draft-recovery"

type SaveState = "saved" | "saving" | "browser" | "offline" | "conflict" | "error"
export type DraftFlushRef = RefObject<(() => Promise<void>) | null>

export function useWorkspaceDraft({ accountId, notebookId, chatId, initial, draftFlushRef, onExpired }: {
  accountId: string
  notebookId: string
  chatId?: string
  initial?: WorkspacePromptDraft
  draftFlushRef: DraftFlushRef
  onExpired: () => void
}) {
  const key = workspaceDraftKey(accountId, notebookId, chatId)
  const [recovery] = useState(() => readDraftRecovery(key))
  const unsaved = recovery && recovery.text !== initial?.text ? recovery : null
  const stale = Boolean(initial && unsaved && unsaved.revision !== initial.revision)
  const [text, setText] = useState(unsaved?.text ?? initial?.text ?? "")
  const [status, setStatus] = useState<SaveState>(() =>
    stale ? "conflict" : unsaved ? (chatId ? "saving" : "browser") : "saved")
  const [keptOnDevice, setKeptOnDevice] = useState(Boolean(unsaved))
  const [deviceWriteFailed, setDeviceWriteFailed] = useState(false)
  const [remote, setRemote] = useState<WorkspacePromptDraft | null>(null)
  const [reviewing, setReviewing] = useState(false)
  const [reviewError, setReviewError] = useState("")
  const state = useRef({
    saved: initial,
    text,
    edit: unsaved,
    mounted: false,
    pending: null as Promise<void> | null,
    conflict: stale,
    timer: 0,
  })
  const review = useRef<AbortController | null>(null)

  const flush = useCallback((): Promise<void> => {
    const draft = state.current
    window.clearTimeout(draft.timer)
    if (draft.pending) return draft.pending
    if (!chatId || !draft.saved || !draft.edit || draft.conflict) return Promise.resolve()

    async function sync(id: string) {
      if (draft.mounted) setStatus("saving")
      try {
        while (draft.edit && draft.saved) {
          const submitted = draft.edit
          const saved = await saveWorkspaceDraft(notebookId, id, {
            text: submitted.text, revision: draft.saved.revision,
          })
          draft.saved = saved
          if (draft.edit === submitted) {
            writeDraftRecovery(key, null, { previous: submitted })
            draft.edit = null
          } else {
            // Keep the last keystrokes and advance their base only after this editor's write succeeds.
            const rebased = { text: draft.edit.text, revision: saved.revision }
            const cached = writeDraftRecovery(key, rebased, { previous: draft.edit })
            draft.edit = rebased
            if (draft.mounted) setKeptOnDevice(cached)
          }
        }
        if (draft.mounted) {
          setStatus("saved")
          setKeptOnDevice(false)
          setDeviceWriteFailed(false)
        }
      } catch (caught) {
        const code = caught instanceof ApiClientError ? caught.status : 500
        draft.conflict = code === 409
        if (draft.mounted) {
          if (code === 401) onExpired()
          else setStatus(code === 409 ? "conflict" : code === 0 ? "offline" : "error")
        }
      } finally {
        draft.pending = null
      }
    }
    draft.pending = sync(chatId)
    return draft.pending
  }, [chatId, key, notebookId, onExpired])

  useEffect(() => {
    const draft = state.current
    draft.mounted = true
    draftFlushRef.current = flush
    const onLeave = () => { void flush() }
    window.addEventListener("pagehide", onLeave)
    window.addEventListener("online", onLeave)
    if (initial && recovery?.text === initial.text) {
      writeDraftRecovery(key, null, { previous: recovery })
    }
    void flush()
    return () => {
      window.removeEventListener("pagehide", onLeave)
      window.removeEventListener("online", onLeave)
      if (draftFlushRef.current === flush) draftFlushRef.current = null
      review.current?.abort()
      draft.mounted = false
      onLeave()
    }
  }, [draftFlushRef, flush, initial, key, recovery])

  function edit(next: string) {
    const draft = state.current
    draft.text = next
    setText(next)
    const change = { text: next, revision: draft.saved?.revision ?? null }
    draft.edit = change
    // Save before returning from the input event so closing the page cannot race a debounce.
    const written = writeDraftRecovery(key, chatId || next ? change : null)
    setKeptOnDevice(written && Boolean(chatId || next))
    setDeviceWriteFailed(!written)
    if (!chatId) {
      setStatus(written ? next ? "browser" : "saved" : "error")
      return
    }
    if (draft.conflict) return
    window.clearTimeout(draft.timer)
    setStatus("saving")
    draft.timer = window.setTimeout(() => { void flush() }, 350)
  }

  async function compare() {
    if (!chatId || review.current) return
    const request = new AbortController()
    review.current = request
    setReviewing(true)
    setReviewError("")
    try {
      const chat = await getWorkspaceChat(notebookId, chatId, request.signal)
      if (!request.signal.aborted) setRemote(chat.draft)
    } catch (caught) {
      if (request.signal.aborted) return
      if (caught instanceof ApiClientError && caught.status === 401) onExpired()
      else setReviewError(caught instanceof Error ? caught.message : "The saved draft could not load.")
    } finally {
      if (!request.signal.aborted) {
        review.current = null
        setReviewing(false)
      }
    }
  }

  function resolve(useSaved: boolean) {
    if (!remote) return
    const draft = state.current
    draft.saved = remote
    draft.conflict = false
    setRemote(null)
    if (useSaved || draft.text === remote.text) {
      if (draft.edit) writeDraftRecovery(key, null, { previous: draft.edit })
      draft.edit = null
      draft.text = remote.text
      setText(remote.text)
      setStatus("saved")
      setKeptOnDevice(false)
      setDeviceWriteFailed(false)
    } else {
      const change = { text: draft.text, revision: remote.revision }
      draft.edit = change
      const written = writeDraftRecovery(key, change)
      setKeptOnDevice(written)
      setDeviceWriteFailed(!written)
      void flush()
    }
  }

  return {
    text, status, remote, reviewing, reviewError, keptOnDevice, deviceWriteFailed,
    recovered: Boolean(unsaved?.text), edit, flush, compare, resolve,
  }
}
