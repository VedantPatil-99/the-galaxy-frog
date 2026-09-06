import { describe, expect, test } from "bun:test"

import {
  ApiClientError,
  askVideoQuestion,
  cancelIngestionJob,
  checkApiConnectivity,
  getIngestionEvents,
  getTranscript,
  importVideo,
  previewDeliberateApiError,
  retryIngestionJob,
  waitForIngestionJob,
} from "@/lib/api/client"

function jsonResponse(body: unknown, status = 200): Response {
  return Response.json(body, { status })
}

describe("API client", () => {
  const video = {
    video_id: "327d444a-fd80-4a58-aedd-80bcdbe8964f",
    source_kind: "youtube",
    external_id: "dQw4w9WgXcQ",
    canonical_url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    title: "Example",
    duration_ms: 2000,
    channel_name: "Channel",
    thumbnail_url: null,
    transcript_ready: true,
    index_ready: true,
  }
  const job = {
    job_id: "fd2d7b28-d75c-49ec-b906-7883deff0f4e",
    source_kind: "youtube" as const,
    external_id: video.external_id,
    canonical_url: video.canonical_url,
    input_fingerprint: "a".repeat(64),
    status: "queued" as const,
    stage: "source_resolution" as const,
    attempt: 0,
    video_id: null,
    cancel_requested_at: null,
    started_at: null,
    completed_at: null,
    last_error_code: null,
    last_error_message: null,
    last_error_retryable: null,
    created_at: "2026-09-06T15:00:00Z",
    updated_at: "2026-09-06T15:00:00Z",
  }

  test("reads generated health contracts through the proxy", async () => {
    const requestedPaths: string[] = []
    const fetcher = (async (input: URL | RequestInfo) => {
      const path = String(input)
      requestedPaths.push(path)
      return path.endsWith("/health/live")
        ? jsonResponse({ status: "ok" })
        : jsonResponse({ status: "ready", dependencies: { database: "ready" } })
    })

    const result = await checkApiConnectivity(fetcher)

    expect(result).toEqual({
      live: { status: "ok" },
      ready: { status: "ready", dependencies: { database: "ready" } },
    })
    expect(requestedPaths).toEqual([
      "/api/proxy/health/live",
      "/api/proxy/health/ready",
    ])
  })

  test("returns the deliberate backend error envelope", async () => {
    const fetcher = (async () =>
      jsonResponse(
        {
          error: {
            code: "NOT_FOUND",
            message: "Not Found",
            correlation_id: "phase-0-test",
            retryable: false,
          },
        },
        404,
      ))

    const result = await previewDeliberateApiError(fetcher)

    expect(result.error.code).toBe("NOT_FOUND")
    expect(result.error.correlation_id).toBe("phase-0-test")
  })

  test("imports a video and reads its generated transcript contract", async () => {
    const requests: { path: string; init?: RequestInit }[] = []
    const fetcher = async (input: URL | RequestInfo, init?: RequestInit) => {
      const path = String(input)
      requests.push({ path, init })
      if (path.endsWith("/import")) return jsonResponse({ job, reused: false }, 202)
      if (path.includes("/v1/jobs/")) {
        return jsonResponse({
          ...job,
          status: "succeeded",
          stage: "completed",
          attempt: 1,
          video_id: video.video_id,
          started_at: "2026-09-06T15:00:01Z",
          completed_at: "2026-09-06T15:00:02Z",
          updated_at: "2026-09-06T15:00:02Z",
        })
      }
      return jsonResponse({
        video,
        cues: [
          {
            cue_id: "a".repeat(64),
            source_order: 0,
            start_ms: 0,
            end_ms: 2000,
            text: "Exact transcript.",
            language_code: "en",
            caption_kind: "manual",
          },
        ],
        retrieval_units: [
          {
            retrieval_unit_id: "b".repeat(64),
            start_ms: 0,
            end_ms: 2000,
            text: "Exact transcript.",
            cue_ids: ["a".repeat(64)],
          },
        ],
      })
    }

    const imported = await importVideo(video.canonical_url, fetcher)
    const completed = await waitForIngestionJob(imported.job, fetcher, {
      pollIntervalMs: 0,
      maxPolls: 1,
    })
    if (completed.video_id === null || completed.video_id === undefined) {
      throw new Error("Expected a completed video id")
    }
    const transcript = await getTranscript(completed.video_id, fetcher)

    expect(imported.reused).toBeFalse()
    expect(completed.status).toBe("succeeded")
    expect(transcript.cues[0]?.text).toBe("Exact transcript.")
    expect(requests[0]?.init?.method).toBe("POST")
    expect(requests[0]?.init?.body).toBe(
      JSON.stringify({ source_url: video.canonical_url }),
    )
  })

  test("reads events and submits generated retry and cancel contracts", async () => {
    const methods: string[] = []
    const fetcher = async (input: URL | RequestInfo, init?: RequestInit) => {
      methods.push(init?.method ?? "GET")
      if (String(input).endsWith("/events")) {
        return jsonResponse({
          job_id: job.job_id,
          events: [
            {
              event_id: "ad42d0bd-32f5-4e67-bffe-f60e41bdc66d",
              job_id: job.job_id,
              sequence: 1,
              event_type: "created",
              stage: "source_resolution",
              attempt: 0,
              occurred_at: job.created_at,
              details: {
                source_kind: "youtube",
                external_id: video.external_id,
              },
            },
          ],
        })
      }
      return jsonResponse(job)
    }

    const events = await getIngestionEvents(job.job_id, fetcher)
    await retryIngestionJob(job.job_id, fetcher)
    await cancelIngestionJob(job.job_id, fetcher)

    expect(events.events[0]?.details?.external_id).toBe(video.external_id)
    expect(methods).toEqual(["GET", "POST", "POST"])
  })

  test("returns terminal failures and bounds local job polling", async () => {
    const failed = {
      ...job,
      status: "failed" as const,
      completed_at: "2026-09-06T15:00:02Z",
      last_error_code: "SOURCE_UNAVAILABLE",
      last_error_message: "The source is unavailable.",
      last_error_retryable: true,
    }

    expect(await waitForIngestionJob(failed)).toEqual(failed)
    await expect(
      waitForIngestionJob(job, async () => jsonResponse(job), {
        pollIntervalMs: 0,
        maxPolls: 1,
      }),
    ).rejects.toBeInstanceOf(ApiClientError)
  })

  test("returns timestamped answer evidence", async () => {
    const fetcher = async (_input: URL | RequestInfo, init?: RequestInit) => {
      expect(init?.method).toBe("POST")
      return jsonResponse({
        answer: "Grounded answer.",
        confidence: "high",
        evidence: [
          {
            video_id: video.video_id,
            retrieval_unit_id: "b".repeat(64),
            cue_ids: ["a".repeat(64)],
            quote: "Exact transcript.",
            start_ms: 0,
            end_ms: 2000,
            modality: "transcript",
          },
        ],
        warnings: [],
        degraded_mode: false,
      })
    }

    const result = await askVideoQuestion(video.video_id, "What happened?", fetcher)

    expect(result.evidence[0]?.start_ms).toBe(0)
    expect(result.evidence[0]?.modality).toBe("transcript")
  })

  test("rejects an invalid successful response", async () => {
    const fetcher = async () => jsonResponse({ status: "unknown" })

    await expect(checkApiConnectivity(fetcher)).rejects.toBeInstanceOf(
      ApiClientError,
    )
  })

  test("reports a browser-to-proxy network failure", async () => {
    const fetcher = (async () => {
      throw new Error("private network detail")
    })

    try {
      await checkApiConnectivity(fetcher)
      throw new Error("Expected connectivity check to fail")
    } catch (error) {
      expect(error).toBeInstanceOf(ApiClientError)
      expect((error as ApiClientError).message).toBe(
        "The web application could not reach its API proxy.",
      )
      expect((error as ApiClientError).response).toBeNull()
    }
  })
})
