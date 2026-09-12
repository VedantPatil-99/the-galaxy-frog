import { describe, expect, test } from "bun:test"

import type { IngestionEventsResponse } from "@/lib/api/contracts"
import {
  fallbackReasonLabel,
  formatProcessingTime,
  ingestionEventDecision,
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

  test("summarizes persisted fallback, device, retry, and cleanup decisions", () => {
    const baseEvent: IngestionEventsResponse["events"][number] = {
      event_id: "ad42d0bd-32f5-4e67-bffe-f60e41bdc66d",
      job_id: "fd2d7b28-d75c-49ec-b906-7883deff0f4e",
      sequence: 4,
      event_type: "stage_completed",
      stage: "caption_retrieval",
      attempt: 2,
      occurred_at: "2026-09-11T09:00:00Z",
      message: null,
      error_code: null,
      retryable: null,
      details: {
        transcript_origin: "asr",
        fallback_reason: "captions_unavailable",
      },
    }

    expect(ingestionEventDecision(baseEvent)).toBe(
      "Local ASR selected · No caption track was available",
    )
    expect(
      ingestionEventDecision({
        ...baseEvent,
        stage: "transcription",
        details: {
          provider: "faster-whisper",
          model: "small",
          device: "cuda",
          compute_type: "int8_float16",
          cue_count: 1,
          processing_seconds: 31.72,
          reused: false,
        },
      }),
    ).toBe("faster-whisper small · cuda/int8_float16 · 1 cue · 31.72 s")
    expect(
      ingestionEventDecision({
        ...baseEvent,
        stage: "cleanup",
        details: {
          audio_asset_id: "989110ec-7bde-45d6-a508-58cabba3564d",
          temporary_media_present: false,
        },
      }),
    ).toBe("Temporary audio removed; database lineage retained")
    expect(
      ingestionEventDecision({
        ...baseEvent,
        event_type: "failed",
        stage: "transcription",
        error_code: "ASR_EXECUTION_FAILED",
        retryable: true,
        details: null,
      }),
    ).toBe("Retry allowed from checkpoint · ASR_EXECUTION_FAILED")
  })
})
