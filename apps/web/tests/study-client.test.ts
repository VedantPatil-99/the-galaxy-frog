import { describe, expect, spyOn, test } from "bun:test"

import { proxyRequest } from "@/lib/api/proxy"
import { selectedConsentScopes } from "@/lib/api/providers"
import { startStudy, studyExportUrl } from "@/lib/api/study"

describe("study downloads", () => {
  test("requires approval only when the backend marks cloud sharing as required", () => {
    const scope = { provider_id: "gemini-study", data: "text" as const }
    expect(() => selectedConsentScopes({ scopes: [scope], cloud_required: true }, [])).toThrow("requires cloud sharing")
    expect(selectedConsentScopes({ scopes: [scope], cloud_required: true }, ["gemini-study"])).toEqual([scope])
    expect(selectedConsentScopes({ scopes: [scope], cloud_required: false }, [])).toEqual([])
    expect(selectedConsentScopes({ scopes: [], cloud_required: false }, ["unlisted-provider"])).toEqual([])
  })
  test("carries the selected profile and grant through all Study start paths", async () => {
    const selection = { profile: { profile_id: "gemini", version: 1 }, consent_id: "synthetic-grant" }
    const bodies: Record<string, unknown>[] = []
    const urls: string[] = []
    const capture = Object.assign(async (...[url, init]: Parameters<typeof fetch>) => {
      urls.push(String(url))
      bodies.push(JSON.parse(String(init?.body)))
      return Response.json({ run_id: "synthetic-run", state: "queued" })
    }, { preconnect: globalThis.fetch.preconnect })
    const mocked = spyOn(globalThis, "fetch").mockImplementation(capture)
    try {
      await startStudy("video", "chapters", "request", null, undefined, selection)
      await startStudy("video", "notes", "request", "chapters-v1", undefined, selection)
      await startStudy("video", "quiz", "request", "chapters-v1", "parent-v1", selection)
      expect(urls).toEqual(["/api/proxy/v1/videos/video/chapters", "/api/proxy/v1/videos/video/artifacts", "/api/proxy/v1/artifacts/parent-v1/regenerate"])
      expect(bodies.every((body) => JSON.stringify(body.provider_selection) === JSON.stringify(selection))).toBe(true)
      await startStudy("video", "chapters", "request", null)
      expect(bodies[3]).not.toHaveProperty("provider_selection")
    } finally {
      mocked.mockRestore()
    }
  })

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
