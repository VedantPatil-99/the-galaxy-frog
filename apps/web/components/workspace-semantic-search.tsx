"use client"

import { useEffect, useId, useRef, useState, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import { Disclosure } from "@/components/ui/disclosure"
import { ElapsedBadge, EvidenceMatchButton, evidenceId } from "@/components/workspace-evidence-display"
import { ApiClientError } from "@/lib/api/client"
import { buildWorkspaceSemanticIndex, getWorkspaceSemanticIndex, searchWorkspaceMeaning, type WorkspaceSearchHit, type WorkspaceSemanticStatus } from "@/lib/api/workspace"

type Props = { notebookId: string; onOpen: (hit: WorkspaceSearchHit) => void; onExpired: () => void }
type Operation = "status" | "index" | "search"

export function WorkspaceSemanticSearch(props: Props) {
  return <Disclosure label="Search by meaning" keepMounted={false}><SemanticPanel {...props} /></Disclosure>
}

function SemanticPanel({ notebookId, onOpen, onExpired }: Props) {
  const field = useId()
  const request = useRef<AbortController | null>(null)
  const [status, setStatus] = useState<WorkspaceSemanticStatus | null>(null)
  const [operation, setOperation] = useState<Operation | null>("status")
  const [error, setError] = useState("")
  const [query, setQuery] = useState("")
  const [buildTime, setBuildTime] = useState<number | null>(null)
  const [result, setResult] = useState<{ hits: WorkspaceSearchHit[]; elapsed: number } | null>(null)
  const available = status?.available_profile
  const compatible = Boolean(available && status?.active_profile?.revision === available.revision
    && status.active_profile.model === available.model)
  const replace = Boolean(status?.active_collection && !compatible)

  useEffect(() => {
    const controller = new AbortController()
    request.current = controller
    void getWorkspaceSemanticIndex(notebookId, controller.signal).then(current => {
      if (!controller.signal.aborted) setStatus(current)
    }).catch((caught: unknown) => {
      if (controller.signal.aborted) return
      if (caught instanceof ApiClientError && caught.status === 401) onExpired()
      else setError(caught instanceof Error ? caught.message : "Could not check the semantic index.")
    }).finally(() => {
      if (!controller.signal.aborted) {
        request.current = null
        setOperation(null)
      }
    })
    return () => request.current?.abort()
  }, [notebookId, onExpired])

  async function run(action: Operation, execute: (signal: AbortSignal) => Promise<void>) {
    if (request.current) return
    const controller = new AbortController()
    request.current = controller
    setOperation(action)
    setError("")
    try {
      await execute(controller.signal)
    } catch (caught) {
      if (controller.signal.aborted) return
      if (caught instanceof ApiClientError && caught.status === 401) onExpired()
      else setError(caught instanceof Error ? caught.message : "The semantic request could not finish.")
    } finally {
      if (!controller.signal.aborted) {
        request.current = null
        setOperation(null)
      }
    }
  }

  function refresh() {
    void run("status", async signal => {
      const current = await getWorkspaceSemanticIndex(notebookId, signal)
      if (signal.aborted) return
      setStatus(current)
      setResult(null)
      setBuildTime(null)
    })
  }

  function build() {
    if (!available || !status) return
    void run("index", async signal => {
      const started = performance.now()
      const current = await buildWorkspaceSemanticIndex(notebookId, {
        profile: "local-bge-m3", revision: available.revision,
        expected_collection: status.active_collection, replace,
      }, signal)
      if (signal.aborted) return
      setStatus(current)
      setBuildTime(performance.now() - started)
      setResult(null)
    })
  }

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const collection = status?.active_collection
    const submitted = query.trim()
    if (!collection || !submitted || !compatible) return
    void run("search", async signal => {
      setResult(null)
      const started = performance.now()
      const response = await searchWorkspaceMeaning(notebookId, { query: submitted, collection_id: collection }, signal)
      if (!signal.aborted) setResult({ hits: response.hits, elapsed: performance.now() - started })
    })
  }

  return <div className="space-y-3 px-2 pt-2">
    {status ? <div className="space-y-2">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm text-muted-foreground">
        <p>{status.indexed} of {status.total} passages indexed</p>
        {buildTime !== null ? <ElapsedBadge elapsed={buildTime} operation="Indexing" /> : null}
      </div>
      {available ? <p className="text-xs text-muted-foreground">Local BGE-M3 <span title={available.revision}>· {available.revision.slice(0, 12)}</span></p> : <p className="text-sm text-muted-foreground">Start Ollama with BGE-M3 to index or search.</p>}
      <div className="flex flex-wrap items-center gap-2">
        {available && status.total > 0 && (replace || status.indexed < status.total) ? (
          <Button type="button" variant="secondary" className="min-h-11" disabled={operation !== null} onClick={build}>
            {replace ? "Rebuild for this revision" : status.indexed ? "Index next batch" : "Build local index"}
          </Button>
        ) : null}
        <Button type="button" variant="ghost" className="min-h-11" disabled={operation !== null} onClick={refresh}>Refresh status</Button>
      </div>
    </div> : operation === null ? <Button type="button" variant="outline" onClick={refresh}>Retry index status</Button> : null}
    {compatible && status && status.indexed > 0 ? <form onSubmit={search} className="space-y-1.5">
      <label htmlFor={field} className="block text-sm font-medium">Topic or phrase</label>
      <div className="flex flex-wrap items-center gap-2">
        <input id={field} type="search" maxLength={160} required value={query} onChange={event => setQuery(event.target.value)} className="min-h-11 min-w-32 flex-1 rounded-md border border-input bg-card px-3 text-sm focus-visible:outline-2 focus-visible:outline-ring" />
        <Button type="submit" variant="secondary" className="min-h-11" disabled={operation !== null || !query.trim()}>Find related passages</Button>
      </div>
    </form> : null}
    {operation ? <p role="status" className="text-sm text-muted-foreground">{operation === "status" ? "Checking index…" : operation === "index" ? "Indexing passages…" : "Finding related passages…"}</p> : null}
    {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
    {result ? <>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm text-muted-foreground">
        <p role="status">{result.hits.length ? `Closest passages (${result.hits.length})` : "No indexed passages found"}</p>
        <ElapsedBadge elapsed={result.elapsed} operation="Search" />
      </div>
      {result.hits.length ? <ul className="divide-y border-y">
        {result.hits.map(hit => <li key={evidenceId(hit)}><EvidenceMatchButton hit={hit} onOpen={onOpen} /></li>)}
      </ul> : null}
    </> : null}
  </div>
}
