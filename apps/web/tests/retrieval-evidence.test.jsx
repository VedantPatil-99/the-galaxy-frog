import { expect, test } from "bun:test"
import { renderToStaticMarkup } from "react-dom/server"

import { RetrievalEvidence } from "@/components/retrieval-evidence"
import { timestamp } from "@/lib/timestamp"
import { retrievalFixture } from "./retrieval-fixture"

function render(result, pending = false) {
  return renderToStaticMarkup(<RetrievalEvidence result={result} pending={pending} onSelectAnchor={() => {}} onSeek={() => {}} />)
}

test("renders expanded windows separately from original cue intervals and ranks", () => {
  const markup = render(retrievalFixture)
  expect(markup).toContain("00:00–00:19")
  expect(markup).toContain("00:01–00:04")
  expect(markup).toContain("Original transcript evidence.")
  expect(markup).toContain("lexical: rank 1, score 0.5000")
  expect(markup).toContain("Inspect retrieval trace")
  expect(markup).toContain(retrievalFixture.evidence[0].units[0].unit_id)
  expect(timestamp(3_723_000)).toBe("1:02:03")
})

test("requires a visible choice for semantic anchors and disables submission while pending", () => {
  const markup = render({ ...retrievalFixture, status: "anchor_selection_required", evidence: [], anchors: [{ anchor_id: "c".repeat(64), start_ms: 60000, end_ms: 70000, unit_ids: ["a".repeat(64)], text: "Welcome everyone", match_kind: "semantic" }] }, true)
  expect(markup).toContain("Choose the event you mean")
  expect(markup).toContain("Possible match")
  expect(markup).toContain("Use 01:00")
  expect(markup).toContain("Preview moment")
  expect(markup).toContain("disabled")
})

test("renders ASR lineage, rerank scores, explicit fallback and unresolved states", () => {
  const group = retrievalFixture.evidence[0]
  const markup = render({ ...retrievalFixture, degraded: true, warnings: [{ code: "reranker_busy", message: "Reranking failed; results retain fused ordering." }], evidence: [{ ...group, hits: [{ ...group.hits[0], rerank_rank: 1, rerank_score: 3.5 }], cues: [{ ...group.cues[0], origin: "asr", track_id: null, caption_kind: null, transcription_run_id: "asr-run", confidence: 0.9, confidence_method: "mean_word_probability" }] }] })
  expect(markup).toContain("Degraded retrieval")
  expect(markup).toContain("results retain fused ordering")
  expect(markup).toContain("ASR run asr-run")
  expect(markup).toContain("Reranker rank 1, score 3.5000")
  expect(render({ ...retrievalFixture, status: "anchor_unresolved", evidence: [] })).toContain("No matching event was found")
  expect(render({ ...retrievalFixture, evidence: [] })).toContain("No transcript evidence matched")
})

test("renders visual frames with OCR text and suppresses near-duplicate frames", () => {
  const keptFrame = {
    frame_id: "11111111-1111-1111-1111-111111111111",
    sequence: 0,
    start_ms: 5000,
    end_ms: 10000,
    image_url: "/v1/videos/327d444a-fd80-4a58-aedd-80bcdbe8964f/frames/0/image",
    ocr_text: "System Architecture Diagram",
    confidence: 0.95,
    is_duplicate: false,
  }
  const dupFrame = {
    frame_id: "22222222-2222-2222-2222-222222222222",
    sequence: 1,
    start_ms: 10000,
    end_ms: 15000,
    image_url: "/v1/videos/327d444a-fd80-4a58-aedd-80bcdbe8964f/frames/1/image",
    ocr_text: "Duplicate Slide Text",
    confidence: 0.95,
    is_duplicate: true,
  }
  const markup = render({
    ...retrievalFixture,
    visual_frames: [keptFrame, dupFrame],
  })

  expect(markup).toContain("Visual Evidence &amp; Slides")
  expect(markup).toContain("1 slide")
  expect(markup).toContain("/api/proxy/v1/videos/327d444a-fd80-4a58-aedd-80bcdbe8964f/frames/0/image")
  expect(markup).toContain("00:05–00:10")
  expect(markup).toContain("System Architecture Diagram")
  expect(markup).toContain("OCR confidence: 95%")
  expect(markup).toContain("Jump to 00:05")
  expect(markup).not.toContain("Duplicate Slide Text")
})
