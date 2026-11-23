"use client"

import { NotePencilIcon } from "@phosphor-icons/react/dist/csr/NotePencil"
import { useEffect, useId, useRef, useState, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import { Disclosure } from "@/components/ui/disclosure"
import { ApiClientError } from "@/lib/api/client"
import type { WorkspaceTextDraft, WorkspaceTextSource, WorkspaceTextVersion } from "@/lib/api/workspace"
import { useWorkspaceSession } from "@/lib/workspace-session-context"

const inputStyle = "min-h-11 w-full rounded-md border border-input bg-card px-3 py-2 text-base focus-visible:outline-2 focus-visible:outline-ring"
const draftDefault: WorkspaceTextDraft = { title: "", format: "plain", text: "" }

function reportError(error: unknown, onExpired: () => void, setError: (text: string) => void) {
  if (error instanceof ApiClientError && error.status === 401) onExpired()
  else setError(error instanceof Error ? error.message : "Could not load this text source. Try again.")
}

export function WorkspaceTextSources({ notebookId, selectedId, onSelect, onExpired }: {
  notebookId: string
  selectedId?: string
  onSelect: (source: WorkspaceTextSource) => void
  onExpired: () => void
}) {
  const { listTextSources, pasteTextSource } = useWorkspaceSession().api
  const fieldId = useId()
  const pendingSave = useRef<AbortController | null>(null)
  const [sources, setSources] = useState<WorkspaceTextSource[]>([])
  const [draft, setDraft] = useState(draftDefault)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState("")
  const [readError, setReadError] = useState("")
  const [loading, setLoading] = useState(true)
  const [after, setAfter] = useState<string | null>(null)
  const [nextAfter, setNextAfter] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => () => pendingSave.current?.abort(), [])

  useEffect(() => {
    const request = new AbortController()
    void listTextSources(notebookId, after, request.signal).then(page => {
      if (request.signal.aborted) return
      setSources(current => [...current, ...page.sources.filter(item => !current.some(existing => existing.source_id === item.source_id))])
      setNextAfter(page.next_after)
    }).catch(caught => {
      if (!request.signal.aborted) reportError(caught, onExpired, setReadError)
    }).finally(() => {
      if (!request.signal.aborted) setLoading(false)
    })
    return () => request.abort()
  }, [listTextSources, notebookId, after, attempt, onExpired])

  async function addText(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pendingSave.current || !draft.title.trim() || !draft.text.trim()) return
    const request = new AbortController()
    pendingSave.current = request
    setSaving(true)
    setSaveError("")
    try {
      const source = await pasteTextSource(notebookId, draft, request.signal)
      if (request.signal.aborted) return
      setSources(current => [source, ...current.filter(item => item.source_id !== source.source_id)])
      setDraft(draftDefault)
      onSelect(source)
    } catch (caught) {
      if (!request.signal.aborted) reportError(caught, onExpired, setSaveError)
    } finally {
      pendingSave.current = null
      if (!request.signal.aborted) setSaving(false)
    }
  }

  return (
    <section aria-labelledby={`${fieldId}-heading`} className="space-y-3">
      <h2 id={`${fieldId}-heading`} className="text-base font-semibold">Text sources</h2>
      {sources.length > 0 ? (
        <ul className="divide-y border-y">
          {sources.map(source => (
            <li key={source.source_id}>
              <Button variant="ghost" type="button" aria-pressed={source.source_id === selectedId} className="min-h-12 w-full justify-between gap-3 px-2 text-left" onClick={() => onSelect(source)}>
                <span className="truncate">{source.title}</span>
                <span className="text-xs text-muted-foreground">{source.format === "markdown" ? "Markdown" : "Plain text"}</span>
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
      {loading ? <p role="status" className="text-sm text-muted-foreground">Loading text sources…</p> : null}
      {readError ? (
        <div className="space-y-2">
          <p role="alert" className="text-sm text-destructive">{readError}</p>
          <Button variant="outline" className="min-h-11" onClick={() => { setLoading(true); setReadError(""); setAttempt(attempt + 1) }}>Retry text sources</Button>
        </div>
      ) : nextAfter ? (
        <Button variant="outline" disabled={loading} className="min-h-11" onClick={() => { setLoading(true); setAfter(nextAfter) }}>Load more text sources</Button>
      ) : null}
      <Disclosure label="Paste text">
        <form onSubmit={addText} className="max-w-xl space-y-3 px-2 pt-2">
          <div className="space-y-1.5">
            <label htmlFor={`${fieldId}-title`} className="block text-sm font-medium">Source title</label>
            <input id={`${fieldId}-title`} className={inputStyle} maxLength={120} required value={draft.title} disabled={saving} onChange={event => setDraft({ ...draft, title: event.target.value })} />
          </div>
          <div className="space-y-1.5">
            <label htmlFor={`${fieldId}-format`} className="block text-sm font-medium">Format</label>
            <select id={`${fieldId}-format`} className={inputStyle} value={draft.format} disabled={saving} onChange={event => setDraft({ ...draft, format: event.target.value === "markdown" ? "markdown" : "plain" })}>
              <option value="plain">Plain text</option>
              <option value="markdown">Markdown</option>
            </select>
          </div>
          <div className="space-y-1.5">
            <label htmlFor={`${fieldId}-text`} className="block text-sm font-medium">Text</label>
            <textarea id={`${fieldId}-text`} className={inputStyle} rows={6} maxLength={65536} required value={draft.text} disabled={saving} onChange={event => setDraft({ ...draft, text: event.target.value })} />
          </div>
          <Button type="submit" disabled={saving || !draft.title.trim() || !draft.text.trim()} className="min-h-11 gap-2 px-4">
            <NotePencilIcon aria-hidden="true" /> {saving ? "Adding text…" : "Add to notebook"}
          </Button>
          {saveError ? <p role="alert" className="text-sm text-destructive">{saveError}</p> : null}
        </form>
      </Disclosure>
    </section>
  )
}

export function WorkspaceTextPreview({ notebookId, source, matchBlock, onExpired }: {
  notebookId: string
  source: WorkspaceTextSource
  matchBlock?: WorkspaceTextVersion["blocks"][number]
  onExpired: () => void
}) {
  const { getTextVersion } = useWorkspaceSession().api
  const heading = useRef<HTMLHeadingElement>(null)
  const [version, setVersion] = useState<WorkspaceTextVersion | null>(null)
  const [error, setError] = useState("")
  const [attempt, setAttempt] = useState(0)
  const match = version?.blocks.find(block => block.block_id === matchBlock?.block_id)
  const matchEnd = match && Math.min(match.end_char, matchBlock?.end_char ?? match.end_char)
  const characters = version && match ? Array.from(version.text) : null

  useEffect(() => {
    heading.current?.focus({ preventScroll: true })
  }, [])

  useEffect(() => {
    const request = new AbortController()
    void getTextVersion(notebookId, source, request.signal).then(data => {
      if (!request.signal.aborted) setVersion(data)
    }).catch(caught => {
      if (!request.signal.aborted) reportError(caught, onExpired, setError)
    })
    return () => request.abort()
  }, [getTextVersion, notebookId, source, onExpired, attempt])

  return (
    <div className="space-y-3">
      <h2 ref={heading} tabIndex={-1} className="rounded text-xl font-semibold">{source.title}</h2>
      <p className="text-sm text-muted-foreground">{source.format === "markdown" ? "Markdown source" : "Plain text"}</p>
      {error ? (
        <>
          <p role="alert" className="text-sm text-destructive">{error}</p>
          <Button variant="outline" className="min-h-11" onClick={() => { setError(""); setAttempt(attempt + 1) }}>Retry preview</Button>
        </>
      ) : version ? (
        <p className="border-t pt-3 text-sm leading-6 whitespace-pre-wrap wrap-anywhere">{
          match && matchEnd !== undefined && characters ? <>{characters.slice(0, match.start_char).join("")}<mark className="rounded bg-accent text-accent-foreground">{characters.slice(match.start_char, matchEnd).join("")}</mark>{characters.slice(matchEnd).join("")}</> : version.text
        }</p>
      ) : <p role="status" className="text-sm text-muted-foreground">Loading preview…</p>}
    </div>
  )
}
