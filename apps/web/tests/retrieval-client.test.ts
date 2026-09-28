import { expect, test } from "bun:test"

import { askVideoQuestion, getRetrievalTrace, searchVideo } from "@/lib/api/client"
import { isSearchResponse, isAnswerResponse, isRetrievalTraceResponse } from "@/lib/api/contracts"
import { retrievalFixture } from "./retrieval-fixture"

test("sends retrieval-only and selected-anchor contracts to FastAPI through the proxy", async () => {
  const calls: { path: string; body: unknown }[] = []
  const fetcher = async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ path: String(input), body: init?.body ? JSON.parse(String(init.body)) : null })
    return Response.json(retrievalFixture)
  }
  const options = { query: "before the introduction", mode: "hybrid" as const, limit: 8, allow_fallback: true, selected_anchor: "c".repeat(64) }
  const result = await searchVideo(retrievalFixture.video_id, options, fetcher)
  expect(result.trace_id).toBe(retrievalFixture.trace_id)
  expect(calls).toEqual([{ path: `/api/proxy/v1/videos/${retrievalFixture.video_id}/search`, body: options }])
})

test("reads a video-scoped durable trace and carries question retrieval options", async () => {
  const trace = { trace_id: retrievalFixture.trace_id, video_id: retrievalFixture.video_id, version: 1, created_at: "2026-09-28T00:00:00Z", payload: { status: "resolved" } }
  await getRetrievalTrace(trace.video_id, trace.trace_id, async input => {
    expect(String(input)).toBe(`/api/proxy/v1/videos/${trace.video_id}/retrieval-traces/${trace.trace_id}`)
    return Response.json(trace)
  })
  const answer = { answer: "", confidence: "low", evidence: [], warnings: [], degraded_mode: false, retrieval_status: "anchor_selection_required", retrieval: { ...retrievalFixture, status: "anchor_selection_required", evidence: [] } }
  const result = await askVideoQuestion(trace.video_id, "before intro", async (_input, init) => {
    expect(JSON.parse(String(init?.body))).toEqual({ question: "before intro", mode: "lexical", limit: 3, allow_fallback: false, selected_anchor: "c".repeat(64) })
    return Response.json(answer)
  }, { mode: "lexical", limit: 3, allow_fallback: false, selected_anchor: "c".repeat(64) })
  expect(result.retrieval_status).toBe("anchor_selection_required")
})

test("rejects malformed evidence, metadata, anchors and trace versions before rendering", () => {
  expect(isSearchResponse(retrievalFixture)).toBeTrue()
  expect(isSearchResponse({ ...retrievalFixture, evidence: [{ ...retrievalFixture.evidence[0], cues: [{ text: "missing lineage" }] }] })).toBeFalse()
  expect(isSearchResponse({ ...retrievalFixture, anchors: [{ text: "missing identity" }] })).toBeFalse()
  expect(isSearchResponse({ ...retrievalFixture, evidence: [{ ...retrievalFixture.evidence[0], start_ms: -1 }] })).toBeFalse()
  expect(isAnswerResponse({ answer: "x", confidence: "low", evidence: [], warnings: [], degraded_mode: false, retrieval: { bad: true } })).toBeFalse()
  expect(isRetrievalTraceResponse({ version: 2, payload: {} })).toBeFalse()
})
