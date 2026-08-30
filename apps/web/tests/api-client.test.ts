import { describe, expect, test } from "bun:test"

import {
  ApiClientError,
  checkApiConnectivity,
  previewDeliberateApiError,
} from "@/lib/api/client"

function jsonResponse(body: unknown, status = 200): Response {
  return Response.json(body, { status })
}

describe("API client", () => {
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
