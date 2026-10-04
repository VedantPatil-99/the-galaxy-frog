import type { SearchResponse } from "@/lib/api/contracts"

const videoId = "327d444a-fd80-4a58-aedd-80bcdbe8964f"
const unit = { unit_id: "a".repeat(64), start_ms: 1000, end_ms: 4000, text: "Original transcript evidence.", cue_ids: ["b".repeat(64)] }

export const retrievalFixture: SearchResponse = {
  video_id: videoId,
  trace_id: "fd2d7b28-d75c-49ec-b906-7883deff0f4e",
  status: "resolved",
  mode: "hybrid",
  analysis: { original: "Find evidence", normalized: "Find evidence", lexical_query: "Find evidence", exact_phrases: [], kind: "spoken", version: "1" },
  window: null,
  anchors: [],
  selected_anchor: null,
  evidence: [{
    start_ms: 0, end_ms: 19000,
    hits: [{ video_id: videoId, unit, stages: [{ stage: "lexical", rank: 1, score: 0.5 }], fusion_rank: 1, fusion_score: 1 / 61, rerank_rank: null, rerank_score: null }],
    units: [unit],
    cues: [{ cue_id: unit.cue_ids[0], source: { kind: "youtube", external_id: "dQw4w9WgXcQ", canonical_url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ" }, language_code: "en", source_order: 0, start_ms: 1000, end_ms: 4000, text: unit.text, origin: "caption", track_id: "en", caption_kind: "manual", transcription_run_id: null, confidence: null, confidence_method: null }],
  }],
  context_chars: unit.text.length,
  warnings: [],
  degraded: false,
}
