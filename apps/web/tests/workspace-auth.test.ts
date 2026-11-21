import { afterEach, beforeEach, expect, test } from "bun:test"

import { POST } from "@/app/api/auth/route"
import { GET } from "@/app/auth/callback/route"
import { proxyRequest } from "@/lib/api/proxy"

const fixture = {
  SUPABASE_URL: "https://test-project.supabase.co",
  SUPABASE_PUBLISHABLE_KEY: "sb_publishable_test",
  WORKSPACE_WEB_ORIGIN: "http://localhost:3000",
}
const previous = new Map<string, string | undefined>()

beforeEach(() => {
  for (const [name, value] of Object.entries(fixture)) {
    previous.set(name, process.env[name])
    process.env[name] = value
  }
})

afterEach(() => {
  for (const [name, value] of previous) {
    if (value === undefined) delete process.env[name]
    else process.env[name] = value
  }
})

test("rejects browser auth writes from a different origin", async () => {
  const response = await POST(new Request("http://localhost:3000/api/auth", {
    method: "POST",
    headers: { origin: "https://other.example" },
    body: JSON.stringify({ action: "email-link", email: "creator@example.com" }),
  }))
  expect(response.status).toBe(403)
  expect(response.headers.get("cache-control")).toContain("no-store")
})

test("redirects broken email links to the configured app without reflecting parameters", async () => {
  const response = await GET(new Request("http://localhost:3000/auth/callback?next=https://other.example"))
  expect(response.status).toBe(303)
  expect(response.headers.get("location")).toBe("http://localhost:3000/?sign-in=link-expired")
  expect(response.headers.get("cache-control")).toContain("no-store")
})

test("forwards only the server-side bearer to the private API", async () => {
  await proxyRequest(
    new Request("http://localhost:3000/api/proxy/v1/workspace/session", { headers: { cookie: "sb=session" } }),
    ["v1", "workspace", "session"],
    async (_, init) => {
      const headers = new Headers(init?.headers)
      expect(headers.get("authorization")).toBe("Bearer test-session")
      expect(headers.get("cookie")).toBeNull()
      return Response.json({})
    },
    "http://api.test",
    "test-session",
  )
})
