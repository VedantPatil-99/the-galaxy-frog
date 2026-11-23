import { expect, test } from "bun:test"

import { createWorkspaceSession } from "@/lib/api/workspace-session"

const first = "41abef09-2585-4d88-8001-68a1574a1a3e"
const second = "79e450bd-b542-4218-9f3b-82574abf9fe7"
const fixtureNotebook = {
  notebook_id: "example", title: "Private", owner_id: first,
  created_at: "2026-11-22T00:00:00Z", updated_at: "2026-11-22T00:00:00Z",
}

test("an account change discards a response even after its headers arrived", async () => {
  let marker = "session-a"
  let invalidations = 0
  const started = Promise.withResolvers<void>()
  const releaseBody = Promise.withResolvers<void>()
  const a = createWorkspaceSession(first, marker, () => { invalidations++ }, () => marker, async (_, init) => {
    expect(new Headers(init?.headers).get("x-workspace-account")).toBe(first)
    return new Response(new ReadableStream({
      async start(controller) {
        started.resolve()
        await releaseBody.promise
        controller.enqueue(new TextEncoder().encode(JSON.stringify(fixtureNotebook)))
        controller.close()
      },
    }))
  })
  const result = a.api.createNotebook("Private")
  await started.promise
  marker = "session-b"
  releaseBody.resolve()
  await expect(result).rejects.toHaveProperty("name", "AbortError")
  expect(a.signal.aborted).toBe(true)
  expect(invalidations).toBe(1)

  const b = createWorkspaceSession(second, marker, () => {}, () => marker, async (_, init) => {
    const headers = new Headers(init?.headers)
    expect(headers.get("x-workspace-account")).toBe(second)
    expect(headers.get("x-workspace-session")).toBe("session-b")
    return Response.json({ notebooks: [{ ...fixtureNotebook, owner_id: second }] })
  })
  await expect(b.api.getNotebooks(new AbortController().signal)).resolves.toEqual([
    { ...fixtureNotebook, owner_id: second },
  ])
})

test("a mismatched verified identity invalidates the session before further writes", async () => {
  let requests = 0
  let invalidations = 0
  const session = createWorkspaceSession(first, "legacy", () => { invalidations++ }, () => "legacy", async () => {
    requests++
    return Response.json({ error: {
      code: "WORKSPACE_SESSION_CHANGED", message: "The workspace sign-in changed.",
      correlation_id: "fixture", retryable: false,
    } }, { status: 409 })
  })
  await expect(session.api.createNotebook("Old tab")).rejects.toHaveProperty("status", 409)
  await expect(session.api.createNotebook("Retry")).rejects.toHaveProperty("name", "AbortError")
  expect(requests).toBe(1)
  expect(invalidations).toBe(1)
})
