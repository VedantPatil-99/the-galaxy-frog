"use client"

import { useCallback, useEffect, useRef, useState } from "react"

import { ApiClientError } from "@/lib/api/client"
import { getWorkspaceChat, saveWorkspaceDraft, type WorkspacePromptDraft } from "@/lib/api/workspace"

type SaveState = "saved" | "saving" | "offline" | "conflict" | "error"

export function useWorkspaceDraft(
  notebookId: string,
  chatId: string | undefined,
  initial: WorkspacePromptDraft | undefined,
  onExpired: () => void,
) {
  const [text, setText] = useState(initial?.text ?? "")
  const [status, setStatus] = useState<SaveState>("saved")
  const [remote, setRemote] = useState<WorkspacePromptDraft | null>(null)
  const [reviewing, setReviewing] = useState(false)
  const [reviewError, setReviewError] = useState("")
  const state = useRef({
    saved: initial,
    text: initial?.text ?? "",
    mounted: false,
    pending: false,
    conflict: false,
    timer: 0,
  })
  const review = useRef<AbortController | null>(null)

  const flush = useCallback(async () => {
    const draft = state.current
    window.clearTimeout(draft.timer)
    if (!chatId || !draft.saved || draft.pending || draft.conflict || draft.saved.text === draft.text) return
    draft.pending = true
    if (draft.mounted) setStatus("saving")
    try {
      // A user can edit while a write is in flight. Send the latest text with its new revision.
      while (draft.text !== draft.saved.text) {
        draft.saved = await saveWorkspaceDraft(notebookId, chatId, {
          text: draft.text, revision: draft.saved.revision,
        })
      }
      if (draft.mounted) setStatus("saved")
    } catch (caught) {
      const code = caught instanceof ApiClientError ? caught.status : 500
      draft.conflict = code === 409
      if (draft.mounted) {
        if (code === 401) onExpired()
        else setStatus(code === 409 ? "conflict" : code === 0 ? "offline" : "error")
      }
    } finally {
      draft.pending = false
    }
  }, [chatId, notebookId, onExpired])

  useEffect(() => {
    const draft = state.current
    draft.mounted = true
    const onLeave = () => { void flush() }
    window.addEventListener("pagehide", onLeave)
    return () => {
      window.removeEventListener("pagehide", onLeave)
      review.current?.abort()
      draft.mounted = false
      onLeave()
    }
  }, [flush])

  function edit(next: string) {
    const draft = state.current
    draft.text = next
    setText(next)
    if (!chatId || draft.conflict) return
    window.clearTimeout(draft.timer)
    setStatus(!draft.pending && draft.saved?.text === next ? "saved" : "saving")
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
    if (useSaved) {
      draft.text = remote.text
      setText(remote.text)
      setStatus("saved")
    } else {
      if (draft.text === remote.text) setStatus("saved")
      else void flush()
    }
  }

  return { text, status, remote, reviewing, reviewError, edit, flush, compare, resolve }
}
