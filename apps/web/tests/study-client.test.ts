import { describe, expect, test } from "bun:test"

import { proxyRequest } from "@/lib/api/proxy"
import { studyExportUrl } from "@/lib/api/study"

describe("study downloads", () => {
  test("exports the selected saved version through the same-origin proxy", async () => {
    const path = studyExportUrl("video", "artifact-v1", "markdown")
    const upstream = async (input: RequestInfo | URL) => {
      expect(String(input)).toBe("http://127.0.0.1:8000/v1/videos/video/artifacts/artifact-v1/export?format=markdown")
      return new Response("[0-1000 ms](https://www.youtube.com/watch?v=abcdefghijk&t=0s)", {
        headers: {
          "Content-Type": "text/markdown",
          "Content-Disposition": 'attachment; filename="notes-v1.md"',
        },
      })
    }
    const response = await proxyRequest(
      new Request("http://localhost" + path),
      ["v1", "videos", "video", "artifacts", "artifact-v1", "export"],
      upstream,
    )
    expect(response.headers.get("content-disposition")).toBe('attachment; filename="notes-v1.md"')
    expect(response.headers.get("cache-control")).toBe("no-store")
    expect(await response.text()).toContain("t=0s")
  })
})
