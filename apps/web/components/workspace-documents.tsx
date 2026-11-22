"use client"

import { FileArrowUpIcon } from "@phosphor-icons/react/dist/csr/FileArrowUp"
import { useEffect, useId, useRef, useState, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import { Disclosure } from "@/components/ui/disclosure"
import { WorkspacePageOCRPanel } from "@/components/workspace-page-ocr"
import { ApiClientError } from "@/lib/api/client"
import {
  documentPageImageUrl, getDocumentSection, importDocument, listDocuments,
  type WorkspaceDocument, type WorkspaceDocumentSection,
} from "@/lib/api/workspace"

const MAX_BYTES = 8 * 1024 * 1024
const formatLabel = { pdf: "PDF", docx: "DOCX", plain: "Plain text", markdown: "Markdown" }

function handleError(error: unknown, onExpired: () => void, setError: (message: string) => void) {
  if (error instanceof ApiClientError && error.status === 401) onExpired()
  else setError(error instanceof Error ? error.message : "Could not complete this document request.")
}

export function WorkspaceDocuments({ notebookId, selectedId, onSelect, onExpired }: {
  notebookId: string
  selectedId?: string
  onSelect: (document: WorkspaceDocument) => void
  onExpired: () => void
}) {
  const id = useId()
  const upload = useRef<AbortController | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [sources, setSources] = useState<WorkspaceDocument[]>([])
  const [after, setAfter] = useState<string | null>(null)
  const [nextAfter, setNextAfter] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [loadError, setLoadError] = useState("")
  const [saveError, setSaveError] = useState("")

  useEffect(() => () => upload.current?.abort(), [])

  useEffect(() => {
    const request = new AbortController()
    void listDocuments(notebookId, after, request.signal).then(page => {
      if (request.signal.aborted) return
      setSources(current => [...current, ...page.sources.filter(item => !current.some(source => source.source_id === item.source_id))])
      setNextAfter(page.next_after)
    }).catch(error => {
      if (!request.signal.aborted) handleError(error, onExpired, setLoadError)
    }).finally(() => {
      if (!request.signal.aborted) setLoading(false)
    })
    return () => request.abort()
  }, [notebookId, after, attempt, onExpired])

  async function addFile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!file || upload.current) return
    if (file.size < 1 || file.size > MAX_BYTES) {
      setSaveError("Choose a nonempty file of 8 MiB or less.")
      return
    }
    const request = new AbortController()
    upload.current = request
    setSaving(true)
    setSaveError("")
    try {
      const source = await importDocument(notebookId, file, request.signal)
      if (request.signal.aborted) return
      setSources(current => [source, ...current.filter(item => item.source_id !== source.source_id)])
      setFile(null)
      if (input.current) input.current.value = ""
      onSelect(source)
    } catch (error) {
      if (!request.signal.aborted) handleError(error, onExpired, setSaveError)
    } finally {
      upload.current = null
      if (!request.signal.aborted) setSaving(false)
    }
  }

  return (
    <section aria-labelledby={`${id}-heading`} className="space-y-3">
      <h2 id={`${id}-heading`} className="text-base font-semibold">Documents</h2>
      {sources.length > 0 ? (
        <ul className="divide-y border-y">
          {sources.map(source => (
            <li key={source.source_id}>
              <Button variant="ghost" type="button" aria-pressed={source.source_id === selectedId} className="min-h-12 w-full justify-between gap-3 px-2 text-left" onClick={() => onSelect(source)}>
                <span className="truncate">{source.filename}</span>
                <span className="text-xs text-muted-foreground">{formatLabel[source.format]}</span>
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
      {loading ? <p role="status" className="text-sm text-muted-foreground">Loading documents…</p> : null}
      {loadError ? (
        <div className="space-y-2">
          <p role="alert" className="text-sm text-destructive">{loadError}</p>
          <Button variant="outline" className="min-h-11" onClick={() => { setLoadError(""); setLoading(true); setAttempt(attempt + 1) }}>Retry documents</Button>
        </div>
      ) : nextAfter ? (
        <Button variant="outline" disabled={loading} className="min-h-11" onClick={() => { setLoading(true); setAfter(nextAfter) }}>Load more documents</Button>
      ) : null}
      <Disclosure label="Import a document">
        <form onSubmit={addFile} className="max-w-xl space-y-3 px-2 pt-2">
          <label htmlFor={`${id}-file`} className="block text-sm font-medium">Document file</label>
          <input
            ref={input}
            id={`${id}-file`}
            aria-describedby={`${id}-formats`}
            type="file"
            className="min-h-11 w-full rounded-md border border-input bg-card px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-ring file:me-3 file:font-medium"
            accept=".pdf,.docx,.txt,.md,.markdown"
            required
            disabled={saving}
            onChange={event => { setFile(event.target.files?.[0] ?? null); setSaveError("") }}
          />
          <p id={`${id}-formats`} className="text-sm text-muted-foreground">PDF, DOCX, TXT, or Markdown. Up to 8 MiB.</p>
          <Button type="submit" disabled={!file || saving} className="min-h-11 gap-2 px-4">
            <FileArrowUpIcon aria-hidden="true" /> {saving ? "Importing…" : "Add to notebook"}
          </Button>
          {saving ? <p role="status" className="text-sm text-muted-foreground">Preparing the document preview…</p> : null}
          {saveError ? <p role="alert" className="text-sm text-destructive">{saveError}</p> : null}
        </form>
      </Disclosure>
    </section>
  )
}

export function WorkspaceDocumentPreview({ notebookId, source, match, onExpired }: {
  notebookId: string
  source: WorkspaceDocument
  match?: { section_number: number; block: { text: string }; kind: "document" | "page_ocr" }
  onExpired: () => void
}) {
  const heading = useRef<HTMLHeadingElement>(null)
  const [page, setPage] = useState(match?.section_number ?? 1)
  const [section, setSection] = useState<WorkspaceDocumentSection | null>(null)
  const [loadError, setLoadError] = useState("")
  const [imageError, setImageError] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const pdf = source.format === "pdf"
  const current = section?.section_number === page ? section : null

  useEffect(() => { heading.current?.focus({ preventScroll: true }) }, [])

  useEffect(() => {
    const request = new AbortController()
    void getDocumentSection(notebookId, source, page, request.signal).then(data => {
      if (!request.signal.aborted) setSection(data)
    }).catch(error => {
      if (!request.signal.aborted) handleError(error, onExpired, setLoadError)
    })
    return () => request.abort()
  }, [notebookId, source, page, onExpired, attempt])

  function goToPage(next: number) {
    setLoadError("")
    setImageError(false)
    setPage(next)
  }

  const text = loadError ? (
    <div className="space-y-2">
      <p role="alert" className="text-sm text-destructive">{loadError}</p>
      <Button variant="outline" className="min-h-11" onClick={() => { setLoadError(""); setAttempt(attempt + 1) }}>Retry text</Button>
    </div>
  ) : current ? (
    <p className="text-sm leading-6 whitespace-pre-wrap">{current.text || "This page has no extractable text. OCR may be needed."}</p>
  ) : <p role="status" className="text-sm text-muted-foreground">Loading text…</p>

  return (
    <div className="space-y-3">
      <h2 ref={heading} tabIndex={-1} className="rounded text-xl font-semibold">{source.filename}</h2>
      <p className="text-sm text-muted-foreground">
        {formatLabel[source.format]}
        {source.pages_without_text ? ` · ${source.pages_without_text} ${source.pages_without_text === 1 ? "page" : "pages"} without extractable text` : ""}
      </p>
      {match && page === match.section_number ? <div className="border-s-2 border-primary ps-3">
        <p className="text-xs text-muted-foreground">{match.kind === "page_ocr" ? `Matching OCR text on page ${page}` : pdf ? `Match on page ${page}` : "Matching passage"}</p>
        <p className="text-sm leading-6 whitespace-pre-wrap wrap-anywhere">{match.block.text}</p>
      </div> : null}
      {pdf ? (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Button variant="outline" className="min-h-11" disabled={page === 1} onClick={() => goToPage(page - 1)}>Previous</Button>
            <p aria-live="polite" className="text-xs text-muted-foreground">Page {page} of {source.section_count}</p>
            <Button variant="outline" className="min-h-11" disabled={page === source.section_count} onClick={() => goToPage(page + 1)}>Next</Button>
          </div>
          {imageError ? (
            <div className="space-y-2">
              <p role="status" className="text-sm text-muted-foreground">The page image is unavailable. Open Extracted text or retry the preview.</p>
              <Button variant="outline" className="min-h-11" onClick={() => setImageError(false)}>Retry preview</Button>
            </div>
          ) : (
            // eslint-disable-next-line @next/next/no-img-element -- The same-origin private image needs the browser's session.
            <img key={page} src={documentPageImageUrl(notebookId, source, page)} alt={`Page ${page} of ${source.filename}`} className="mx-auto max-h-[70dvh] w-full rounded-md border object-contain" onError={() => setImageError(true)} />
          )}
          {current && !current.text ? (
            <div className="space-y-2">
              <p className="text-sm text-muted-foreground">This page has no extractable text.</p>
              <WorkspacePageOCRPanel key={page} notebookId={notebookId} source={source} page={page} onExpired={onExpired} />
            </div>
          ) : null}
          {!current || current.text ? <Disclosure label="Extracted text">{text}</Disclosure> : null}
        </>
      ) : <div className="border-t pt-3">{text}</div>}
    </div>
  )
}
