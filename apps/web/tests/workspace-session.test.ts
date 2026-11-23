import { expect, test } from "bun:test"

import { createWorkspaceSession } from "@/lib/api/workspace-session"
import type { WorkspaceTurn } from "@/lib/api/workspace"

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

test("a durable send posts only the pinned intent and reads it back through the same account", async () => {
  const passage = { source_id: "source", version_id: "version", block_id: "block" }
  const intent = { question: "What moves?", submission_key: "attempt", draft_revision: 1, passage }
  const turn: WorkspaceTurn = { turn_id: "turn", sequence: 1, question: intent.question, created_at: "2026-11-23T00:00:00Z", passage,
    run: { run_id: "run", status: "queued", revision: 0, failure: null, evidence: null, answer: null,
      settings: { model: "qwen3:4b", provider: "ollama", policy: "local-pasted-text-v1", required_revision: null },
      model_revision_at_dispatch: null, retry_of_run_id: null,
      execution: { claims: 0, possible_dispatch_at: null } } }
  const session = createWorkspaceSession(first, "legacy", () => {}, () => "legacy", async (path, init) => {
    expect(path).toBe("/api/proxy/v1/workspace/notebooks/notebook/chats/chat/turns")
    expect(new Headers(init?.headers).get("x-workspace-account")).toBe(first)
    if (init?.method === "POST") {
      expect(JSON.parse(String(init.body))).toEqual(intent)
      return Response.json(turn, { status: 202 })
    }
    return Response.json({ turns: [turn], next_before: null })
  })
  await expect(session.api.submitWorkspaceTurn("notebook", "chat", intent, session.signal)).resolves.toEqual(turn)
  await expect(session.api.listWorkspaceTurns("notebook", "chat", null, session.signal)).resolves.toEqual({ turns: [turn], next_before: null })
})

test("reconnect, Stop and explicit retry use the account-bound run endpoints", async () => {
  const stopped: WorkspaceTurn = { turn_id: "turn", sequence: 1, question: "What moves?", created_at: "2026-11-24T00:00:00Z",
    passage: { source_id: "source", version_id: "version", block_id: "block" }, run: {
      run_id: "run", status: "stopped", revision: 2, failure: null, evidence: null, answer: null,
      retry_of_run_id: null, execution: { claims: 1, possible_dispatch_at: "2026-11-24T00:00:01Z" },
      settings: { provider: "ollama", policy: "local-pasted-text-v1", model: "qwen3:4b", required_revision: null },
      model_revision_at_dispatch: "fixture",
    } }
  const linked: WorkspaceTurn = { ...stopped, turn_id: "next-turn", sequence: 2, run: {
    ...stopped.run, run_id: "next-run", status: "queued", revision: 0, retry_of_run_id: "run",
    model_revision_at_dispatch: null, execution: { claims: 0, possible_dispatch_at: null },
  } }
  const observed: Array<[string, string, unknown]> = []
  const session = createWorkspaceSession(first, "legacy", () => {}, () => "legacy", async (path, init) => {
    expect(new Headers(init?.headers).get("x-workspace-account")).toBe(first)
    observed.push([String(path), init?.method ?? "GET", init?.body ? JSON.parse(String(init.body)) : null])
    return Response.json(String(path).endsWith("/retry") ? linked : stopped)
  })
  const base = "/api/proxy/v1/workspace/notebooks/notebook/chats/chat/runs/run"
  const signal = session.signal
  await expect(session.api.getWorkspaceRun("notebook", "chat", "run", signal)).resolves.toEqual(stopped)
  await expect(session.api.stopWorkspaceRun("notebook", "chat", "run", signal)).resolves.toEqual(stopped)
  await expect(session.api.retryWorkspaceRun("notebook", "chat", "run", "retry-key", signal)).resolves.toEqual(linked)
  expect(observed).toEqual([[base, "GET", null], [`${base}/stop`, "POST", null], [`${base}/retry`, "POST", { submission_key: "retry-key" }]])
})
