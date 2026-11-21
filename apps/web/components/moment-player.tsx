"use client"

import { useEffect, useRef, useState } from "react"

import { ApiClientError, explainVideoMoment } from "@/lib/api/client"
import { ProviderRun } from "@/components/provider-run"
import { ProviderSelectionFields, useProviderSelection } from "@/components/provider-selection"
import { Button } from "@/components/ui/button"
import { Disclosure } from "@/components/ui/disclosure"
import type { ExplainMomentResponse, VideoResponse } from "@/lib/api/contracts"
import { timestamp } from "@/lib/timestamp"
import { loadYouTubeApi, pausedPlayheadMs, seekToMs, YOUTUBE_PLAYER_ORIGIN, type YouTubePlayer } from "@/lib/youtube-player"

const citationLabels = { transcript: "Transcript", ocr: "On-screen text", frame: "Frame" }

export function MomentPlayer({ video, seekRequest }: {
  video: VideoResponse
  seekRequest: { milliseconds: number } | null
}) {
  const container = useRef<HTMLDivElement>(null)
  const player = useRef<YouTubePlayer | null>(null)
  const pending = useRef(false)
  const [status, setStatus] = useState<"loading" | "ready" | "unavailable">("loading")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [failedTrace, setFailedTrace] = useState<string | null>(null)
  const [result, setResult] = useState<ExplainMomentResponse | null>(null)
  const policy = useProviderSelection("moment")
  const { external_id: externalId, title } = video

  useEffect(() => {
    const wrapper = container.current
    if (!wrapper) return
    let disposed = false
    let instance: YouTubePlayer | undefined
    let readyTimeout: number | undefined
    const iframe = document.createElement("iframe")
    const url = new URL(`/embed/${encodeURIComponent(externalId)}`, YOUTUBE_PLAYER_ORIGIN)
    url.search = new URLSearchParams({ enablejsapi: "1", rel: "0", origin: window.location.origin }).toString()
    iframe.title = `YouTube player: ${title}`
    iframe.src = url.toString()
    iframe.className = "size-full"
    iframe.allow = "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
    iframe.allowFullscreen = true
    wrapper.append(iframe)

    loadYouTubeApi().then(api => {
      if (disposed) return
      readyTimeout = window.setTimeout(() => setStatus("unavailable"), 12_000)
      instance = new api.Player(iframe, {
        events: { onReady: ({ target }) => {
          if (disposed) return
          window.clearTimeout(readyTimeout)
          player.current = target
          setStatus("ready")
        } },
      })
    }).catch(() => {
      if (!disposed) setStatus("unavailable")
    })

    return () => {
      disposed = true
      window.clearTimeout(readyTimeout)
      instance?.destroy()
      iframe.remove()
      player.current = null
    }
  }, [externalId, title])

  useEffect(() => {
    if (seekRequest && status === "ready" && player.current) {
      seekToMs(player.current, seekRequest.milliseconds)
    }
  }, [seekRequest, status])

  async function explain() {
    if (!player.current || pending.current) return
    setError(null)
    setFailedTrace(null)
    setResult(null)
    try {
      const timestampMs = pausedPlayheadMs(player.current, video.duration_ms)
      pending.current = true
      setBusy(true)
      const selected = await policy.selection(video.video_id)
      const response = await explainVideoMoment(video.video_id, {
        timestamp_ms: timestampMs,
        allow_external_frames: false,
        provider_selection: selected,
      })
      if (response.video_id !== video.video_id || response.timestamp_ms !== timestampMs) {
        throw new Error("The response did not match the requested video and playhead.")
      }
      setResult(response)
    } catch (caught) {
      const action = caught instanceof ApiClientError ? caught.response?.error.suggested_action : null
      const trace = caught instanceof ApiClientError ? caught.response?.error.details?.trace_id : null
      if (typeof trace === "string") setFailedTrace(trace)
      const message = caught instanceof Error ? caught.message : "The moment could not be explained."
      setError(action ? `${message} ${action}` : message)
    } finally {
      pending.current = false
      setBusy(false)
    }
  }

  function seek(milliseconds: number) {
    if (player.current) seekToMs(player.current, milliseconds)
  }

  return (
    <>
      <div ref={container} className="mb-3 aspect-video overflow-hidden rounded-lg bg-black" />
      <Disclosure label="Explain this moment">
        <div className="space-y-3 px-2 pt-2">
          <Button type="button" onClick={explain} disabled={status !== "ready" || busy} className="min-h-11">
            {busy ? "Examining this moment…" : "Explain this moment"}
          </Button>
          <ProviderSelectionFields control={policy} busy={busy} />
          {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
          <div aria-live="polite" aria-busy={busy}>
            {result ? <MomentEvidence result={result} onSeek={seek} /> : null}
            {result || failedTrace ? <ProviderRun operation="moment" runId={result?.trace_id ?? failedTrace ?? ""} /> : null}
          </div>
        </div>
      </Disclosure>
    </>
  )
}

export function MomentEvidence({ result, onSeek }: {
  result: ExplainMomentResponse
  onSeek: (milliseconds: number) => void
}) {
  return (
    <section aria-label="Moment explanation" className="space-y-3 rounded-xl border bg-background p-4 text-sm">
      <h3 className="font-heading text-lg font-semibold">Moment at {timestamp(result.timestamp_ms)}</h3>
      <p className="text-muted-foreground">Examined {timestamp(result.window.start_ms)}–{timestamp(result.window.end_ms)}</p>
      <p className="whitespace-pre-wrap wrap-break-word">{result.explanation}</p>
      {result.warnings.map(warning => <p key={warning.code} className="text-muted-foreground">{warning.message}</p>)}
      {result.citations.length > 0 ? (
        <ul aria-label="Moment citations" className="space-y-2">
          {result.citations.map(item => (
            <li key={`${item.modality}-${item.source_id}`}>
              <button type="button" onClick={() => onSeek(item.seek_ms)} className="w-full rounded-lg border px-3 py-2 text-left hover:bg-muted">
                <span className="font-semibold">{citationLabels[item.modality]} · {timestamp(item.seek_ms)}</span>
                {item.quote ? <span className="block wrap-break-word">{item.quote}</span> : null}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {result.current_frame || result.related_moments.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-muted-foreground">Sampled frames</span>
          {[...result.related_moments, ...(result.current_frame ? [result.current_frame] : [])]
            .sort((left, right) => left.capture_ms - right.capture_ms)
            .map(frame => <button key={frame.frame_id} type="button" onClick={() => onSeek(frame.capture_ms)} className="rounded-full border px-2.5 py-1 hover:bg-muted">{timestamp(frame.capture_ms)}</button>)}
        </div>
      ) : null}
      <p className="wrap-break-word font-mono text-xs text-muted-foreground">Trace {result.trace_id}</p>
    </section>
  )
}
