"use client"

import { useEffect, useId, useRef, useState, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import { Disclosure } from "@/components/ui/disclosure"
import { ElapsedBadge, EvidenceMatchButton, evidenceId } from "@/components/workspace-evidence-display"
import { ApiClientError } from "@/lib/api/client"
import {
  buildWorkspaceSemanticIndex, confirmWorkspaceCloudEmbedding, getWorkspaceSemanticIndex, previewWorkspaceCloudEmbedding,
  searchWorkspaceMeaning, type CloudEmbeddingAction, type CloudEmbeddingPreview, type WorkspaceSearchHit, type WorkspaceSemanticStatus,
} from "@/lib/api/workspace"

type Props = { notebookId: string; onOpen: (hit: WorkspaceSearchHit) => void; onExpired: () => void }
type Operation = "status" | "index" | "search" | "preview"
type Target = "local" | NonNullable<CloudEmbeddingAction["profile"]>

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
  const [target, setTarget] = useState<Target | null>(null)
  const [review, setReview] = useState<{ action: CloudEmbeddingAction; preview: CloudEmbeddingPreview } | null>(null)
  const [permission, setPermission] = useState(false)
  const activeProvider = status?.active_profile?.provider
  const selected = target ?? (activeProvider === "cohere" ? "embed-v4.0" : activeProvider === "gemini" ? "gemini-embedding-2" : "local")
  const available = selected === "local" ? status?.available_profile
    : selected === "embed-v4.0" ? status?.cohere_profile : status?.cloud_profile
  const providerName = selected === "embed-v4.0" ? "Cohere" : "Gemini"
  const isCohereReview = review?.preview.model === "embed-v4.0"
  const reviewName = isCohereReview ? "Cohere" : "Gemini"
  const destination = isCohereReview ? "Cohere" : "Google Gemini"
  const compatible = Boolean(available && status?.active_profile?.revision === available.revision
    && status.active_profile.model === available.model && status.active_profile.provider === available.provider)
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
    setReview(null)
    void run("status", async signal => {
      const current = await getWorkspaceSemanticIndex(notebookId, signal)
      if (signal.aborted) return
      setStatus(current)
      setResult(null)
      setBuildTime(null)
    })
  }

  function previewCloud(action: CloudEmbeddingAction) {
    setReview(null)
    setPermission(false)
    void run("preview", async signal => {
      const preview = await previewWorkspaceCloudEmbedding(notebookId, action, signal)
      if (!signal.aborted) setReview({ action, preview })
    })
  }

  function confirmCloud() {
    if (!review || !permission) return
    const { action, preview } = review
    setReview(null)
    setPermission(false)
    void run(action.kind, async signal => {
      if (action.kind === "search") setResult(null)
      const started = performance.now()
      const response = await confirmWorkspaceCloudEmbedding(notebookId, preview.preview_id, action, signal)
      if (signal.aborted) return
      const elapsed = performance.now() - started
      if (response.kind === "index") {
        setStatus(response.index)
        setBuildTime(elapsed)
        setResult(null)
      } else {
        setResult({ hits: response.results.hits, elapsed })
      }
    })
  }

  function build() {
    if (!available || !status) return
    if (selected !== "local") {
      previewCloud({ kind: "index", profile: selected, expected_collection: status.active_collection, replace })
      return
    }
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
    if (selected !== "local") {
      previewCloud({ kind: "search", profile: selected, query: submitted, collection_id: collection })
      return
    }
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
        <p>{status.indexed} of {status.total} passages indexed{replace ? " in the current index" : ""}</p>
        {buildTime !== null ? <ElapsedBadge elapsed={buildTime} operation="Indexing" /> : null}
      </div>
      {status.cloud_profile || status.cohere_profile || activeProvider === "cohere" || activeProvider === "gemini" || selected !== "local" ? (
        <label className="flex flex-wrap items-center gap-2 text-sm">
          Embedding profile
          <select value={selected} disabled={operation !== null} className="min-h-11 rounded-md border border-input bg-card px-2" onChange={event => {
            const profile = event.target.value
            setTarget(profile === "gemini-embedding-2" || profile === "embed-v4.0" ? profile : "local")
            setReview(null)
            setError("")
            setResult(null)
            setBuildTime(null)
          }}>
            <option value="local">Local BGE-M3</option>
            {status.cloud_profile || activeProvider === "gemini" || selected === "gemini-embedding-2" ? <option value="gemini-embedding-2">Gemini Embedding 2 · Cloud</option> : null}
            {status.cohere_profile || activeProvider === "cohere" || selected === "embed-v4.0" ? <option value="embed-v4.0">Cohere Embed v4 · Cloud</option> : null}
          </select>
        </label>
      ) : null}
      {available ? <p className="text-xs text-muted-foreground" title={available.revision}>{selected !== "local" ? `Cloud · ${available.dimension.toLocaleString()} dimensions` : `Local BGE-M3 · ${available.revision.slice(0, 12)}`}</p>
        : <p className="text-sm text-muted-foreground">{selected !== "local" ? `Configure the optional ${providerName} embedding profile to continue.` : "Start Ollama with BGE-M3 to index or search."}</p>}
      <div className="flex flex-wrap items-center gap-2">
        {available && status.total > 0 && (replace || status.indexed < status.total) ? (
          <Button type="button" variant="secondary" className="min-h-11" disabled={operation !== null} onClick={build}>
            {selected !== "local" ? `Preview ${providerName} ${replace ? "rebuild" : "indexing"}` : replace ? "Rebuild for this revision" : status.indexed ? "Index next batch" : "Build local index"}
          </Button>
        ) : null}
        <Button type="button" variant="ghost" className="min-h-11" disabled={operation !== null} onClick={refresh}>Refresh status</Button>
      </div>
    </div> : operation === null ? <Button type="button" variant="outline" onClick={refresh}>Retry index status</Button> : null}
    {compatible && status && status.indexed > 0 ? <form onSubmit={search} className="space-y-1.5">
      <label htmlFor={field} className="block text-sm font-medium">Topic or phrase</label>
      <div className="flex flex-wrap items-center gap-2">
        <input id={field} type="search" maxLength={160} required value={query} readOnly={operation !== null} onChange={event => { setQuery(event.target.value); setReview(null) }} className="min-h-11 min-w-32 flex-1 rounded-md border border-input bg-card px-3 text-sm focus-visible:outline-2 focus-visible:outline-ring" />
        <Button type="submit" variant="secondary" className="min-h-11" disabled={operation !== null || !query.trim()}>{selected !== "local" ? `Preview ${providerName} search` : "Find related passages"}</Button>
      </div>
    </form> : null}
    {review ? <section aria-label={`${reviewName} request preview`} className="space-y-2.5 rounded-md border border-border bg-card px-3 py-3 text-sm">
      <p className="font-semibold">Review {reviewName} request</p>
      <p className="wrap-anywhere">{review.action.kind === "index" ? `Send ${review.preview.inputs} ${review.preview.inputs === 1 ? "passage excerpt" : "passage excerpts"} to ${destination}.` : `Send “${review.action.query}” to ${destination}.`}</p>
      <p className="text-muted-foreground">
        {review.preview.pricing_basis === "trial" ? "Estimated trial cost" : isCohereReview ? "Estimated cost at the configured rate" : "Estimated list cost"} ${review.preview.estimated_usd} USD
        {review.action.kind === "index" ? " for this batch." : "."}
        {review.action.kind === "index" && review.preview.remaining_inputs > review.preview.inputs
          ? ` $${review.preview.estimated_remaining_usd} USD total for all ${review.preview.remaining_inputs} passages still to index.`
          : null}
        {review.preview.pricing_basis === "trial" ? " For development evaluation." : " The estimate uses input length."}
      </p>
      {isCohereReview
        ? <p className="text-xs text-muted-foreground">Cohere’s data-use terms depend on the account. <a href="https://cohere.com/enterprise-data-commitments" target="_blank" rel="noreferrer" className="underline">Cohere data use</a></p>
        : <p className="text-xs text-muted-foreground">Google may use free-tier inputs to improve its products. <a href="https://ai.google.dev/gemini-api/docs/pricing#gemini-embedding-2" target="_blank" rel="noreferrer" className="underline">Gemini pricing and data use</a></p>}
      <label className="flex min-h-11 items-center gap-2">
        <input type="checkbox" checked={permission} onChange={event => setPermission(event.target.checked)} className="size-4 accent-primary" />
        I approve sending this text to {reviewName}
      </label>
      <div className="flex flex-wrap gap-2">
        <Button type="button" className="min-h-11" disabled={!permission || operation !== null} onClick={confirmCloud}>Confirm and send</Button>
        <Button type="button" variant="ghost" className="min-h-11" onClick={() => setReview(null)}>Cancel</Button>
      </div>
    </section> : null}
    {operation ? <p role="status" className="text-sm text-muted-foreground">{operation === "status" ? "Checking index…" : operation === "index" ? "Indexing passages…" : operation === "preview" ? "Preparing cost preview…" : "Finding related passages…"}</p> : null}
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
