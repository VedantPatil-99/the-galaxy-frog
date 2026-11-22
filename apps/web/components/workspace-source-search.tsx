"use client"

import { MagnifyingGlassIcon } from "@phosphor-icons/react/dist/csr/MagnifyingGlass"
import { useEffect, useId, useRef, useState, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import { ApiClientError } from "@/lib/api/client"
import { searchWorkspaceSources, type WorkspaceSearchHit } from "@/lib/api/workspace"
import { timestamp } from "@/lib/timestamp"

function location(hit: WorkspaceSearchHit) {
  if (hit.kind === "video") return `${timestamp(hit.start_ms)}–${timestamp(hit.end_ms)}`
  if (hit.kind === "text") return "Pasted text"
  if (hit.page_number === null) return `${hit.source.format === "docx" ? "DOCX" : "Text file"}`
  return hit.kind === "page_ocr" ? `Page ${hit.page_number} · OCR` : `Page ${hit.page_number}`
}

function duration(milliseconds: number) {
  if (milliseconds < 1) return "<1 ms"
  return milliseconds < 1_000 ? `${Math.round(milliseconds)} ms` : `${(milliseconds / 1_000).toFixed(1)} s`
}

export function WorkspaceSourceSearch({ notebookId, onOpen, onExpired }: {
  notebookId: string
  onOpen: (hit: WorkspaceSearchHit) => void
  onExpired: () => void
}) {
  const field = useId()
  const request = useRef<AbortController | null>(null)
  const [query, setQuery] = useState("")
  const [result, setResult] = useState<{ query: string; hits: WorkspaceSearchHit[]; elapsed: number } | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => () => request.current?.abort(), [])

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const submitted = query.trim()
    if (!submitted || request.current) return
    const controller = new AbortController()
    request.current = controller
    setPending(true)
    setResult(null)
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
      <label htmlFor={field} id={`${field}-label`} className="block text-base font-semibold">Find words in sources</label>
      <form onSubmit={search} className="flex flex-wrap items-center gap-2">
        <input id={field} type="search" maxLength={160} required value={query} onChange={event => setQuery(event.target.value)} className="min-h-11 min-w-32 flex-1 rounded-md border border-input bg-card px-3 text-sm focus-visible:outline-2 focus-visible:outline-ring" />
        <Button type="submit" variant="secondary" disabled={pending || !query.trim()} className="min-h-11 gap-1.5"><MagnifyingGlassIcon aria-hidden="true" /> Search</Button>
      </form>
      {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
      {pending ? <p role="status" className="text-sm text-muted-foreground">Searching sources…</p> : null}
      {result ? (
        <>
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm text-muted-foreground">
            <p role="status" className="min-w-0 wrap-anywhere">{result.hits.length ? `${result.hits.length} shown` : "No matches"} for “{result.query}”</p>
            <span className="rounded border border-border px-1.5 py-0.5 font-sans text-xs tabular-nums" aria-label={`Search took ${duration(result.elapsed)}`} title="Time from submitting the search to receiving results">{duration(result.elapsed)}</span>
          </div>
          {result.hits.length ? (
            <ul className="divide-y border-y">
              {result.hits.map(hit => (
                <li key={hit.kind === "video" ? hit.unit_id : hit.block.block_id}>
                  <button type="button" className="block w-full rounded-md px-2 py-2.5 text-left hover:bg-accent focus-visible:outline-2 focus-visible:outline-ring" onClick={() => onOpen(hit)}>
                    <span className="flex flex-wrap items-baseline justify-between gap-x-2">
                      <span className="min-w-0 text-sm font-semibold wrap-anywhere">{"filename" in hit.source ? hit.source.filename : hit.source.title}</span>
                      <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{location(hit)}</span>
                    </span>
                    <span className="mt-1 line-clamp-2 text-sm text-muted-foreground wrap-anywhere">{hit.kind === "video" ? hit.text : hit.block.text}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </>
      ) : null}
    </section>
  )
}
