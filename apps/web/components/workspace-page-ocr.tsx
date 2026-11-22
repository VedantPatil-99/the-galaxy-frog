"use client"

import { useEffect, useId, useRef, useState, type FormEvent } from "react"

import { Button } from "@/components/ui/button"
import { Disclosure } from "@/components/ui/disclosure"
import { ApiClientError } from "@/lib/api/client"
import { getPageOCR, startPageOCR, type WorkspaceDocument, type WorkspacePageOCR } from "@/lib/api/workspace"

const notices = {
  unavailable: "Page OCR is off for this workspace.",
  running: "Reading this page with LlamaParse…",
  no_text: "LlamaParse found no readable text.",
  failed: "LlamaParse could not finish OCR for this page.",
  unknown: "LlamaParse may have received this page. Its OCR result could not be confirmed.",
} as const

export function WorkspacePageOCRPanel({ notebookId, source, page, onExpired }: {
  notebookId: string
  source: WorkspaceDocument
  page: number
  onExpired: () => void
}) {
  const consentId = useId()
  const submission = useRef<AbortController | null>(null)
  const [receipt, setReceipt] = useState<WorkspacePageOCR | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState("")
  const [refresh, setRefresh] = useState(0)

  useEffect(() => () => submission.current?.abort(), [])

  useEffect(() => {
    const request = new AbortController()
    async function read() {
      try {
        const state = await getPageOCR(notebookId, source, page, request.signal)
        if (!request.signal.aborted) {
          setReceipt(state)
          if (state.status !== "ready") setError("")
        }
      } catch (caught) {
        if (request.signal.aborted) return
        if (caught instanceof ApiClientError && caught.status === 401) onExpired()
        else setError(caught instanceof Error ? caught.message : "Could not check OCR status.")
      }
    }
    if (!receipt) void read()
    const timer = receipt?.status === "running" ? window.setTimeout(() => { void read() }, 2_500) : undefined
    return () => {
      request.abort()
      window.clearTimeout(timer)
    }
  }, [notebookId, source, page, onExpired, receipt, refresh])

  async function approve(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!confirmed || submission.current || receipt?.status !== "ready") return
    const request = new AbortController()
    submission.current = request
    setSending(true)
    setError("")
    try {
      const result = await startPageOCR(notebookId, source, page, request.signal)
      if (!request.signal.aborted) setReceipt(result)
    } catch (caught) {
      if (request.signal.aborted) return
      if (caught instanceof ApiClientError && caught.status === 401) onExpired()
      else {
        setError(caught instanceof Error ? caught.message : "Could not confirm the OCR request.")
        setReceipt(null)
      }
    } finally {
      submission.current = null
      if (!request.signal.aborted) {
        setSending(false)
        setConfirmed(false)
      }
    }
  }

  return (
    <div className="space-y-2">
      {error ? (
        <div className="space-y-2">
          <p role="alert" className="text-sm text-destructive">{error}</p>
          <Button variant="outline" className="min-h-11" onClick={() => { setError(""); setReceipt(null); setRefresh(refresh + 1) }}>Check OCR status</Button>
        </div>
      ) : null}
      <p role="status" className={sending ? "text-sm text-muted-foreground" : "sr-only"}>
        {sending ? notices.running : receipt?.status === "succeeded" ? "OCR text is ready." : ""}
      </p>
      {receipt?.status === "ready" ? (
        <Disclosure key="consent" label="Read this page with OCR">
          <form onSubmit={approve} className="space-y-3 px-2 pt-2">
            <p className="text-sm leading-6 text-muted-foreground">LlamaParse receives an image of page {page}. The request can use LlamaCloud credits.</p>
            <a href="https://developers.llamaindex.ai/llamaparse/general/data_retention/" target="_blank" rel="noreferrer" className="inline-flex min-h-8 items-center text-sm text-primary underline underline-offset-4">LlamaParse data retention</a>
            <div className="flex items-start gap-2 text-sm">
              <input type="checkbox" id={consentId} className="mt-1 size-4 accent-primary" checked={confirmed} disabled={sending} onChange={event => setConfirmed(event.target.checked)} />
              <label htmlFor={consentId}>I approve sending this page image to LlamaParse.</label>
            </div>
            <Button type="submit" disabled={!confirmed || sending} className="min-h-11">Run OCR</Button>
          </form>
        </Disclosure>
      ) : receipt?.status === "succeeded" ? (
        <Disclosure key="result" label="OCR text" details="LlamaParse">
          <p className="px-2 text-sm leading-6 whitespace-pre-wrap">{receipt.text}</p>
        </Disclosure>
      ) : receipt && receipt.status in notices ? (
        <p role="status" className="text-sm text-muted-foreground">{notices[receipt.status as keyof typeof notices]}</p>
      ) : null}
    </div>
  )
}
