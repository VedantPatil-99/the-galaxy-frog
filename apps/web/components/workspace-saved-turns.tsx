"use client"

import { useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import type { WorkspaceSearchHit, WorkspaceTurn } from "@/lib/api/workspace"
import { useWorkspaceSession } from "@/lib/workspace-session-context"

function mergeTurns(current: WorkspaceTurn[], updates: WorkspaceTurn[]) {
  const index = new Map(current.map(turn => [turn.turn_id, turn]))
  for (const turn of updates) {
    const saved = index.get(turn.turn_id)
    if (!saved || turn.run.revision >= saved.run.revision) index.set(turn.turn_id, turn)
  }
  return [...index.values()].sort((first, second) => first.sequence - second.sequence)
}

function active(turn: WorkspaceTurn) {
  return turn.run.status === "queued" || turn.run.status === "running"
}

export function useWorkspaceTurns(notebookId: string, chatId?: string) {
  const session = useWorkspaceSession()
  const { listWorkspaceTurns } = session.api
  const [turns, setTurns] = useState<WorkspaceTurn[]>([])
  const [next, setNext] = useState<number | null>()
  const [readingOlder, setReadingOlder] = useState(false)
  const [error, setError] = useState("")
  const [refresh, setRefresh] = useState(0)

  useEffect(() => {
    if (!chatId) return
    const id = chatId
    const controller = new AbortController()
    let timer = 0
    let pending = false
    let delay = 750
    async function read() {
      if (pending || controller.signal.aborted) return
      window.clearTimeout(timer)
      pending = true
      try {
        const page = await listWorkspaceTurns(notebookId, id, null, controller.signal)
        if (controller.signal.aborted) return
        setTurns(current => mergeTurns(current, page.turns))
        setNext(current => current === undefined ? page.next_before : current)
        setError("")
        if (page.turns.some(active) && document.visibilityState === "visible") {
          timer = window.setTimeout(read, delay)
          delay = Math.min(delay + 350, 2_500)
        }
      } catch (caught) {
        if (!controller.signal.aborted && session.isCurrent()) {
          setError(caught instanceof Error ? caught.message : "Could not load the saved conversation.")
        }
      } finally {
        pending = false
      }
    }
    const resume = () => { if (document.visibilityState === "visible") void read() }
    window.addEventListener("online", resume)
    document.addEventListener("visibilitychange", resume)
    void read()
    return () => {
      controller.abort()
      window.clearTimeout(timer)
      window.removeEventListener("online", resume)
      document.removeEventListener("visibilitychange", resume)
    }
  }, [chatId, listWorkspaceTurns, notebookId, refresh, session])

  async function loadEarlier() {
    if (!chatId || next == null || readingOlder) return
    setReadingOlder(true)
    try {
      const page = await listWorkspaceTurns(notebookId, chatId, next, session.signal)
      setTurns(current => mergeTurns(current, page.turns))
      setNext(page.next_before)
      setError("")
    } catch (caught) {
      if (session.isCurrent()) setError(caught instanceof Error ? caught.message : "Could not load earlier turns.")
    } finally {
      if (session.isCurrent()) setReadingOlder(false)
    }
  }

  return {
    turns, error, hasEarlier: next != null, readingOlder, loadEarlier,
    active: turns.some(active),
    retry: () => setRefresh(current => current + 1),
    accepted: (turn: WorkspaceTurn) => {
      setTurns(current => mergeTurns(current, [turn]))
      setRefresh(current => current + 1)
    },
  }
}

const failures = {
  access_changed: "Workspace authorization changed before the answer could be saved.",
  evidence_unavailable: "The pinned passage is unavailable. Select an available passage for a new question.",
  model_unavailable: "The configured local Ollama model is unavailable. Check the model and try a new question.",
  answer_unverified: "Ollama returned an answer whose evidence could not be verified.",
  dispatch_unknown: "Ollama may have received this request. Review the run before sending a new question.",
} satisfies Record<NonNullable<WorkspaceTurn["run"]["failure"]>, string>

export function WorkspaceSavedTurns({ history, onOpen }: {
  history: ReturnType<typeof useWorkspaceTurns>
  onOpen: (hit: WorkspaceSearchHit) => void
}) {
  const latest = history.turns.at(-1)
  return <div className="space-y-3 px-2 pt-3" aria-label="Saved conversation">
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
          : run.status === "running" ? "Running in Ollama"
          : run.status === "completed" ? `Saved answer · ${run.settings.model}`
          : run.failure ? failures[run.failure] : "The run ended without a saved answer."
        return <li key={turn.turn_id} className="space-y-2 border-s-2 border-border ps-3">
          <p className="text-sm font-medium whitespace-pre-wrap wrap-anywhere">{turn.question}</p>
          <p role={latest?.turn_id === turn.turn_id ? "status" : undefined} className="text-sm text-muted-foreground wrap-anywhere">{status}</p>
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
