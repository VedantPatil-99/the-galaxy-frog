import { expect, test } from "bun:test"

import { ApiClientError, explainVideoMoment } from "@/lib/api/client"
import type { ExplainMomentResponse } from "@/lib/api/contracts"

const response: ExplainMomentResponse = {
  video_id: "video",
  timestamp_ms: 2300,
  trace_id: "trace",
  explanation: "The speaker introduces the topic.",
  window: { start_ms: 0, end_ms: 17_300 },
  warnings: [],
  citations: [{ modality: "transcript", source_id: "cue", start_ms: 2000, end_ms: 3000, seek_ms: 2000, quote: "The topic", unit_ids: ["unit"] }],
  current_frame: null,
  related_moments: [],
}

test("posts the playhead through the proxy with external processing off by default", async () => {
  for (const timestamp of [2300, 6000]) {
    const result = await explainVideoMoment("video", { timestamp_ms: timestamp, allow_external_frames: false }, async (url, init) => {
      expect(url).toBe("/api/proxy/v1/videos/video/explain-moment")
      expect(init?.method).toBe("POST")
      expect(JSON.parse(String(init?.body))).toEqual({ timestamp_ms: timestamp, allow_external_frames: false })
      return Response.json({ ...response, timestamp_ms: timestamp })
    })
    expect(result.timestamp_ms).toBe(timestamp)
    expect(result.citations[0].seek_ms).toBe(2000)
  }
})

test("rejects a response whose citation cannot seek inside the examined window", async () => {
  await expect(explainVideoMoment("video", { timestamp_ms: 2300, allow_external_frames: false }, async () => Response.json({
    ...response,
    citations: [{ ...response.citations[0], seek_ms: response.window.end_ms }],
  }))).rejects.toBeInstanceOf(ApiClientError)
})
