import { describe, expect, test } from "bun:test"

import {
  ApiClientError,
  askVideoQuestion,
  checkApiConnectivity,
  getTranscript,
  importVideo,
  previewDeliberateApiError,
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
      if (path.endsWith("/import")) return jsonResponse({ video, reused: false })
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
    const transcript = await getTranscript(imported.video.video_id, fetcher)

    expect(imported.reused).toBeFalse()
    expect(transcript.cues[0]?.text).toBe("Exact transcript.")
    expect(requests[0]?.init?.method).toBe("POST")
    expect(requests[0]?.init?.body).toBe(
      JSON.stringify({ source_url: video.canonical_url }),
    )
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
