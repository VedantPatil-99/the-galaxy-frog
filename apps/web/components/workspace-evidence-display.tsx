import type { WorkspaceSearchHit } from "@/lib/api/workspace"
import { timestamp } from "@/lib/timestamp"

export function location(hit: WorkspaceSearchHit) {
  if (hit.kind === "video") return `${timestamp(hit.start_ms)}–${timestamp(hit.end_ms)}`
  if (hit.kind === "text") return "Pasted text"
  if (hit.page_number === null) return hit.source.format === "docx" ? "DOCX" : "Text file"
  return hit.kind === "page_ocr" ? `Page ${hit.page_number} · OCR` : `Page ${hit.page_number}`
}

export function sourceTitle(hit: WorkspaceSearchHit) {
  return "filename" in hit.source ? hit.source.filename : hit.source.title
}

export function evidenceId(hit: WorkspaceSearchHit) {
  return hit.kind === "video" ? hit.unit_id : hit.block.block_id
}

function duration(milliseconds: number) {
  if (milliseconds < 1) return "<1 ms"
  return milliseconds < 1_000 ? `${Math.round(milliseconds)} ms` : `${(milliseconds / 1_000).toFixed(1)} s`
}

export function ElapsedBadge({ elapsed, operation }: { elapsed: number; operation: "Search" | "Answer" | "Indexing" }) {
  return <span className="rounded border border-border px-1.5 py-0.5 font-sans text-xs tabular-nums" aria-label={`${operation} took ${duration(elapsed)}`} title={`Time to receive the ${operation.toLowerCase()} response`}>{duration(elapsed)}</span>
}

export function EvidenceMatchButton({ hit, onOpen }: { hit: WorkspaceSearchHit; onOpen: (hit: WorkspaceSearchHit) => void }) {
  return <button type="button" className="block w-full rounded-md px-2 py-2.5 text-left hover:bg-accent focus-visible:outline-2 focus-visible:outline-ring" onClick={() => onOpen(hit)}>
    <span className="flex flex-wrap items-baseline justify-between gap-x-2">
      <span className="min-w-0 text-sm font-semibold wrap-anywhere">{sourceTitle(hit)}</span>
      <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{location(hit)}</span>
    </span>
    <span className="mt-1 line-clamp-2 text-sm text-muted-foreground wrap-anywhere">{hit.kind === "video" ? hit.text : hit.block.text}</span>
  </button>
}
