import { expect, test } from "bun:test"
import { renderToStaticMarkup } from "react-dom/server"

import { TranscriptionEvidence } from "@/components/transcription-evidence"

test("explains when captions avoid ASR", () => {
  const markup = renderToStaticMarkup(<TranscriptionEvidence transcription={null} />)

  expect(markup).toContain("Caption-backed transcript")
  expect(markup).toContain("Local ASR not invoked")
  expect(markup).toContain("skipped audio acquisition and transcription")
})

test("renders the actual durable ASR execution evidence", () => {
  const markup = renderToStaticMarkup(
    <TranscriptionEvidence
      transcription={{
        run_id: "d707f781-0b45-430c-9489-1834134543a0",
        job_id: "fd2d7b28-d75c-49ec-b906-7883deff0f4e",
        audio_asset_id: "75116b11-a64c-4dfb-bb61-68e87c3d4092",
        audio_attempt: 1,
        fallback_reason: "captions_unavailable",
        audio_start_ms: 0,
        audio_end_ms: 11000,
        provider: "faster-whisper",
        provider_revision: "1.2.1",
        model: "small",
        model_revision: "536b0662742c02347bc0e980a01041f333bce120",
        device: "cuda",
        compute_type: "int8_float16",
        language_code: "en",
        language_confidence: 0.91,
        language_confidence_method: "provider_language_probability",
        processing_seconds: 31.72,
        transcribed_at: "2026-09-10T14:30:00Z",
      }}
    />,
  )

  expect(markup).toContain("ASR execution evidence")
  expect(markup).toContain("No caption track was available")
  expect(markup).toContain("faster-whisper 1.2.1")
  expect(markup).toContain("536b0662742c02347bc0e980a01041f333bce120")
  expect(markup).toContain("CUDA · int8_float16")
  expect(markup).toContain("31.72 s")
  expect(markup).toContain("0–11,000 ms")
})
