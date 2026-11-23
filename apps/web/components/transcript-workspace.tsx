"use client"

import { Fragment, useEffect, useRef, useState, type FormEvent } from "react"
import { ArrowUpIcon } from "@phosphor-icons/react/dist/csr/ArrowUp"
import { PlayIcon } from "@phosphor-icons/react/dist/csr/Play"
import { PlusIcon } from "@phosphor-icons/react/dist/csr/Plus"
import { VideoCameraIcon } from "@phosphor-icons/react/dist/csr/VideoCamera"

import { IngestionProgress } from "@/components/ingestion-progress"
import { ProviderRun } from "@/components/provider-run"
import { ProviderSelectionFields, useProviderSelection } from "@/components/provider-selection"
import { MomentPlayer } from "@/components/moment-player"
import { StudyWorkspace } from "@/components/study-workspace"
import { TranscriptionEvidence } from "@/components/transcription-evidence"
import { RetrievalEvidence } from "@/components/retrieval-evidence"
import { Button } from "@/components/ui/button"
import { Disclosure } from "@/components/ui/disclosure"
import { WorkspaceShell, type WorkspacePane } from "@/components/workspace-shell"
import {
  ApiClientError,
  askVideoQuestion,
  cancelIngestionJob,
  getIngestionEvents,
  getIngestionJob,
  getTranscript,
  importVideo,
  retryIngestionJob,
  waitForIngestionJob,
  searchVideo,
} from "@/lib/api/client"
import type {
  AnswerResponse,
  IngestionEventsResponse,
  IngestionJobResponse,
  TranscriptResponse,
  SearchResponse,
  RetrievalMode,
} from "@/lib/api/contracts"
import { getImportConsent } from "@/lib/api/providers"
import { timestamp } from "@/lib/timestamp"

function errorMessage(error: unknown): string {
  if (error instanceof ApiClientError) {
    const action = error.response?.error.suggested_action
    const trace = error.response?.error.details?.trace_id
    const message = action ? `${error.message} ${action}` : error.message
    return typeof trace === "string" ? `${message} Retrieval trace: ${trace}` : message
  }
  return "The request could not be completed. Please try again."
}

function rememberVideo(videoId: string | null) {
  const url = new URL(window.location.href)
  if (videoId === null) url.searchParams.delete("video")
  else url.searchParams.set("video", videoId)
  window.history.replaceState(window.history.state, "", url)
}

function youtubeSourceId(value: string): string {
  const url = new URL(value)
  return url.searchParams.get("v") ?? url.pathname.split("/").filter(Boolean).at(-1) ?? ""
}

function rememberJob(jobId: string | null) {
  const url = new URL(window.location.href)
  if (jobId) url.searchParams.set("job", jobId)
  else url.searchParams.delete("job")
  window.history.replaceState(window.history.state, "", url)
}

export function TranscriptWorkspace() {
  const previewTitle = useRef<HTMLHeadingElement>(null)
  const importField = useRef<HTMLInputElement>(null)
  const [pane, setPane] = useState<WorkspacePane>("work")
  const [seekRequest, setSeekRequest] = useState<{ milliseconds: number } | null>(null)
  const [sourceUrl, setSourceUrl] = useState("")
  const [question, setQuestion] = useState("")
  const [transcript, setTranscript] = useState<TranscriptResponse | null>(null)
  const [answer, setAnswer] = useState<AnswerResponse | null>(null)
  const [retrieval, setRetrieval] = useState<SearchResponse | null>(null)
  const [failedQuery, setFailedQuery] = useState<{ operation: "search" | "question"; traceId: string } | null>(null)
  const [retrievalMode, setRetrievalMode] = useState<RetrievalMode>("reranked")
  const [evidenceOnly, setEvidenceOnly] = useState(false)
  const [job, setJob] = useState<IngestionJobResponse | null>(null)
  const [events, setEvents] = useState<IngestionEventsResponse["events"]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sourceError, setSourceError] = useState<string | null>(null)
  const [importing, setImporting] = useState(false)
  const [jobActionPending, setJobActionPending] = useState(false)
  const [asking, setAsking] = useState(false)
  const importPolicy = useProviderSelection("import")
  const questionPolicy = useProviderSelection("question")
  const [storedJobId, setStoredJobId] = useState<string | null>(null)
  const setImportGrant = importPolicy.setGrantId

  const restoreRequest = useRef<AbortController | null>(null)
  const [resultMode, setResultMode] = useState<"search" | "question">("question")

  useEffect(() => {
    const params = new URL(window.location.href).searchParams
    const id = params.get("video")
    const jobId = params.get("job")
    const abort = new AbortController()
    restoreRequest.current = abort
    if (jobId && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(jobId)) {
      void Promise.all([getIngestionJob(jobId), getIngestionEvents(jobId), getImportConsent(jobId)])
        .then(([restored, history, consent]) => {
          if (abort.signal.aborted) return
          setJob(restored)
          setEvents(history.events)
          setSourceUrl(restored.canonical_url)
          if (consent && !consent.revoked_at) setImportGrant(consent.consent_id)
        }).catch(() => {})
    }
    if (!id || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(id)) return () => abort.abort()
    void getTranscript(id, (input, init) => fetch(input, { ...init, signal: abort.signal }))
      .then((loaded) => {
        if (abort.signal.aborted) return
        setTranscript(loaded)
        setSourceUrl(loaded.video.canonical_url)
        setStoredJobId(params.get("job"))
      })
      .catch((caught: unknown) => {
        if (!abort.signal.aborted) setError(errorMessage(caught))
      })
    return () => abort.abort()
  }, [setImportGrant])

  function seekTo(milliseconds: number) {
    setPane("sources")
    setSeekRequest({ milliseconds })
  }

  useEffect(() => {
    if (!seekRequest) return
    const target = previewTitle.current
    target?.focus({ preventScroll: true })
    target?.scrollIntoView({ block: "nearest" })
  }, [seekRequest])

  async function refreshJobProgress(nextJob: IngestionJobResponse) {
    setJob(nextJob)
    const history = await getIngestionEvents(nextJob.job_id)
    setEvents(history.events)
  }

  async function monitorIngestion(
    initialJob: IngestionJobResponse,
    reused: boolean,
  ) {
    const completed = await waitForIngestionJob(initialJob, fetch, {
      maxPolls: 14_400,
      onUpdate: refreshJobProgress,
    })
    if (completed.status === "failed") {
      setSourceError(completed.last_error_message ?? "The ingestion job could not continue.")
      return
    }
    if (completed.status === "cancelled") {
      setNotice("Ingestion cancelled safely. Persisted checkpoints were retained according to policy.")
      return
    }
    if (completed.status !== "succeeded" || completed.video_id == null) {
      throw new ApiClientError("The ingestion job did not complete.", 409, null)
    }

    const loaded = await getTranscript(completed.video_id)
    rememberVideo(completed.video_id)
    setAnswer(null)
    setRetrieval(null)
    setTranscript(loaded)
    if (loaded.transcription !== null) {
      setNotice(
        "ASR transcript ready. See Provider decisions for the model and device used.",
      )
    } else {
      setNotice(
        reused
          ? "Existing durable job reused — no transcript rows were duplicated."
          : "Caption transcript imported and indexed.",
      )
    }
  }

  async function handleImport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    restoreRequest.current?.abort()
    rememberVideo(null)
    rememberJob(null)
    setStoredJobId(null)
    setImporting(true)
    setError(null)
    setSourceError(null)
    setNotice(null)
    setAnswer(null)
    setRetrieval(null)
    setTranscript(null)
    setSeekRequest(null)
    setJob(null)
    setEvents([])
    try {
      const selected = await importPolicy.selection(youtubeSourceId(sourceUrl))
      const imported = await importVideo(sourceUrl, fetch, selected)
      rememberJob(imported.job.job_id)
      if (imported.reused && selected?.consent_id) {
        const canonical = await getImportConsent(imported.job.job_id).catch(() => null)
        importPolicy.setGrantId(canonical && !canonical.revoked_at ? canonical.consent_id : null)
      }
      setNotice(
        imported.reused
          ? "Following the existing durable job and its persisted event trail."
          : "Durable job queued. Waiting for a worker to claim it.",
      )
      await monitorIngestion(imported.job, imported.reused)
    } catch (caught) {
      setSourceError(errorMessage(caught))
    } finally {
      setImporting(false)
    }
  }

  async function handleCancel() {
    if (job === null) return
    setJobActionPending(true)
    setSourceError(null)
    try {
      const updated = await cancelIngestionJob(job.job_id)
      await refreshJobProgress(updated)
      setNotice(
        updated.status === "cancelled"
          ? "The queued job was cancelled."
          : "Cancellation requested. The worker will stop at a safe checkpoint.",
      )
    } catch (caught) {
      setSourceError(errorMessage(caught))
    } finally {
      setJobActionPending(false)
    }
  }

  async function handleRetry() {
    if (job === null) return
    setJobActionPending(true)
    setImporting(true)
    setSourceError(null)
    setNotice(null)
    try {
      const selected = await importPolicy.selection(job.external_id) ?? {
        profile: { profile_id: "local", version: 1 },
      }
      const retried = await retryIngestionJob(job.job_id, fetch, selected)
      setJobActionPending(false)
      setNotice(`Retry queued from the ${retried.stage.replaceAll("_", " ")} checkpoint.`)
      await monitorIngestion(retried, true)
    } catch (caught) {
      setSourceError(errorMessage(caught))
    } finally {
      setJobActionPending(false)
      setImporting(false)
    }
  }

  async function handleQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    await submitQuery()
  }

  function clearPendingAnchor() {
    if (retrieval?.status === "anchor_selection_required" || retrieval?.status === "anchor_unresolved") {
      setRetrieval(null)
    }
  }

  async function submitQuery(selectedAnchor?: string) {
    if (transcript === null || !question.trim()) return
    setAsking(true)
    setError(null)
    setAnswer(null)
    setRetrieval(null)
    setFailedQuery(null)
    setResultMode(evidenceOnly ? "search" : "question")
    try {
      const options = { mode: retrievalMode, selected_anchor: selectedAnchor, limit: 8, allow_fallback: true, allow_external_frames: false }
      if (evidenceOnly) {
        const profile = questionPolicy.profile()
        setRetrieval(await searchVideo(transcript.video.video_id, {
          ...options, query: question,
          provider_selection: profile ? { profile } : undefined,
        }))
      } else {
        const selected = await questionPolicy.selection(transcript.video.video_id)
        const response = await askVideoQuestion(transcript.video.video_id, question, fetch, {
          ...options, provider_selection: selected,
        })
        setRetrieval(response.retrieval ?? null)
        setAnswer(response.retrieval_status === "anchor_selection_required" || response.retrieval_status === "anchor_unresolved" ? null : response)
      }
    } catch (caught) {
      const traceId = caught instanceof ApiClientError ? caught.response?.error.details?.trace_id : null
      if (typeof traceId === "string") {
        setFailedQuery({ operation: evidenceOnly ? "search" : "question", traceId })
      }
      setError(errorMessage(caught))
    } finally {
      setAsking(false)
    }
  }

  const video = transcript?.video
  const originalVideo = video ? new URL(video.canonical_url) : null
  if (seekRequest) originalVideo?.searchParams.set("t", String(Math.floor(seekRequest.milliseconds / 1000)))

  const importForm = (
    <div className="space-y-3">
      <form onSubmit={handleImport} className="space-y-2">
        <label htmlFor="source-url" className="block text-sm font-medium">YouTube link</label>
        <input
          ref={importField}
          id="source-url"
          type="url"
          required
          value={sourceUrl}
          onChange={(event) => setSourceUrl(event.target.value)}
          placeholder="https://youtu.be/…"
          className="min-h-11 w-full rounded-md border border-input bg-card px-3 text-sm focus-visible:outline-2 focus-visible:outline-ring"
        />
        <Button type="submit" disabled={importing || asking} className="min-h-11 w-full">
          <PlusIcon aria-hidden="true" /> {importing ? "Adding video…" : "Add video"}
        </Button>
      </form>
      <Disclosure label="Import settings">
        <ProviderSelectionFields control={importPolicy} busy={importing} />
      </Disclosure>
    </div>
  )

  return (
    <WorkspaceShell
      title="Local workspace"
      pane={pane}
      onPaneChange={setPane}
      galaxy={
        <div className="space-y-2">
          <h2 className="px-2 text-sm font-semibold">Notebooks</h2>
          <Button variant="secondary" className="min-h-11 w-full justify-start px-2" onClick={() => setPane("work")} aria-current="page">Local workspace</Button>
        </div>
      }
      sources={
        <div className="space-y-3">
          <h2 ref={previewTitle} tabIndex={-1} className="rounded text-base font-semibold">{video ? "Source preview" : "Sources"}</h2>
          {video && transcript ? (
            <Fragment key={video.video_id}>
              <div>
                <p className="font-semibold leading-snug">{video.title}</p>
                <p className="mt-1 text-sm text-muted-foreground">{video.channel_name ?? "YouTube"} · {timestamp(video.duration_ms)}</p>
              </div>
              {seekRequest ? <p role="status" className="text-sm text-primary">Cited moment · {timestamp(seekRequest.milliseconds)}</p> : null}
              <MomentPlayer video={video} seekRequest={seekRequest} />
              {originalVideo ? <a href={originalVideo.href} target="_blank" rel="noreferrer" className="inline-flex min-h-9 items-center text-sm text-primary underline underline-offset-4">Open {seekRequest ? "cited moment" : "video"} on YouTube</a> : null}
              <Disclosure label="Full transcript" details={`${transcript.cues.length} cues`} keepMounted={false}>
                <div className="max-h-96 divide-y overflow-y-auto">
                  {transcript.cues.map((cue) => (
                    <button key={cue.cue_id} type="button" onClick={() => seekTo(cue.start_ms)} className="grid min-h-11 w-full grid-cols-[3.25rem_minmax(0,1fr)] gap-2 rounded px-2 py-2.5 text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring">
                      <span className="font-mono text-xs font-semibold text-primary">{timestamp(cue.start_ms)}</span>
                      <span className="text-sm leading-6">
                        {cue.text}
                        <span className="mt-1 block text-xs text-muted-foreground">
                          {timestamp(cue.start_ms)}–{timestamp(cue.end_ms)} · {cue.origin === "asr"
                            ? `ASR${cue.confidence === null ? "" : ` · ${Math.round(cue.confidence * 100)}% confidence`}`
                            : `${cue.caption_kind ?? "Source"} caption`}
                        </span>
                      </span>
                    </button>
                  ))}
                </div>
              </Disclosure>
              {transcript.transcription ? <Disclosure label="Transcription details"><TranscriptionEvidence transcription={transcript.transcription} /></Disclosure> : null}
              <Disclosure label="Replace video">{importForm}</Disclosure>
            </Fragment>
          ) : importForm}
          <div aria-live="polite" className="space-y-1 text-sm">
            {notice ? <p className="text-muted-foreground">{notice}</p> : null}
            {sourceError ? <p role="alert" className="text-destructive">{sourceError}</p> : null}
          </div>
          {job ? (
            <IngestionProgress job={job} events={events} actionPending={jobActionPending} onCancel={handleCancel} onRetry={handleRetry} />
          ) : null}
          {job || storedJobId ? <ProviderRun operation="import" runId={job?.job_id ?? storedJobId ?? ""} refresh={events.length} /> : null}
        </div>
      }
      lab={video ? <StudyWorkspace key={video.video_id} videoId={video.video_id} onSeek={seekTo} /> : undefined}
    >
      {!video ? (
        <div className="max-w-[50ch] py-5 sm:py-10">
          <span className="mb-5 inline-grid size-12 place-items-center rounded-lg bg-secondary text-primary"><VideoCameraIcon aria-hidden="true" className="size-6" /></span>
          <h1 className="text-3xl leading-tight font-semibold tracking-tight sm:text-4xl">Start with a video</h1>
          <p className="mt-3 text-base leading-relaxed text-muted-foreground">Add a YouTube video to ask questions and check answers at the moments they came from.</p>
          <Button type="button" onClick={() => {
            setPane("sources")
            requestAnimationFrame(() => importField.current?.focus())
          }} className="workspace-empty-import mt-6 min-h-11 gap-2 px-4"><PlusIcon aria-hidden="true" /> Add a video</Button>
          {importing ? <p role="status" className="mt-4 text-sm text-muted-foreground">Preparing your video. See Sources for progress.</p> : null}
          {error ? <p role="alert" className="mt-4 text-sm text-destructive">{error}</p> : null}
        </div>
      ) : (
        <div className="space-y-6">
          <div>
            <h1 className="text-3xl leading-tight font-semibold tracking-tight">Ask about this video</h1>
            <Button variant="ghost" onClick={() => setPane("sources")} className="mt-1.5 min-h-11 max-w-full justify-start px-1 text-muted-foreground">
              <VideoCameraIcon aria-hidden="true" /><span className="truncate">{video.title}</span>
            </Button>
          </div>
          <div className="overflow-hidden rounded-xl border border-input bg-card focus-within:border-ring">
            <form onSubmit={handleQuestion}>
              <label htmlFor="question" className="sr-only">Question about this video</label>
              <textarea
                id="question"
                required
                rows={3}
                disabled={asking}
                maxLength={2000}
                value={question}
                onChange={(event) => { setQuestion(event.target.value); clearPendingAnchor() }}
                placeholder="What does the speaker say about…?"
                className="block min-h-24 w-full resize-y bg-transparent px-4 pt-3 text-base leading-relaxed focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
              />
              <div className="flex flex-wrap items-center justify-between gap-2 p-3">
                <Button variant={evidenceOnly ? "secondary" : "ghost"} type="button" disabled={asking} aria-pressed={evidenceOnly} className="min-h-11" onClick={() => { setEvidenceOnly(!evidenceOnly); clearPendingAnchor() }}>
                  Evidence only
                </Button>
                <Button type="submit" disabled={asking || !question.trim()} className="min-h-11 gap-2 px-4">
                  {asking ? "Checking…" : evidenceOnly ? "Find evidence" : "Ask"} <ArrowUpIcon aria-hidden="true" />
                </Button>
              </div>
            </form>
            <Disclosure label="Question settings" details={evidenceOnly ? "Search only" : undefined}>
              <div className="space-y-3 px-3">
                <ProviderSelectionFields control={questionPolicy} busy={asking} evidenceOnly={evidenceOnly} />
                <label className="flex flex-wrap items-center gap-2 text-sm">Retrieval method
                  <select value={retrievalMode} disabled={asking} onChange={event => { setRetrievalMode(event.target.value as RetrievalMode); clearPendingAnchor() }} className="min-h-10 rounded-md border bg-background p-2 text-sm">
                    <option value="reranked">Hybrid + reranking</option>
                    <option value="hybrid">Hybrid</option>
                    <option value="lexical">Exact terms</option>
                    <option value="dense">Semantic</option>
                  </select>
                </label>
              </div>
            </Disclosure>
          </div>
          {asking ? <p role="status" className="text-sm text-muted-foreground">Checking the video for relevant evidence…</p> : null}
          {error ? <p role="alert" className="rounded-lg border border-destructive/30 p-3 text-sm text-destructive">{error}</p> : null}

          {answer ? (
            <article aria-label="Answer" className="space-y-4 border-t pt-5">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="text-xl font-semibold">Answer</h2>
                <span className="text-sm text-muted-foreground">{answer.confidence} confidence</span>
              </div>
              <p className="max-w-[72ch] whitespace-pre-line text-base leading-[1.65]">{answer.answer}</p>
              {answer.warnings.map((warning) => <p key={warning} className="text-sm text-muted-foreground">{warning}</p>)}
              {answer.evidence.length > 0 || Boolean(answer.visual_evidence?.length) ? (
                <div>
                  <h3 className="mb-1 text-sm font-semibold">Citations</h3>
                  <ul className="divide-y">
                    {answer.evidence.map((item) => (
                      <li key={item.retrieval_unit_id} className="flex flex-wrap items-baseline gap-x-3 py-2">
                        <Button variant="secondary" type="button" className="min-h-11 font-mono" onClick={() => seekTo(item.start_ms)}>
                          <PlayIcon aria-hidden="true" /> {timestamp(item.start_ms)}–{timestamp(item.end_ms)}
                        </Button>
                        <p className="min-w-0 flex-1 basis-48 text-sm leading-6 text-muted-foreground">“{item.quote}”</p>
                      </li>
                    ))}
                    {answer.visual_evidence?.map(frame => (
                      <li key={frame.frame_id} className="py-2">
                        <Button variant="secondary" type="button" className="min-h-11" onClick={() => seekTo(frame.capture_ms ?? frame.start_ms)}>
                          <PlayIcon aria-hidden="true" /> <span className="font-mono">{timestamp(frame.start_ms)}–{timestamp(frame.end_ms)}</span> On-screen evidence
                        </Button>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </article>
          ) : null}
          {retrieval ? <RetrievalEvidence key={retrieval.trace_id} result={retrieval} pending={asking} onSelectAnchor={submitQuery} onSeek={seekTo} showMatches={resultMode === "search"} /> : null}
          {retrieval ? <ProviderRun operation={resultMode} runId={retrieval.trace_id} /> : null}
          {failedQuery ? <ProviderRun operation={failedQuery.operation} runId={failedQuery.traceId} /> : null}
        </div>
      )}
    </WorkspaceShell>
  )
}
