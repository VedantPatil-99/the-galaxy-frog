"use client"

import { useState } from "react"

import { Button } from "@/components/ui/button"
import { ApiClientError, getRetrievalTrace } from "@/lib/api/client"
import type { RetrievalTraceResponse, SearchResponse } from "@/lib/api/contracts"
import { timestamp } from "@/lib/timestamp"

interface Props {
  result: SearchResponse
  pending: boolean
  onSelectAnchor: (anchorId: string) => void
  onSeek: (milliseconds: number) => void
}

export function RetrievalEvidence({ result, pending, onSelectAnchor, onSeek }: Props) {
  const [trace, setTrace] = useState<RetrievalTraceResponse | null>(null)
  const [loadingTrace, setLoadingTrace] = useState(false)
  const [traceError, setTraceError] = useState<string | null>(null)

  async function loadTrace() {
    setLoadingTrace(true)
    setTraceError(null)
    try {
      setTrace(await getRetrievalTrace(result.video_id, result.trace_id))
    } catch (error) {
      setTraceError(error instanceof ApiClientError ? error.message : "The retrieval trace could not be loaded.")
    } finally {
      setLoadingTrace(false)
    }
  }

  const keptFrames = (result.visual_frames ?? []).filter((frame) => !frame.is_duplicate)

  return (
    <section aria-label="Retrieved evidence" className="mt-6 space-y-4 border-t pt-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-heading text-lg font-semibold">Evidence for your query</h3>
        <span className="rounded-full border px-2.5 py-1 text-xs">{result.degraded ? "Degraded retrieval" : result.status === "anchor_selection_required" ? "Choose a moment" : result.status === "anchor_unresolved" ? "Event unresolved" : "Retrieval complete"}</span>
      </div>
      <p className="break-words text-sm text-muted-foreground">{result.analysis.original}</p>
      {result.warnings.length > 0 ? <div role="status" className="space-y-2 rounded-xl border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
        {result.warnings.map((warning, index) => <p key={`${warning.code}-${index}`}>{warning.message}</p>)}
      </div> : null}

      {result.status === "anchor_selection_required" ? <div className="space-y-3 rounded-2xl border p-4">
        <h4 className="font-semibold">Choose the event you mean</h4>
        <p className="text-sm text-muted-foreground">These are possible matching moments. Choose one to continue searching around it.</p>
        {result.anchors.map(anchor => <div key={anchor.anchor_id} className="rounded-xl bg-muted/40 p-3">
          <p className="font-mono text-xs text-primary">{timestamp(anchor.start_ms)}–{timestamp(anchor.end_ms)} · {anchor.match_kind === "semantic" ? "Possible match" : "Transcript match"}</p>
          <p className="my-2 text-sm leading-6">{anchor.text}</p>
          <div className="flex flex-wrap gap-2">
            <Button type="button" disabled={pending} onClick={() => onSelectAnchor(anchor.anchor_id)}>Use {timestamp(anchor.start_ms)}</Button>
            <Button type="button" variant="outline" onClick={() => onSeek(anchor.start_ms)}>Preview moment</Button>
          </div>
        </div>)}
      </div> : null}
      {result.status === "anchor_unresolved" ? <p role="status" className="rounded-xl border p-4 text-sm">No matching event was found. Try a phrase spoken in the video or an explicit timestamp such as “before 02:30”.</p> : null}
      {result.status === "resolved" && result.evidence.length === 0 && keptFrames.length === 0 && !result.ocr_evidence?.length ? <p className="text-sm text-muted-foreground">No transcript evidence matched this query and time range.</p> : null}

      {result.visual_decisions?.length ? <div className="rounded-xl border p-3 text-sm space-y-2" aria-label="Visual processing decisions">
        {result.visual_decisions.map((decision, index) => <p key={index}>{decision.decision.replaceAll("_", " ")} · {decision.reason.replaceAll("_", " ")}</p>)}
        {result.visual_reasoning ? <p className="text-xs text-muted-foreground">Reasoning: {result.visual_reasoning.provider?.model ?? "unavailable"} · {result.visual_reasoning.provider?.location ?? "disabled"} · {result.visual_reasoning.cache_hit ? "cached result" : `${Math.round(result.visual_reasoning.elapsed_ms)} ms`} · {result.visual_reasoning.examined_frame_ids.length} frame examined</p> : null}
      </div> : null}

      {result.ocr_evidence?.length ? <div aria-label="OCR evidence" className="space-y-3">
        <h4 className="text-sm font-semibold">OCR evidence</h4>
        {result.ocr_evidence.map(block => <article key={block.block_id} className="rounded-xl border p-3 space-y-2">
          <p className="whitespace-pre-wrap text-sm">{block.text}</p>
          <p className="text-xs text-muted-foreground">{block.provider.provider} · {block.provider.provider_revision}{block.confidence != null ? ` · OCR confidence ${Math.round(block.confidence * 100)}% (${block.confidence_method})` : ""}</p>
          <Button type="button" variant="outline" size="sm" onClick={() => onSeek(block.capture_ms ?? block.start_ms)}>{timestamp(block.start_ms)}–{timestamp(block.end_ms)}{block.capture_ms != null ? ` · Captured ${timestamp(block.capture_ms)}` : ""}</Button>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img className="max-h-48 rounded-lg" loading="lazy" alt={`OCR source frame ${block.sequence}`} src={`/api/proxy/v1/videos/${block.video_id}/frames/${block.sequence}/image`} />
          <p className="break-all font-mono text-[0.65rem]">Block {block.block_id} · Frame {block.frame_id}</p>
        </article>)}
      </div> : null}

      {keptFrames.length > 0 ? (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-semibold">Visual frame evidence</h4>
            <span className="rounded-full border px-2 py-0.5 text-xs text-muted-foreground">
              {keptFrames.length} {keptFrames.length === 1 ? "frame" : "frames"}
            </span>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {keptFrames.map((frame) => {
              const src = frame.image_url.startsWith("/api/proxy")
                ? frame.image_url
                : `/api/proxy${frame.image_url}`
              return (
                <div
                  key={frame.frame_id}
                  className="overflow-hidden rounded-xl border bg-card text-card-foreground shadow-sm"
                >
                  <div className="relative aspect-video w-full bg-muted">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={src}
                      alt={`Visual frame at ${timestamp(frame.capture_ms ?? frame.start_ms)}`}
                      className="h-full w-full object-cover"
                      loading="lazy"
                    />
                    <div className="absolute bottom-2 left-2 rounded-md bg-black/75 px-2 py-0.5 font-mono text-xs text-white">
                      {timestamp(frame.start_ms)}–{timestamp(frame.end_ms)}
                    </div>
                  </div>
                  <div className="p-3 space-y-2">
                    <p className="text-xs font-medium">{frame.modality === "visual" ? "Visual similarity" : "OCR frame"}{frame.capture_ms != null ? ` · Captured ${timestamp(frame.capture_ms)}` : " · Capture time unknown"}</p>
                    {frame.collection ? <p className="text-xs text-muted-foreground">{frame.collection.model} · {frame.collection.provider}{frame.rank != null ? ` · Rank ${frame.rank}` : ""}</p> : null}
                    {frame.ocr_text ? (
                      <div>
                        <p className="line-clamp-3 text-xs leading-5 text-muted-foreground whitespace-pre-wrap">
                          {frame.ocr_text}
                        </p>
                        {frame.confidence != null ? (
                          <p className="mt-1 text-[0.65rem] text-muted-foreground font-mono">
                            OCR confidence: {(frame.confidence * 100).toFixed(0)}%
                          </p>
                        ) : null}
                      </div>
                    ) : (
                      <p className="text-xs italic text-muted-foreground">No extracted text</p>
                    )}
                    <div className="pt-1">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="w-full text-xs"
                        onClick={() => onSeek(frame.capture_ms ?? frame.start_ms)}
                      >
                        Jump to {timestamp(frame.capture_ms ?? frame.start_ms)}
                      </Button>
                    </div>
                    <details className="text-xs break-all"><summary className="cursor-pointer">Frame provenance</summary><p>Frame {frame.frame_id} · Scene {frame.scene_id ?? "unknown"}</p><p>Source {frame.source ?? "unknown"} · pHash {frame.perceptual_hash ?? "unknown"}</p><p>SHA-256 {frame.content_sha256 ?? "unknown"}</p>{frame.collection ? <p>Collection {frame.collection_id} · Revision {frame.collection.revision} · {frame.collection.dimension} dimensions · {frame.collection.normalization}</p> : null}</details>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      ) : null}

      {result.evidence.map((group, index) => <article key={`${group.start_ms}-${group.end_ms}`} className="rounded-2xl border p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h4 className="text-sm font-semibold">Transcript evidence {index + 1}</h4>
          <Button type="button" variant="outline" size="sm" onClick={() => onSeek(group.start_ms)}>{timestamp(group.start_ms)}–{timestamp(group.end_ms)}</Button>
        </div>
        <p className="mb-3 text-xs text-muted-foreground">Expanded display window. Original cue and citation intervals are preserved below.</p>
        <div className="max-h-80 space-y-2 overflow-y-auto">
          {group.cues.map(cue => <button key={cue.cue_id} type="button" onClick={() => onSeek(cue.start_ms)} className="block w-full rounded-lg p-2 text-left hover:bg-muted/60 focus-visible:outline-primary">
            <span className="font-mono text-xs text-primary">{timestamp(cue.start_ms)}–{timestamp(cue.end_ms)} · {cue.origin === "asr" ? "ASR" : `${cue.caption_kind ?? "source"} caption`}</span>
            <span className="mt-1 block text-sm leading-6">{cue.text}</span>
          </button>)}
        </div>
        <details className="mt-4 border-t pt-3 text-xs">
          <summary className="cursor-pointer font-medium">Original intervals, provenance, and ranking</summary>
          <div className="mt-3 space-y-3 break-words">
            {group.hits.map(hit => <div key={hit.unit.unit_id} className="rounded-lg bg-muted/40 p-3">
              <p className="font-mono">{timestamp(hit.unit.start_ms)}–{timestamp(hit.unit.end_ms)} · Original matching unit</p>
              <p className="mt-1">{hit.stages.map(stage => `${stage.stage}: rank ${stage.rank}, score ${stage.score.toFixed(4)}`).join(" · ") || "Chronological timestamp match"}</p>
              <p>Fusion rank {hit.fusion_rank} · score {hit.fusion_score.toFixed(6)}{hit.rerank_rank != null ? ` · Reranker rank ${hit.rerank_rank}, score ${hit.rerank_score?.toFixed(4)}` : ""}</p>
            </div>)}
            {group.units.map(unit => <p key={unit.unit_id} className="break-all font-mono">Unit {unit.unit_id} · {timestamp(unit.start_ms)}–{timestamp(unit.end_ms)}</p>)}
            {group.cues.map(cue => <p key={cue.cue_id} className="break-all">Cue {cue.cue_id} · {cue.language_code} · {cue.origin === "asr" ? `ASR run ${cue.transcription_run_id}` : `Track ${cue.track_id}`}{cue.confidence != null ? ` · confidence ${cue.confidence} (${cue.confidence_method})` : ""}</p>)}
          </div>
        </details>
      </article>)}

      <div className="space-y-2 border-t pt-4">
        <p className="break-all font-mono text-[0.65rem] text-muted-foreground">Trace {result.trace_id} · {result.mode} · {result.context_chars.toLocaleString()} context characters</p>
        <Button type="button" variant="outline" disabled={loadingTrace} onClick={loadTrace}>{loadingTrace ? "Loading trace…" : "Inspect retrieval trace"}</Button>
        {traceError ? <p role="alert" className="text-sm text-destructive">{traceError}</p> : null}
        {trace ? <details open className="rounded-xl border p-3 text-xs"><summary className="cursor-pointer font-medium">Saved retrieval trace · version {trace.version}</summary><pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(trace.payload, null, 2)}</pre></details> : null}
      </div>
    </section>
  )
}
