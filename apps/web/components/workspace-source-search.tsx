"use client"

import { MagnifyingGlassIcon } from "@phosphor-icons/react/dist/csr/MagnifyingGlass"
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import { Disclosure } from "@/components/ui/disclosure"
import { WorkspaceChatPicker } from "@/components/workspace-chat-picker"
import { ElapsedBadge, EvidenceMatchButton, evidenceId, location, sourceTitle } from "@/components/workspace-evidence-display"
import { ApiClientError } from "@/lib/api/client"
import type { WorkspaceAnswer, WorkspaceChat, WorkspaceSearchHit } from "@/lib/api/workspace"
import { useWorkspaceSession } from "@/lib/workspace-session-context"
import { useWorkspaceDraft, type DraftFlushRef } from "@/lib/use-workspace-draft"

const maxPassages = 6

function WorkspacePassageAnswer({ accountId, notebookId, chat, query, selected, draftFlushRef, onDraftPresence, onOpen, onExpired }: {
  accountId: string
  notebookId: string
  chat: WorkspaceChat | null
  query: string
  selected: string[]
  draftFlushRef: DraftFlushRef
  onDraftPresence: (chatId: string, present: boolean) => void
  onOpen: (hit: WorkspaceSearchHit) => void
  onExpired: () => void
}) {
  const { answerWorkspaceQuestion } = useWorkspaceSession().api
  const field = useId()
  const request = useRef<AbortController | null>(null)
  const draft = useWorkspaceDraft({
    accountId, notebookId, chatId: chat?.chat_id, initial: chat?.draft, draftFlushRef, onExpired,
  })
  const [pending, setPending] = useState(false)
  const [error, setError] = useState("")
  const [result, setResult] = useState<{ question: string; answer: WorkspaceAnswer; elapsed: number } | null>(null)
  const chatId = chat?.chat_id ?? ""
  const hasDraft = Boolean(draft.text.trim())

  useEffect(() => () => request.current?.abort(), [])
  useEffect(() => onDraftPresence(chatId, hasDraft), [chatId, hasDraft, onDraftPresence])

  async function answer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const submitted = draft.text.trim()
    if (!submitted || !selected.length || request.current) return
    const controller = new AbortController()
    request.current = controller
    setPending(true)
    setError("")
    setResult(null)
    const started = performance.now()
    try {
      const response = await answerWorkspaceQuestion(notebookId, query, submitted, selected, controller.signal)
      if (!controller.signal.aborted) {
        setResult({ question: submitted, answer: response, elapsed: performance.now() - started })
      }
    } catch (caught) {
      if (controller.signal.aborted) return
      if (caught instanceof ApiClientError && caught.status === 401) onExpired()
      else setError(caught instanceof Error ? caught.message : "The answer could not finish.")
    } finally {
      request.current = null
      if (!controller.signal.aborted) setPending(false)
    }
  }

  const canSend = !pending && selected.length > 0 && Boolean(draft.text.trim())
  const statusText = {
    saved: draft.text ? "Draft saved" : "",
    saving: "Saving draft…",
    browser: "Saved in this browser",
    offline: draft.keptOnDevice ? "Kept in this browser" : "Draft offline",
    conflict: "Draft conflict",
    error: draft.keptOnDevice ? "Kept in this browser" : "Draft save failed",
  }[draft.status]
  const body = (
    <>
      <form onSubmit={answer} className="space-y-2 px-2 pt-2">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <label htmlFor={field} className="text-sm font-medium">Question</label>
          <span role="status" className="text-xs text-muted-foreground">{statusText}</span>
        </div>
        <textarea id={field} rows={3} required maxLength={1_000} value={draft.text} onChange={event => draft.edit(event.target.value)}
          onBlur={() => { void draft.flush() }} onKeyDown={event => {
            if (canSend && event.key === "Enter" && !event.shiftKey && !event.altKey && !event.ctrlKey && !event.metaKey
              && !event.nativeEvent.isComposing && window.matchMedia("(pointer: fine)").matches) {
              event.preventDefault()
              event.currentTarget.form?.requestSubmit()
            }
          }}
          className="w-full rounded-md border border-input bg-card px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-ring" />
        {draft.status === "offline" || draft.status === "error" ? (
          <Button type="button" variant="outline" className="min-h-11" onClick={() => {
            if (chat) void draft.flush()
            else draft.edit(draft.text)
          }}>Retry draft save</Button>
        ) : null}
        {draft.deviceWriteFailed ? <p className="text-xs text-muted-foreground">Browser storage is unavailable.</p> : null}
        {draft.status === "conflict" ? <div className="space-y-2 rounded-md border border-border bg-card px-3 py-2 text-sm">
          {draft.remote ? <>
            <p className="font-medium">Saved draft</p>
            <p className="max-h-32 overflow-auto whitespace-pre-wrap wrap-anywhere text-muted-foreground">{draft.remote.text || "Empty draft"}</p>
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="outline" className="min-h-11" onClick={() => draft.resolve(true)}>Use saved draft</Button>
              <Button type="button" variant="outline" className="min-h-11" onClick={() => draft.resolve(false)}>Save my draft</Button>
            </div>
          </> : <>
            <p>A newer draft is saved. Your text is still in the editor.</p>
            <Button type="button" variant="outline" className="min-h-11" disabled={draft.reviewing} onClick={() => { void draft.compare() }}>Review saved draft</Button>
            {draft.reviewError ? <p role="alert" className="text-destructive">{draft.reviewError}</p> : null}
          </>}
        </div> : null}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <Button type="submit" className="min-h-11" disabled={!canSend}>Ask with Ollama</Button>
          {pending ? <p role="status" className="text-sm text-muted-foreground">Reading the selected passages…</p> : null}
          {!selected.length ? <p className="text-sm text-muted-foreground">Search for passages and select evidence to ask.</p> : null}
        </div>
        {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
      </form>
      {result ? (
        <div className="space-y-3 px-2 pt-4">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm text-muted-foreground">
            <p role="status" className="min-w-0 wrap-anywhere">{result.answer.blocks.length ? `${chat ? "Session answer" : "Answer"} · ${result.answer.model}` : "The selected passages could not establish an answer."}</p>
            <ElapsedBadge elapsed={result.elapsed} operation="Answer" />
          </div>
          {result.answer.blocks.length ? <p className="text-sm font-medium wrap-anywhere">{result.question}</p> : null}
          {result.answer.blocks.map((block, blockIndex) => (
            <div key={blockIndex} className="space-y-1 border-s-2 border-border ps-3">
              <p className="text-sm leading-6 whitespace-pre-wrap wrap-anywhere">{block.text}</p>
              <ul className="space-y-1">
                {block.citations.map((citation, citationIndex) => (
                  <li key={`${evidenceId(citation.evidence)}-${citationIndex}`}>
                    <button type="button" onClick={() => onOpen(citation.evidence)} className="block min-h-11 w-full rounded-md px-2 py-2 text-left hover:bg-accent focus-visible:outline-2 focus-visible:outline-ring">
                      <span className="block text-xs font-semibold text-primary wrap-anywhere">{sourceTitle(citation.evidence)} · {location(citation.evidence)}</span>
                      <span className="block text-xs leading-5 text-muted-foreground wrap-anywhere">“{citation.quote}”</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      ) : null}
    </>
  )
  return chat ? (
    <section aria-label="Chat question" className="space-y-2 border-t pb-2">{body}</section>
  ) : (
    <Disclosure label="Ask about selected passages" details={`${selected.length} selected`} defaultOpen={draft.recovered}>{body}</Disclosure>
  )
}

export function WorkspaceSourceSearch({ accountId, notebookId, draftFlushRef, onOpen, onExpired }: {
  accountId: string
  notebookId: string
  draftFlushRef: DraftFlushRef
  onOpen: (hit: WorkspaceSearchHit) => void
  onExpired: () => void
}) {
  const { searchWorkspaceSources } = useWorkspaceSession().api
  const field = useId()
  const request = useRef<AbortController | null>(null)
  const [query, setQuery] = useState("")
  const [result, setResult] = useState<{ query: string; hits: WorkspaceSearchHit[]; elapsed: number } | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [chat, setChat] = useState<WorkspaceChat | null | undefined>(undefined)
  const [draftPresence, setDraftPresence] = useState<Record<string, boolean>>({})
  const [pending, setPending] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => () => request.current?.abort(), [])
  const markDraft = useCallback((chatId: string, present: boolean) => {
    setDraftPresence(current => current[chatId] === present ? current : { ...current, [chatId]: present })
  }, [])

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const submitted = query.trim()
    if (!submitted || request.current) return
    const controller = new AbortController()
    request.current = controller
    setPending(true)
    setResult(null)
    setSelected([])
    setError("")
    const started = performance.now()
    try {
      const response = await searchWorkspaceSources(notebookId, submitted, controller.signal)
      if (!controller.signal.aborted) {
        setResult({ query: submitted, hits: response.hits, elapsed: performance.now() - started })
      }
    } catch (caught) {
      if (controller.signal.aborted) return
      if (caught instanceof ApiClientError && caught.status === 401) onExpired()
      else setError(caught instanceof Error ? caught.message : "Search could not finish. Try again.")
    } finally {
      request.current = null
      if (!controller.signal.aborted) setPending(false)
    }
  }

  return (
    <section className="space-y-2.5" aria-labelledby={`${field}-label`}>
      <WorkspaceChatPicker accountId={accountId} notebookId={notebookId} draftPresence={draftPresence} onSelect={setChat} onExpired={onExpired} />
      <label htmlFor={field} id={`${field}-label`} className="block text-base font-semibold">Find words in sources</label>
      <form onSubmit={search} className="flex flex-wrap items-center gap-2">
        <input id={field} type="search" maxLength={160} required value={query} onChange={event => setQuery(event.target.value)} className="min-h-11 min-w-32 flex-1 rounded-md border border-input bg-card px-3 text-sm focus-visible:outline-2 focus-visible:outline-ring" />
        <Button type="submit" variant="secondary" disabled={pending || !query.trim()} className="min-h-11 gap-1.5"><MagnifyingGlassIcon aria-hidden="true" /> Search</Button>
      </form>
      {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
      {pending ? <p role="status" className="text-sm text-muted-foreground">Searching sources…</p> : null}
      {result ? (
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm text-muted-foreground">
          <p role="status" className="min-w-0 wrap-anywhere">{result.hits.length ? `${result.hits.length} shown` : "No matches"} for “{result.query}”</p>
          <ElapsedBadge elapsed={result.elapsed} operation="Search" />
        </div>
      ) : null}
      {chat !== undefined ? (
        <WorkspacePassageAnswer key={chat?.chat_id ?? "one-shot"} accountId={accountId} notebookId={notebookId} chat={chat} query={result?.query ?? ""} selected={selected} draftFlushRef={draftFlushRef} onDraftPresence={markDraft} onOpen={onOpen} onExpired={onExpired} />
      ) : null}
      {result?.hits.length ? (
        <ul className="divide-y border-y">
          {result.hits.map(hit => (
            <li key={evidenceId(hit)} className="grid grid-cols-[2.75rem_minmax(0,1fr)] items-start">
              <label className="flex min-h-11 items-center justify-center">
                <span className="sr-only">Use {sourceTitle(hit)} · {location(hit)} in answer</span>
                <input type="checkbox" className="size-4 accent-primary" checked={selected.includes(evidenceId(hit))} disabled={selected.length >= maxPassages && !selected.includes(evidenceId(hit))} onChange={event => {
                  const id = evidenceId(hit)
                  const checked = event.target.checked
                  setSelected(current => checked ? [...current, id] : current.filter(value => value !== id))
                }} />
              </label>
              <EvidenceMatchButton hit={hit} onOpen={onOpen} />
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  )
}
