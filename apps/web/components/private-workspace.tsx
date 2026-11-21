"use client"

import { BookOpenIcon } from "@phosphor-icons/react/dist/csr/BookOpen"
import { PlusIcon } from "@phosphor-icons/react/dist/csr/Plus"
import { useEffect, useState, type FormEvent, type ReactNode } from "react"

import { Button } from "@/components/ui/button"
import { Disclosure } from "@/components/ui/disclosure"
import { WorkspaceShell, type WorkspacePane } from "@/components/workspace-shell"
import { workspaceAuthAction } from "@/components/workspace-sign-in"
import { ApiClientError } from "@/lib/api/client"
import { createNotebook, getNotebooks, getOwnedTranscript, listSources, type Notebook, type WorkspaceSource, type WorkspaceTranscript } from "@/lib/api/workspace"
import { timestamp } from "@/lib/timestamp"
import { YOUTUBE_PLAYER_ORIGIN } from "@/lib/youtube-player"

function youtubePreview(source: WorkspaceSource, milliseconds: number) {
  if (source.source_kind !== "youtube" || !source.canonical_url) return null
  try {
    const url = new URL(source.canonical_url)
    const id = url.hostname === "youtu.be"
      ? url.pathname.slice(1)
      : ["www.youtube.com", "youtube.com", "m.youtube.com"].includes(url.hostname) ? url.searchParams.get("v") : null
    if (url.protocol !== "https:" || !id || !/^[\w-]{11}$/.test(id)) return null
    return `${YOUTUBE_PLAYER_ORIGIN}/embed/${id}?rel=0&start=${Math.floor(milliseconds / 1000)}`
  } catch {
    return null
  }
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : "Something went wrong. Try again."
}

function SourcePreview({ notebookId, source, onExpired }: {
  notebookId: string
  source: WorkspaceSource
  onExpired: () => void
}) {
  const [transcript, setTranscript] = useState<WorkspaceTranscript | null>(null)
  const [seek, setSeek] = useState(0)
  const [error, setError] = useState("")
  const [attempt, setAttempt] = useState(0)
  const src = youtubePreview(source, seek)

  useEffect(() => {
    const request = new AbortController()
    void getOwnedTranscript(notebookId, source.video_id, request.signal).then(data => {
      if (!request.signal.aborted) setTranscript(data)
    }).catch((caught: unknown) => {
      if (request.signal.aborted) return
      if (caught instanceof ApiClientError && caught.status === 401) onExpired()
      else setError(errorText(caught))
    })
    return () => request.abort()
  }, [attempt, notebookId, onExpired, source.video_id])

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-xl font-semibold">{source.title}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{timestamp(source.duration_ms)} · {source.source_kind === "youtube" ? "YouTube" : "Video"}</p>
      </div>
      {src ? <iframe className="aspect-video w-full rounded-lg bg-black" title={`Preview of ${source.title}`} src={src} allowFullScreen /> : null}
      <Disclosure label="Full transcript" details={transcript ? `${transcript.cues.length} cues` : undefined} keepMounted={false}>
        {error ? (
          <div className="px-2 text-sm">
            <p role="alert" className="text-destructive">{error}</p>
            <Button type="button" variant="outline" className="mt-2 min-h-11" onClick={() => { setError(""); setAttempt(attempt + 1) }}>Retry transcript</Button>
          </div>
        ) : transcript ? (
          <ul className="max-h-96 divide-y overflow-y-auto">
            {transcript.cues.map(cue => (
              <li key={cue.cue_id} className="space-y-1 px-2 py-2.5">
                {src ? (
                  <Button type="button" variant="secondary" className="min-h-11 font-mono" onClick={() => setSeek(cue.start_ms)}>{timestamp(cue.start_ms)}–{timestamp(cue.end_ms)}</Button>
                ) : <p className="font-mono text-xs text-primary">{timestamp(cue.start_ms)}–{timestamp(cue.end_ms)}</p>}
                <p className="text-sm leading-6">{cue.text}</p>
                <p className="text-xs text-muted-foreground">{cue.origin === "asr" ? "Speech transcript" : `${cue.caption_kind ?? "Source"} caption`}</p>
              </li>
            ))}
          </ul>
        ) : <p role="status" className="px-2 text-sm text-muted-foreground">Loading transcript…</p>}
      </Disclosure>
    </div>
  )
}

function NotebookSources({ notebook, pane, setPane, onExpired, galaxy, accountId }: {
  notebook: Notebook
  pane: WorkspacePane
  setPane: (pane: WorkspacePane) => void
  onExpired: () => void
  galaxy: ReactNode
  accountId: string
}) {
  const [sources, setSources] = useState<WorkspaceSource[]>([])
  const [after, setAfter] = useState<string | null>(null)
  const [nextAfter, setNextAfter] = useState<string | null>(null)
  const [selected, setSelected] = useState<WorkspaceSource | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const request = new AbortController()
    void listSources(notebook.notebook_id, after, request.signal).then(data => {
      if (request.signal.aborted) return
      setSources(current => after ? [...current, ...data.sources] : data.sources)
      setNextAfter(data.next_after)
      setSelected(current => current ?? data.sources[0] ?? null)
    }).catch((caught: unknown) => {
      if (request.signal.aborted) return
      if (caught instanceof ApiClientError && caught.status === 401) onExpired()
      else setError(errorText(caught))
    }).finally(() => {
      if (!request.signal.aborted) setLoading(false)
    })
    return () => request.abort()
  }, [after, attempt, notebook.notebook_id, onExpired])

  return (
    <WorkspaceShell
      title={notebook.title}
      pane={pane}
      onPaneChange={setPane}
      galaxy={galaxy}
      preferenceKey={accountId}
      sources={selected ? <SourcePreview key={selected.video_id} notebookId={notebook.notebook_id} source={selected} onExpired={onExpired} /> : (
        <p className="text-sm text-muted-foreground">{loading ? "Loading sources…" : "Select a source in the workspace to preview it here."}</p>
      )}
    >
      <div className="space-y-5">
        <h1 className="text-3xl font-semibold tracking-tight">{notebook.title}</h1>
        <h2 className="text-lg font-semibold">Notebook sources</h2>
        {sources.length ? (
          <ul className="divide-y border-y">
            {sources.map(source => (
              <li key={source.video_id}>
                <Button variant="ghost" type="button" aria-pressed={source.video_id === selected?.video_id} className="min-h-12 w-full justify-between gap-3 px-2 text-left" onClick={() => { setSelected(source); setPane("sources") }}>
                  <span className="truncate">{source.title}</span>
                  <span className="font-mono text-xs text-muted-foreground">{timestamp(source.duration_ms)}</span>
                </Button>
              </li>
            ))}
          </ul>
        ) : !loading && !error ? <p className="text-muted-foreground">This notebook has no assigned sources yet.</p> : null}
        {loading ? <p role="status" className="text-sm text-muted-foreground">Loading sources…</p> : null}
        {error ? (
          <div className="text-sm">
            <p role="alert" className="text-destructive">{error}</p>
            <Button variant="outline" className="mt-2 min-h-11" onClick={() => { setError(""); setLoading(true); setAttempt(attempt + 1) }}>Retry sources</Button>
          </div>
        ) : nextAfter ? (
          <Button variant="outline" disabled={loading} className="min-h-11" onClick={() => { setLoading(true); setAfter(nextAfter) }}>Load more sources</Button>
        ) : null}
      </div>
    </WorkspaceShell>
  )
}

export function PrivateWorkspace({ accountId, onExpired }: { accountId: string; onExpired: () => void }) {
  const [notebooks, setNotebooks] = useState<Notebook[] | null>(null)
  const [selected, setSelected] = useState<string | null>(() => typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get("notebook"))
  const [pane, setPane] = useState<WorkspacePane>("work")
  const [title, setTitle] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const request = new AbortController()
    void getNotebooks(request.signal).then(data => {
      if (!request.signal.aborted) setNotebooks(data)
    }).catch((caught: unknown) => {
      if (request.signal.aborted) return
      if (caught instanceof ApiClientError && caught.status === 401) onExpired()
      else setError(errorText(caught))
    })
    return () => request.abort()
  }, [attempt, onExpired])

  function chooseNotebook(id: string) {
    const url = new URL(window.location.href)
    url.searchParams.set("notebook", id)
    window.history.replaceState(window.history.state, "", url)
    setSelected(id)
    setPane("work")
  }

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!title.trim()) return
    setBusy(true)
    setError("")
    try {
      const created = await createNotebook(title.trim())
      setNotebooks(current => [created, ...(current ?? [])])
      setTitle("")
      chooseNotebook(created.notebook_id)
    } catch (caught) {
      if (caught instanceof ApiClientError && caught.status === 401) onExpired()
      else setError(errorText(caught))
    } finally {
      setBusy(false)
    }
  }

  async function signOut() {
    setBusy(true)
    setError("")
    try {
      await workspaceAuthAction("sign-out")
      onExpired()
    } catch (caught) {
      setError(errorText(caught))
    } finally {
      setBusy(false)
    }
  }

  const notebook = notebooks?.find(item => item.notebook_id === selected) ?? notebooks?.[0]
  const galaxy = (
    <div className="space-y-3">
      <h2 className="px-2 text-sm font-semibold">Notebooks</h2>
      {notebooks?.length ? <ul className="space-y-1">
        {notebooks.map(item => (
          <li key={item.notebook_id}>
            <Button type="button" variant={notebook?.notebook_id === item.notebook_id ? "secondary" : "ghost"} className="min-h-11 w-full justify-start px-2" aria-current={notebook?.notebook_id === item.notebook_id ? "page" : undefined} onClick={() => chooseNotebook(item.notebook_id)}>
              <span className="truncate">{item.title}</span>
            </Button>
          </li>
        ))}
      </ul> : null}
      {notebooks ? <Disclosure label="New notebook">
        <form onSubmit={create} className="space-y-2 px-1">
          <label htmlFor="notebook-title" className="text-sm">Notebook name</label>
          <input id="notebook-title" required maxLength={120} value={title} onChange={event => setTitle(event.target.value)} className="min-h-11 w-full rounded-md border border-input bg-card px-2 text-sm" />
          <Button type="submit" disabled={busy || !title.trim()} className="min-h-11 w-full"><PlusIcon aria-hidden="true" /> Create</Button>
        </form>
      </Disclosure> : null}
      {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
      <Button type="button" disabled={busy} variant="ghost" className="min-h-11" onClick={() => { void signOut() }}>Sign out</Button>
    </div>
  )

  if (notebook) {
    return <NotebookSources key={notebook.notebook_id} notebook={notebook} pane={pane} setPane={setPane} onExpired={onExpired} galaxy={galaxy} accountId={accountId} />
  }

  return (
    <WorkspaceShell title="Private workspace" pane={pane} onPaneChange={setPane} galaxy={galaxy} preferenceKey={accountId}>
      {notebooks === null ? (
        error ? <Button variant="outline" className="min-h-11" onClick={() => { setError(""); setAttempt(attempt + 1) }}>Retry notebooks</Button>
          : <p role="status" className="text-muted-foreground">Loading notebooks…</p>
      ) : (
        <div className="space-y-3">
          <BookOpenIcon aria-hidden="true" className="size-8 text-primary" />
          <h1 className="text-3xl font-semibold tracking-tight">Start a notebook</h1>
          <p className="text-muted-foreground">Use New notebook in the Notebooks panel to give it a name.</p>
          <Button type="button" variant="outline" onClick={() => setPane("galaxy")} className="min-h-11 min-[52rem]:hidden">Open notebooks</Button>
        </div>
      )}
    </WorkspaceShell>
  )
}
