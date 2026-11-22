"use client"

import { MagnifyingGlassIcon } from "@phosphor-icons/react/dist/csr/MagnifyingGlass"
import { useEffect, useId, useRef, useState, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import { Disclosure } from "@/components/ui/disclosure"
import { ApiClientError } from "@/lib/api/client"
import { answerWorkspaceQuestion, searchWorkspaceSources, type WorkspaceAnswer, type WorkspaceSearchHit } from "@/lib/api/workspace"
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

function sourceTitle(hit: WorkspaceSearchHit) {
  return "filename" in hit.source ? hit.source.filename : hit.source.title
}

function evidenceId(hit: WorkspaceSearchHit) {
  return hit.kind === "video" ? hit.unit_id : hit.block.block_id
}

function ElapsedBadge({ elapsed, operation }: { elapsed: number; operation: "Search" | "Answer" }) {
  return <span className="rounded border border-border px-1.5 py-0.5 font-sans text-xs tabular-nums" aria-label={`${operation} took ${duration(elapsed)}`} title={`Time to receive the ${operation.toLowerCase()} response`}>{duration(elapsed)}</span>
}

const maxPassages = 6

function WorkspacePassageAnswer({ notebookId, query, selected, onOpen, onExpired }: {
  notebookId: string
  query: string
  selected: string[]
  onOpen: (hit: WorkspaceSearchHit) => void
  onExpired: () => void
}) {
  const field = useId()
  const request = useRef<AbortController | null>(null)
  const [question, setQuestion] = useState("")
  const [pending, setPending] = useState(false)
  const [error, setError] = useState("")
  const [result, setResult] = useState<{ question: string; answer: WorkspaceAnswer; elapsed: number } | null>(null)

  useEffect(() => () => request.current?.abort(), [])

  async function answer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const submitted = question.trim()
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

  return (
    <Disclosure label="Ask about selected passages" details={`${selected.length} selected`}>
      <form onSubmit={answer} className="space-y-2 px-2 pt-2">
        <label htmlFor={field} className="block text-sm font-medium">Question</label>
        <textarea id={field} rows={2} required maxLength={1_000} value={question} onChange={event => setQuestion(event.target.value)} className="w-full rounded-md border border-input bg-card px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-ring" />
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <Button type="submit" className="min-h-11" disabled={pending || !selected.length || !question.trim()}>Ask with Ollama</Button>
          {pending ? <p role="status" className="text-sm text-muted-foreground">Reading the selected passages…</p> : null}
        </div>
        {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
      </form>
      {result ? (
        <div className="space-y-3 px-2 pt-4">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm text-muted-foreground">
            <p role="status" className="min-w-0 wrap-anywhere">{result.answer.blocks.length ? `Answer · ${result.answer.model}` : "The selected passages could not establish an answer."}</p>
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
    </Disclosure>
  )
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
  const [selected, setSelected] = useState<string[]>([])
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
            <ElapsedBadge elapsed={result.elapsed} operation="Search" />
          </div>
          {result.hits.length ? (
            <>
              <WorkspacePassageAnswer notebookId={notebookId} query={result.query} selected={selected} onOpen={onOpen} onExpired={onExpired} />
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
                  <button type="button" className="block w-full rounded-md px-2 py-2.5 text-left hover:bg-accent focus-visible:outline-2 focus-visible:outline-ring" onClick={() => onOpen(hit)}>
                    <span className="flex flex-wrap items-baseline justify-between gap-x-2">
                      <span className="min-w-0 text-sm font-semibold wrap-anywhere">{sourceTitle(hit)}</span>
                      <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{location(hit)}</span>
                    </span>
                    <span className="mt-1 line-clamp-2 text-sm text-muted-foreground wrap-anywhere">{hit.kind === "video" ? hit.text : hit.block.text}</span>
                  </button>
                </li>
              ))}
              </ul>
            </>
          ) : null}
        </>
      ) : null}
    </section>
  )
}
