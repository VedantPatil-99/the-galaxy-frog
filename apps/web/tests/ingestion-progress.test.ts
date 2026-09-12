import { describe, expect, test } from "bun:test"

import type { IngestionEventsResponse } from "@/lib/api/contracts"
import {
  fallbackReasonLabel,
  formatProcessingTime,
  ingestionEventLabel,
  ingestionStatusLabel,
  presentStage,
} from "@/lib/ingestion-progress"

describe("ingestion progress presentation", () => {
  test("describes the caption-to-ASR path without hiding skipped stages", () => {
    expect(presentStage("caption_retrieval")).toEqual({
      label: "Checking captions",
      description: "Preferring viable timestamped captions before local ASR.",
      progress: 28,
    })
    expect(presentStage("transcription").progress).toBeGreaterThan(
      presentStage("audio_acquisition").progress,
    )
    expect(presentStage("completed").progress).toBe(100)
  })

  test("turns generated status and event values into readable evidence", () => {
    const event: IngestionEventsResponse["events"][number] = {
      event_id: "ad42d0bd-32f5-4e67-bffe-f60e41bdc66d",
      job_id: "fd2d7b28-d75c-49ec-b906-7883deff0f4e",
      sequence: 4,
      event_type: "stage_completed",
      stage: "transcription",
      attempt: 2,
      occurred_at: "2026-09-11T09:00:00Z",
      message: null,
      error_code: null,
      retryable: null,
      details: null,
    }

    expect(ingestionStatusLabel("failed")).toBe("Needs attention")
    expect(ingestionEventLabel(event)).toBe("Stage completed: Transcribing locally")
  })

  test("explains ASR selection and measured processing time", () => {
    expect(fallbackReasonLabel("captions_unavailable")).toBe(
      "No caption track was available",
    )
    expect(fallbackReasonLabel("captions_unusable")).toContain("usable evidence")
    expect(formatProcessingTime(31.72)).toBe("31.72 s")
    expect(formatProcessingTime(125)).toBe("2 min 5 s")
  })
})
