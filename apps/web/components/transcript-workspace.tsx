"use client"

import { useRef, useState, type FormEvent } from "react"

import {
  ApiClientError,
  askVideoQuestion,
  getTranscript,
  importVideo,
} from "@/lib/api/client"
import type { AnswerResponse, TranscriptResponse } from "@/lib/api/contracts"
import { seekCommands, YOUTUBE_PLAYER_ORIGIN } from "@/lib/youtube-player"

function timestamp(milliseconds: number): string {
  const totalSeconds = Math.floor(milliseconds / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  const clock = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
  return hours > 0 ? `${hours}:${clock}` : clock
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiClientError) {
    const action = error.response?.error.suggested_action
    return action ? `${error.message} ${action}` : error.message
  }
  return "The request could not be completed. Please try again."
}

export function TranscriptWorkspace() {
  const player = useRef<HTMLIFrameElement>(null)
  const [sourceUrl, setSourceUrl] = useState("")
  const [question, setQuestion] = useState("")
  const [transcript, setTranscript] = useState<TranscriptResponse | null>(null)
  const [answer, setAnswer] = useState<AnswerResponse | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [importing, setImporting] = useState(false)
  const [asking, setAsking] = useState(false)

  function seekTo(milliseconds: number) {
    for (const command of seekCommands(milliseconds)) {
      player.current?.contentWindow?.postMessage(command, YOUTUBE_PLAYER_ORIGIN)
    }
    player.current?.scrollIntoView({ behavior: "smooth", block: "center" })
  }

  async function handleImport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setImporting(true)
    setError(null)
    setNotice(null)
    setAnswer(null)
    try {
      const imported = await importVideo(sourceUrl)
      const loaded = await getTranscript(imported.video.video_id)
      setTranscript(loaded)
      setNotice(
        imported.reused
          ? "Existing import reused — no transcript rows were duplicated."
          : "Caption transcript imported and indexed.",
      )
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setImporting(false)
    }
  }

  async function handleQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (transcript === null) return
    setAsking(true)
    setError(null)
    setAnswer(null)
    try {
      setAnswer(await askVideoQuestion(transcript.video.video_id, question))
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setAsking(false)
    }
  }

  return (
    <section aria-labelledby="workspace-title" className="mx-auto w-full max-w-7xl px-5 pb-16 sm:px-7 lg:px-10">
      <div className="mb-7 max-w-3xl">
        <p className="mb-3 font-mono text-xs tracking-[0.22em] text-primary uppercase">Phase 1 · Transcript-first</p>
        <h1 id="workspace-title" className="font-heading text-4xl leading-tight font-semibold tracking-[-0.04em] sm:text-5xl">Ask the video. Keep the receipts.</h1>
        <p className="mt-4 max-w-2xl text-sm leading-6 text-muted-foreground sm:text-base">Import a public captioned YouTube video, inspect its exact cues, and seek every answer back to timestamped transcript evidence.</p>
      </div>

      <form onSubmit={handleImport} className="rounded-2xl border bg-card p-3 shadow-sm sm:flex sm:items-center sm:gap-3">
        <label htmlFor="source-url" className="sr-only">Public YouTube URL</label>
        <input id="source-url" type="url" required value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://www.youtube.com/watch?v=…" className="h-11 w-full rounded-xl border bg-background px-4 text-sm outline-none transition focus:border-primary focus:ring-3 focus:ring-primary/15" />
        <button type="submit" disabled={importing} className="mt-3 inline-flex h-11 w-full items-center justify-center rounded-xl bg-primary px-5 text-sm font-semibold text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60 sm:mt-0 sm:w-auto">{importing ? "Importing captions…" : "Import video"}</button>
      </form>

      <div aria-live="polite" className="min-h-12 py-3 text-sm">
        {notice ? <p className="text-chart-4 dark:text-chart-2">{notice}</p> : null}
        {error ? <p role="alert" className="text-destructive">{error}</p> : null}
      </div>

      {transcript === null ? (
        <div className="grid min-h-80 place-items-center rounded-3xl border border-dashed bg-muted/30 px-6 text-center">
          <div className="max-w-md">
            <p className="font-heading text-xl font-semibold">Your evidence workspace is ready.</p>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">Only metadata and captions are retrieved. Galaxy Frog does not download audio or video in Phase 1.</p>
          </div>
        </div>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1.35fr)_minmax(20rem,0.65fr)]">
          <div className="space-y-5">
            <article className="overflow-hidden rounded-3xl border bg-card shadow-sm">
              <div className="aspect-video bg-black">
                <iframe ref={player} title={`YouTube player: ${transcript.video.title}`} src={`https://www.youtube-nocookie.com/embed/${encodeURIComponent(transcript.video.external_id)}?enablejsapi=1&rel=0`} className="size-full" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowFullScreen />
              </div>
              <div className="flex flex-col gap-3 p-5 sm:flex-row sm:items-start sm:justify-between">
                <div>
                  <h2 className="font-heading text-xl font-semibold">{transcript.video.title}</h2>
                  <p className="mt-1 text-sm text-muted-foreground">{transcript.video.channel_name ?? "YouTube"}</p>
                </div>
                <div className="flex gap-2 text-xs">
                  <span className="rounded-full border px-2.5 py-1">Transcript ready</span>
                  <span className="rounded-full border px-2.5 py-1">Index ready</span>
                </div>
              </div>
            </article>

            <article className="rounded-3xl border bg-card p-5 shadow-sm sm:p-6">
              <div className="mb-4">
                <p className="font-mono text-[0.68rem] tracking-[0.18em] text-primary uppercase">Grounded question</p>
                <h2 className="mt-1 font-heading text-xl font-semibold">What do you want to verify?</h2>
              </div>
              <form onSubmit={handleQuestion} className="flex flex-col gap-3 sm:flex-row">
                <label htmlFor="question" className="sr-only">Question about this video</label>
                <input id="question" required maxLength={2000} value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="What does the speaker say about…?" className="h-11 flex-1 rounded-xl border bg-background px-4 text-sm outline-none transition focus:border-primary focus:ring-3 focus:ring-primary/15" />
                <button type="submit" disabled={asking} className="h-11 rounded-xl bg-foreground px-5 text-sm font-semibold text-background disabled:opacity-60">{asking ? "Checking evidence…" : "Ask"}</button>
              </form>

              {answer ? (
                <div className="mt-6 border-t pt-6">
                  <div className="mb-3 flex items-center gap-2 text-xs text-muted-foreground">
                    <span className="rounded-full bg-primary/10 px-2.5 py-1 font-medium text-primary">{answer.confidence} confidence</span>
                    <span>{answer.evidence.length} citation{answer.evidence.length === 1 ? "" : "s"}</span>
                  </div>
                  <p className="text-base leading-7">{answer.answer}</p>
                  {answer.warnings.map((warning) => <p key={warning} className="mt-3 text-sm text-muted-foreground">{warning}</p>)}
                  <div className="mt-5 space-y-3">
                    {answer.evidence.map((item) => (
                      <button key={item.retrieval_unit_id} type="button" onClick={() => seekTo(item.start_ms)} className="block w-full rounded-2xl border border-primary/20 bg-primary/5 p-4 text-left transition hover:border-primary/45 hover:bg-primary/10">
                        <span className="font-mono text-xs font-semibold text-primary">{timestamp(item.start_ms)}–{timestamp(item.end_ms)} · transcript</span>
                        <span className="mt-2 block text-sm leading-6">“{item.quote}”</span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}
            </article>
          </div>

          <aside className="rounded-3xl border bg-card shadow-sm lg:sticky lg:top-6 lg:flex lg:max-h-[calc(100vh-3rem)] lg:flex-col">
            <div className="border-b p-5">
              <p className="font-mono text-[0.68rem] tracking-[0.18em] text-primary uppercase">Source cues</p>
              <div className="mt-1 flex items-end justify-between">
                <h2 className="font-heading text-xl font-semibold">Transcript</h2>
                <span className="text-xs text-muted-foreground">{transcript.cues.length} cues</span>
              </div>
            </div>
            <div className="divide-y overflow-y-auto">
              {transcript.cues.map((cue) => (
                <button key={cue.cue_id} type="button" onClick={() => seekTo(cue.start_ms)} className="grid w-full grid-cols-[3.5rem_1fr] gap-3 p-4 text-left transition hover:bg-muted/60">
                  <span className="font-mono text-xs font-semibold text-primary">{timestamp(cue.start_ms)}</span>
                  <span className="text-sm leading-6">{cue.text}</span>
                </button>
              ))}
            </div>
          </aside>
        </div>
      )}
    </section>
  )
}
