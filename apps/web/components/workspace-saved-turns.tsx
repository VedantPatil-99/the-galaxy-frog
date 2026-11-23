"use client"

import { useEffect, useRef, useState } from "react"

import { Button } from "@/components/ui/button"
import { ApiClientError } from "@/lib/api/client"
import type { WorkspaceSearchHit, WorkspaceTurn } from "@/lib/api/workspace"
import { useWorkspaceSession } from "@/lib/workspace-session-context"

function mergeTurns(current: WorkspaceTurn[], updates: WorkspaceTurn[]) {
  const index = new Map(current.map(turn => [turn.turn_id, turn]))
  for (const turn of updates) {
    const saved = index.get(turn.turn_id)
    if (saved?.run.revision === turn.run.revision && saved.run.failure === "evidence_unavailable"
      && turn.run.status === "completed") continue
    if (!saved || turn.run.revision >= saved.run.revision) index.set(turn.turn_id, turn)
  }
  return [...index.values()].sort((first, second) => first.sequence - second.sequence)
}

function active(turn: WorkspaceTurn) {
  return turn.run.status === "queued" || turn.run.status === "preparing" || turn.run.status === "running"
}

export function useWorkspaceTurns(notebookId: string, chatId?: string) {
  const session = useWorkspaceSession()
  const { listWorkspaceTurns, getWorkspaceRun, stopWorkspaceRun, retryWorkspaceRun } = session.api
  const [turns, setTurns] = useState<WorkspaceTurn[]>([])
  const [next, setNext] = useState<number | null>()
  const [readingOlder, setReadingOlder] = useState(false)
  const [error, setError] = useState("")
  const [actionError, setActionError] = useState("")
  const [offline, setOffline] = useState(() => typeof navigator !== "undefined" && !navigator.onLine)
  const [acting, setActing] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const action = useRef<AbortController | null>(null)
  const retryKeys = useRef(new Map<string, string>())
  const mostRecentPageSequence = useRef(0)

  useEffect(() => () => action.current?.abort(), [])

  useEffect(() => {
    if (!chatId) return
    const id = chatId
    let pending: AbortController | null = null
    let timer = 0
    let delay = 750
    let activeRunId: string | null = null
    const visible = () => document.visibilityState === "visible" && navigator.onLine
    function pause() {
      window.clearTimeout(timer)
      pending?.abort()
      pending = null
    }
    function schedule() {
      if (!activeRunId || !visible()) return
      timer = window.setTimeout(() => { void read(false) }, delay)
      delay = Math.min(delay + 350, 2_500)
    }
    async function read(full: boolean) {
      if (pending || !visible()) return
      const controller = new AbortController()
      pending = controller
      try {
        let current: WorkspaceTurn[]
        if (!full && activeRunId) {
          current = [await getWorkspaceRun(notebookId, id, activeRunId, controller.signal)]
        } else {
          const page = await listWorkspaceTurns(notebookId, id, null, controller.signal)
          if (controller.signal.aborted) return
          const previous = mostRecentPageSequence.current
          const gap = previous > 0 && (page.turns[0]?.sequence ?? 0) > previous + 1
          // A new page can start after a gap if the chat advanced while this tab was away.
          setNext(cursor => cursor === undefined || gap ? page.next_before : cursor)
          mostRecentPageSequence.current = Math.max(previous, page.turns.at(-1)?.sequence ?? 0)
          current = page.turns
        }
        if (controller.signal.aborted) return
        setTurns(turns => mergeTurns(turns, current))
        activeRunId = current.find(active)?.run.run_id ?? null
        setError("")
        schedule()
      } catch (caught) {
        if (!controller.signal.aborted && session.isCurrent()) {
          setError(caught instanceof Error ? caught.message : "Could not load the saved conversation.")
          schedule()
        }
      } finally {
        if (pending === controller) pending = null
      }
    }
    function resume() {
      setOffline(!navigator.onLine)
      pause()
      delay = 750
      if (visible()) void read(true)
    }
    window.addEventListener("online", resume)
    window.addEventListener("offline", resume)
    window.addEventListener("focus", resume)
    document.addEventListener("visibilitychange", resume)
    void read(true)
    return () => {
      pause()
      window.removeEventListener("online", resume)
      window.removeEventListener("offline", resume)
      window.removeEventListener("focus", resume)
      document.removeEventListener("visibilitychange", resume)
    }
  }, [chatId, getWorkspaceRun, listWorkspaceTurns, notebookId, refresh, session])

  async function loadEarlier() {
    if (!chatId || next == null || readingOlder) return
    setReadingOlder(true)
    try {
      const page = await listWorkspaceTurns(notebookId, chatId, next, session.signal)
      setTurns(current => mergeTurns(current, page.turns))
      setNext(cursor => cursor === next ? page.next_before : cursor)
      setError("")
    } catch (caught) {
      if (session.isCurrent()) setError(caught instanceof Error ? caught.message : "Could not load earlier turns.")
    } finally {
      if (session.isCurrent()) setReadingOlder(false)
    }
  }

  function accepted(turn: WorkspaceTurn) {
    setTurns(current => mergeTurns(current, [turn]))
    setRefresh(current => current + 1)
  }

  async function act(kind: "stop" | "retry", turn: WorkspaceTurn) {
    if (!chatId || action.current || !session.isCurrent()) return false
    const controller = new AbortController()
    action.current = controller
    const runId = turn.run.run_id
    const scope = `galaxy-frog:run-retry:${session.accountId}:${notebookId}:${chatId}:${runId}`
    function retryKey() {
      const current = retryKeys.current.get(runId) ?? crypto.randomUUID()
      let key = current
      try {
        const saved = window.sessionStorage.getItem(scope)
        if (saved) {
          const [expiry, previous] = saved.split(":")
          if (previous && Number(expiry) > Date.now()) key = previous
        }
        window.sessionStorage.setItem(scope, `${Date.now() + 600_000}:${key}`)
      } catch { /* The current chat can still retry with its in-memory key. */ }
      retryKeys.current.set(runId, key)
      return key
    }
    function forgetRetry() {
      retryKeys.current.delete(runId)
      try { window.sessionStorage.removeItem(scope) } catch { /* Storage can be disabled. */ }
    }
    setActing(true)
    setActionError("")
    try {
      const result = kind === "stop"
        ? await stopWorkspaceRun(notebookId, chatId, runId, controller.signal)
        : await retryWorkspaceRun(notebookId, chatId, runId, retryKey(), controller.signal)
      if (kind === "retry") forgetRetry()
      accepted(result)
      return true
    } catch (caught) {
      if (controller.signal.aborted || !session.isCurrent()) return false
      const unknown = caught instanceof ApiClientError && (caught.status === 0 || caught.status >= 500
        || (caught.status >= 200 && caught.status < 300))
      if (kind === "retry" && !unknown) forgetRetry()
      setActionError(unknown ? "The request could not be confirmed. Reload the conversation or try the same action again."
        : caught instanceof Error ? caught.message : "The run action could not finish.")
      setRefresh(current => current + 1)
      return false
    } finally {
      action.current = null
      if (!controller.signal.aborted) setActing(false)
    }
  }

  return {
    turns, error, actionError, offline, acting, act, hasEarlier: next != null, readingOlder, loadEarlier,
    active: turns.some(active),
    retry: () => setRefresh(current => current + 1),
    accepted,
  }
}

const failures = {
  access_changed: "Workspace authorization changed before the answer could be saved.",
  evidence_unavailable: "The pinned passage is unavailable. Select an available passage for a new question.",
  model_unavailable: "The configured local Ollama model is unavailable. Check the model and try a new question.",
  answer_unverified: "Ollama returned an answer whose evidence could not be verified.",
  dispatch_unknown: "Ollama may have received this request. Review the run before sending a new question.",
  recovery_exhausted: "The worker could not prepare this run after three claims. You can retry it.",
} satisfies Record<NonNullable<WorkspaceTurn["run"]["failure"]>, string>

export function WorkspaceSavedTurns({ history, onOpen, onRestore }: {
  history: ReturnType<typeof useWorkspaceTurns>
  onOpen: (hit: WorkspaceSearchHit) => void
  onRestore: (question: string) => void
}) {
  const latest = history.turns.at(-1)
  return <div className="space-y-3 px-2 pt-3" aria-label="Saved conversation">
    {history.offline && <p role="status" className="text-sm text-muted-foreground">Offline. Run status will refresh when you reconnect.</p>}
    {history.actionError && <p role="alert" className="text-sm text-destructive">{history.actionError}</p>}
    {history.error && <div className="space-y-1">
      <p role="alert" className="text-sm text-destructive">{history.error}</p>
      <Button type="button" variant="outline" className="min-h-11" onClick={history.retry}>Reload conversation</Button>
    </div>}
    {history.hasEarlier && <Button type="button" variant="outline" className="min-h-11" disabled={history.readingOlder} onClick={() => { void history.loadEarlier() }}>Load earlier turns</Button>}
    <ol className="space-y-4">
      {history.turns.map(turn => {
        const { run } = turn
        const evidence = run.evidence
        const status = run.status === "queued" ? "Queued for Ollama"
          : run.status === "preparing" ? "Preparing the pinned passage"
          : run.status === "running" ? "Running in Ollama"
          : run.status === "completed" ? `Saved answer · ${run.settings.model}`
          : run.status === "stopped" ? "Run stopped"
          : run.failure ? failures[run.failure] : "The run ended without a saved answer."
        const recoverable = run.status === "stopped" || run.status === "failed" || run.status === "uncertain"
        const retried = history.turns.some(previous => previous.run.retry_of_run_id === run.run_id)
        return <li key={turn.turn_id} className="space-y-2 border-s-2 border-border ps-3">
          <p className="text-sm font-medium whitespace-pre-wrap wrap-anywhere">{turn.question}</p>
          <p id={`run-status-${run.run_id}`} tabIndex={-1} role={latest?.turn_id === turn.turn_id ? "status" : undefined} className="text-sm text-muted-foreground wrap-anywhere">{status}</p>
          {run.status === "stopped" && run.execution.possible_dispatch_at && <p className="text-sm text-muted-foreground">Ollama may already have received this question.</p>}
          {active(turn) && <Button type="button" variant="outline" className="min-h-11" aria-disabled={history.acting || history.offline} onClick={async event => {
            if (history.acting || history.offline) return
            const button = event.currentTarget
            const focusStartedHere = document.activeElement === button
            if (await history.act("stop", turn) && focusStartedHere && (document.activeElement === button || document.activeElement === document.body)) {
              document.getElementById(`run-status-${run.run_id}`)?.focus()
            }
          }}>Stop</Button>}
          {recoverable && <div className="flex flex-wrap gap-2">
            <Button type="button" variant="ghost" className="min-h-11" onClick={() => onRestore(turn.question)}>Restore question</Button>
            {!retried && run.failure !== "evidence_unavailable" && <Button type="button" variant="outline" className="min-h-11" disabled={history.acting || history.active || history.offline} onClick={() => { void history.act("retry", turn) }}>Retry run</Button>}
          </div>}
          {run.answer?.blocks.length === 0 && <p className="text-sm">The pinned passage could not establish an answer.</p>}
          {evidence && run.answer?.blocks.map((block, index) => <div key={index} className="space-y-1">
            <p className="text-sm leading-6 whitespace-pre-wrap wrap-anywhere">{block.text}</p>
            <ul className="space-y-1">
              {block.citations.map((citation, number) => <li key={number}>
                <button type="button" className="block min-h-11 w-full rounded-md px-2 py-2 text-left hover:bg-accent focus-visible:outline-2 focus-visible:outline-ring" onClick={() => onOpen({
                  kind: "text", source: evidence.source,
                  block: { block_id: evidence.block_id, start_char: evidence.start_char, end_char: evidence.end_char, text: evidence.excerpt },
                })}>
                  <span className="block text-xs font-semibold text-primary wrap-anywhere">{evidence.source.title}</span>
                  <span className="block text-xs text-muted-foreground wrap-anywhere">“{citation.quote}”</span>
                </button>
              </li>)}
            </ul>
          </div>)}
        </li>
      })}
    </ol>
  </div>
}
