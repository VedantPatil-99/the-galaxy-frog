import { expect, test } from "bun:test"
import { renderToStaticMarkup } from "react-dom/server"

import { IngestionProgress } from "@/components/ingestion-progress"

const baseJob = {
  job_id: "fd2d7b28-d75c-49ec-b906-7883deff0f4e",
  source_kind: "youtube",
  external_id: "dQw4w9WgXcQ",
  canonical_url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  input_fingerprint: "a".repeat(64),
  status: "running",
  stage: "transcription",
  attempt: 1,
  video_id: null,
  cancel_requested_at: null,
  started_at: "2026-09-11T09:00:00Z",
  completed_at: null,
  last_error_code: null,
  last_error_message: null,
  last_error_retryable: null,
  created_at: "2026-09-11T08:59:58Z",
  updated_at: "2026-09-11T09:00:01Z",
}

test("renders active ASR stage, durable events, and cancellation", () => {
  const markup = renderToStaticMarkup(
    <IngestionProgress
      job={baseJob}
      events={[
        {
          event_id: "ad42d0bd-32f5-4e67-bffe-f60e41bdc66d",
          job_id: baseJob.job_id,
          sequence: 7,
          event_type: "stage_started",
          stage: "transcription",
          attempt: 1,
          occurred_at: "2026-09-11T09:00:01Z",
          message: null,
          error_code: null,
          retryable: null,
          details: null,
        },
      ]}
      actionPending={false}
      onCancel={() => undefined}
      onRetry={() => undefined}
    />,
  )

  expect(markup).toContain("Transcribing locally")
  expect(markup).toContain("Stage started: Transcribing locally")
  expect(markup).toContain("Cancel job")
  expect(markup).toContain("Attempt 1")
})

test("renders safe retry guidance for a recoverable failure", () => {
  const markup = renderToStaticMarkup(
    <IngestionProgress
      job={{
        ...baseJob,
        status: "failed",
        completed_at: "2026-09-11T09:00:02Z",
        last_error_code: "ASR_EXECUTION_FAILED",
        last_error_message: "Local transcription could not finish.",
        last_error_retryable: true,
      }}
      events={[]}
      actionPending={false}
      onCancel={() => undefined}
      onRetry={() => undefined}
    />,
  )

  expect(markup).toContain("ASR_EXECUTION_FAILED")
  expect(markup).toContain("Retry from checkpoint")
  expect(markup).toContain("The durable checkpoint is safe to retry.")
  expect(markup).not.toContain("Cancel job")
})
