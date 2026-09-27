import { describe, expect, spyOn, test } from "bun:test"

import { buildUpstreamUrl, proxyRequest } from "@/lib/api/proxy"

describe("API proxy", () => {
  test("allows bounded retrieval time for search and questions", async () => {
    const timeout = spyOn(AbortSignal, "timeout").mockImplementation(() => new AbortController().signal)
    try {
      for (const [path, method, expected] of [
        [["v1", "videos", "id", "search"], "POST", 300_000],
        [["v1", "videos", "id", "questions"], "POST", 300_000],
        [["v1", "videos", "import"], "POST", 120_000],
        [["health", "live"], "GET", 10_000],
      ] as const) {
        await proxyRequest(new Request("http://web.test/api/proxy/check", { method }), path, async () => Response.json({}), "http://api.test")
        expect(timeout).toHaveBeenLastCalledWith(expected)
      }
    } finally {
      timeout.mockRestore()
    }
  })

  test("builds a fixed-host upstream URL with the incoming query", () => {
    const result = buildUpstreamUrl(
      "http://web.test/api/proxy/health/ready?detail=full",
      ["health", "ready"],
      "http://127.0.0.1:8000/base/",
    )

    expect(result.toString()).toBe(
      "http://127.0.0.1:8000/base/health/ready?detail=full",
    )
  })

  test("forwards status, safe headers, and response body", async () => {
    const fetcher = (async (input: URL | RequestInfo, init?: RequestInit) => {
      expect(String(input)).toBe("http://api.test/health/live")
      expect(init?.headers).toBeInstanceOf(Headers)
      expect((init?.headers as Headers).get("authorization")).toBe("Bearer test")
      expect((init?.headers as Headers).get("cookie")).toBeNull()
      return Response.json(
        { status: "ok" },
        {
          headers: {
            "X-Correlation-ID": "backend-id",
            "X-Private-Header": "not-forwarded",
          },
        },
      )
    })
    const request = new Request("http://web.test/api/proxy/health/live", {
      headers: {
        authorization: "Bearer test",
        cookie: "private-cookie",
      },
    })

    const response = await proxyRequest(
      request,
      ["health", "live"],
      fetcher,
      "http://api.test",
    )

    expect(response.status).toBe(200)
    expect(await response.json()).toEqual({ status: "ok" })
    expect(response.headers.get("X-Correlation-ID")).toBe("backend-id")
    expect(response.headers.get("X-Private-Header")).toBeNull()
    expect(response.headers.get("Cache-Control")).toBe("no-store")
  })

  test("rejects traversal paths before contacting FastAPI", async () => {
    let called = false
    const fetcher = (async () => {
      called = true
      return new Response()
    })
    const request = new Request("http://web.test/api/proxy/invalid", {
      headers: { "X-Correlation-ID": "safe-test-id" },
    })

    const response = await proxyRequest(
      request,
      ["..", "health"],
      fetcher,
      "http://api.test",
    )
    const body = await response.json()

    expect(called).toBeFalse()
    expect(response.status).toBe(400)
    expect(body.error.code).toBe("INVALID_PROXY_PATH")
    expect(body.error.correlation_id).toBe("safe-test-id")
  })

  test("does not expose internal service routes through the browser proxy", async () => {
    let called = false
    const fetcher = (async () => {
      called = true
      return new Response()
    })
    const request = new Request(
      "http://web.test/api/proxy/internal/qstash/dispatch",
    )

    const response = await proxyRequest(
      request,
      ["internal", "qstash", "dispatch"],
      fetcher,
      "http://api.test",
    )
    const body = await response.json()

    expect(called).toBeFalse()
    expect(response.status).toBe(404)
    expect(body.error.code).toBe("PROXY_ROUTE_NOT_FOUND")
  })

  test("normalizes an unavailable upstream into a safe error", async () => {
    const fetcher = (async () => {
      throw new Error("private connection detail")
    })
    const request = new Request("http://web.test/api/proxy/health/live")

    const response = await proxyRequest(
      request,
      ["health", "live"],
      fetcher,
      "http://api.test",
    )
    const body = await response.json()

    expect(response.status).toBe(502)
    expect(body.error.code).toBe("UPSTREAM_UNAVAILABLE")
    expect(body.error.retryable).toBeTrue()
    expect(response.headers.get("X-Correlation-ID")).toBe(
      body.error.correlation_id,
    )
    expect(JSON.stringify(body)).not.toContain("private connection detail")
  })

  test("distinguishes an upstream timeout", async () => {
    const fetcher = async () => {
      throw new DOMException("private timeout detail", "TimeoutError")
    }
    const request = new Request("http://web.test/api/proxy/health/live")

    const response = await proxyRequest(
      request,
      ["health", "live"],
      fetcher,
      "http://api.test",
    )
    const body = await response.json()

    expect(response.status).toBe(504)
    expect(body.error.code).toBe("UPSTREAM_TIMEOUT")
    expect(body.error.retryable).toBeTrue()
    expect(JSON.stringify(body)).not.toContain("private timeout detail")
  })
})
