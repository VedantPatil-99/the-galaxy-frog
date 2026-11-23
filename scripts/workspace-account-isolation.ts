import assert from "node:assert/strict"
import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"

const root = join(import.meta.dir, "..")
const a = "41abef09-2585-4d88-8001-68a1574a1a3e"
const b = "79e450bd-b542-4218-9f3b-82574abf9fe7"
const notebookA = "f52af6bc-aa8d-4919-9c64-ae318821c82d"
const notebookB = "acee9801-b76b-417d-94d4-59ec242e0f8a"
const chatId = "7365f545-3e32-4421-b817-5d3a16563a90"
const createdAt = "2026-11-22T00:00:00Z"
const source = {
  source_id: "3f27847a-ac9d-4615-b836-413897cb26a4",
  version_id: "ea3cc5fa-f5f7-4ea1-89ce-40dd45e3cc68",
  filename: "Owner A private.pdf",
  format: "pdf",
  size_bytes: 75,
  section_count: 1,
  pages_without_text: 0,
  created_at: createdAt,
}
const draftText = "Owner A unsynced question"
const encode = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url")
const token = (id: string) => `${encode({ alg: "HS256", typ: "JWT" })}.${encode({ sub: id })}.fixture`
const accountOf = (request: Request) => [a, b].find(id => request.headers.get("authorization") === `Bearer ${token(id)}`)
const notebook = (id: string, owner: string, title: string) => ({
  notebook_id: id, owner_id: owner, title, created_at: createdAt, updated_at: createdAt,
})
const chat = { chat_id: chatId, title: "Owner A private chat", draft: { text: "", revision: 0 }, has_draft: false, created_at: createdAt, updated_at: createdAt }
const lateCreate = Promise.withResolvers<void>()
const createStarted = Promise.withResolvers<void>()
const refreshStarted = Promise.withResolvers<void>()
const lateRefresh = Promise.withResolvers<void>()
let sessionMismatch = 0
let accountsCreated: string[] = []

function reply(code: string, status: number) {
  return Response.json({ error: { code, message: "Synthetic workspace response", correlation_id: "fixture", retryable: false } }, { status })
}

function session(id: string) {
  return {
    access_token: token(id), refresh_token: `refresh-${id}`, token_type: "bearer",
    expires_in: 3600, expires_at: Math.floor(Date.now() / 1_000) + 3600,
    user: { id, email: "fixture@example.com", role: "authenticated", aud: "authenticated", created_at: createdAt, app_metadata: {}, user_metadata: {} },
  }
}

const fixture = Bun.serve({
  hostname: "127.0.0.1",
  port: 0,
  async fetch(request) {
    const url = new URL(request.url)
    const path = url.pathname
    if (path === "/auth/v1/otp") return Response.json({})
    if (path === "/auth/v1/token" && url.searchParams.get("grant_type") === "pkce") {
      const body = await request.json()
      assert.ok(body.code_verifier, "PKCE verifier was not preserved between request and callback")
      const id = body.auth_code === "account-a" ? a : b
      assert.ok(body.auth_code === "account-a" || body.auth_code === "account-b")
      return Response.json(session(id))
    }
    if (path === "/auth/v1/token" && url.searchParams.get("grant_type") === "refresh_token") {
      const body = await request.json()
      assert.equal(body.refresh_token, `refresh-${a}`)
      refreshStarted.resolve()
      await lateRefresh.promise
      return Response.json(session(a))
    }
    if (path === "/auth/v1/logout") return new Response(null, { status: 204 })
    if (path === "/v1/workspace/status") {
      return Response.json({ mode: "workspace", authentication_configured: true, creator_access_configured: true, legacy_resources_available: false })
    }
    const owner = accountOf(request)
    if (!owner) return reply("WORKSPACE_AUTH_REQUIRED", 401)
    if (path === "/v1/workspace/session") {
      return Response.json({ account: { account_id: owner, created_at: createdAt }, email: "fixture@example.com", role: "creator" })
    }
    if (request.headers.get("x-workspace-account") !== owner) {
      sessionMismatch++
      return reply("WORKSPACE_SESSION_CHANGED", 409)
    }
    if (path === "/v1/workspace/notebooks") {
      if (request.method === "POST") {
        const { title } = await request.json()
        accountsCreated.push(owner)
        createStarted.resolve()
        await lateCreate.promise
        return Response.json(notebook(crypto.randomUUID(), owner, title), { status: 201 })
      }
      return Response.json({ notebooks: owner === a
        ? [notebook(notebookA, a, "Owner A notebook")]
        : [notebook(notebookB, b, "Owner B notebook")] })
    }
    const notebookId = path.split("/")[4]
    if (notebookId !== (owner === a ? notebookA : notebookB)) return reply("WORKSPACE_NOTEBOOK_NOT_FOUND", 404)
    if (path.endsWith("/sources") || path.endsWith("/text-sources")) return Response.json({ sources: [], next_after: null })
    if (path.endsWith("/documents")) return Response.json({ sources: owner === a ? [source] : [], next_after: null })
    if (path.endsWith("/chats")) {
      const { draft: _draft, ...summary } = chat
      return Response.json({ chats: owner === a ? [summary] : [], next_after: null })
    }
    if (path.endsWith(`/chats/${chatId}`)) return Response.json(chat)
    if (path.endsWith("/draft")) return reply("DATABASE_UNAVAILABLE", 503)
    if (path.endsWith("/sections/1")) {
      return Response.json({ source_id: source.source_id, version_id: source.version_id,
        section_number: 1, page_number: 1, text: "Owner A private preview", blocks: [] })
    }
    if (path.endsWith("/pages/1/image")) {
      const image = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a/mQAAAAASUVORK5CYII=", "base64")
      return new Response(image, { headers: { "Content-Type": "image/png", "Cache-Control": "no-store" } })
    }
    return reply("NOT_FOUND", 404)
  },
})

const reservation = Bun.serve({ hostname: "127.0.0.1", port: 0, fetch: () => new Response(null) })
const port = reservation.port
reservation.stop(true)
const origin = `http://127.0.0.1:${port}`
const profile = await mkdtemp(join(tmpdir(), "galaxy-frog-account-test-"))
const chromeBin = process.env.WORKSPACE_TEST_CHROME ?? Bun.which("google-chrome")
  ?? "C:/Program Files/Google/Chrome/Application/chrome.exe"
const web = Bun.spawn([process.execPath, "--preload", join(import.meta.dir, "workspace-auth-fixture.ts"),
  "node_modules/next/dist/bin/next", "start", "-p", String(port), "-H", "127.0.0.1"], {
  cwd: join(root, "apps", "web"),
  env: {
    ...process.env, FASTAPI_BASE_URL: fixture.url.origin, WORKSPACE_TEST_AUTH_UPSTREAM: fixture.url.origin,
    WORKSPACE_WEB_ORIGIN: origin, SUPABASE_URL: "https://workspace-fixture.test",
    SUPABASE_PUBLISHABLE_KEY: "sb_publishable_workspace_fixture",
  },
  stdout: "inherit", stderr: "inherit",
})
const chrome = Bun.spawn([chromeBin, "--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
  "--window-size=1440,960", "--no-first-run", "--disable-background-networking", "about:blank"], {
  stdout: "ignore", stderr: "inherit",
})

async function until<T>(description: string, attempt: () => Promise<T>) {
  for (let count = 0; count < 160; count++) {
    const value = await attempt().catch(() => null)
    if (value) return value
    await Bun.sleep(125)
  }
  throw new Error(`Timed out waiting for ${description}`)
}

const sockets: WebSocket[] = []
async function openTab(debuggerOrigin: string) {
  const response = await fetch(`${debuggerOrigin}/json/new?about:blank`, { method: "PUT" })
  const socket = new WebSocket((await response.json()).webSocketDebuggerUrl)
  sockets.push(socket)
  await new Promise<void>((resolve, reject) => {
    socket.addEventListener("open", () => resolve(), { once: true })
    socket.addEventListener("error", () => reject(new Error("Chrome DevTools could not connect")), { once: true })
  })
  let sequence = 0
  async function command(method: string, params: object = {}): Promise<any> {
    const id = ++sequence
    return new Promise((resolve, reject) => {
      const listener = (message: MessageEvent) => {
        const event = JSON.parse(message.data)
        if (event.id !== id) return
        socket.removeEventListener("message", listener)
        if (event.error) reject(new Error(JSON.stringify(event.error)))
        else resolve(event.result)
      }
      socket.addEventListener("message", listener)
      socket.send(JSON.stringify({ id, method, params }))
    })
  }
  async function evaluate(expression: string) {
    const result = await command("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true })
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text)
    return result.result.value
  }
  const button = (label: string, prefix: boolean) =>
    `Array.from(document.querySelectorAll('button')).find(button => ${prefix
      ? `button.textContent.trim().startsWith(${JSON.stringify(label)})`
      : `button.textContent.trim() === ${JSON.stringify(label)}`} && button.checkVisibility() && !button.disabled)`
  return {
    command, evaluate,
    go: (path: string) => command("Page.navigate", { url: origin + path }),
    visible: (text: string) => until(text, () => evaluate(`document.body.innerText.includes(${JSON.stringify(text)})`)),
    click: async (label: string, prefix = false) => {
      await until(label, () => evaluate(`Boolean(${button(label, prefix)})`))
      await evaluate(`${button(label, prefix)}.click()`)
    },
    type: async (selector: string, text: string) => {
      await command("Page.bringToFront")
      const field = `document.querySelector(${JSON.stringify(selector)})`
      await until(selector, () => evaluate(`${field}?.checkVisibility()`))
      await evaluate(`${field}.focus(); ${field}.select()`)
      await until("focused editor", () => evaluate(`document.activeElement === ${field} && document.hasFocus()`))
      await command("Input.insertText", { text })
      await until("controlled editor accepted the text", () =>
        evaluate(`${field}?.value === ${JSON.stringify(text)}`))
    },
  }
}

try {
  await until("Next.js", async () => (await fetch(`${origin}/api/proxy/v1/workspace/status`)).ok)
  const debuggerPort = await until("Chrome DevTools", async () =>
    (await Bun.file(join(profile, "DevToolsActivePort")).text()).split(/\r?\n/)[0])
  const debuggerOrigin = `http://127.0.0.1:${debuggerPort}`
  const first = await openTab(debuggerOrigin)
  const composer = '[aria-label="Chat question"] textarea'
  const draft = `document.querySelector(${JSON.stringify(composer)})?.value`
  async function requestLink(tab: Awaited<ReturnType<typeof openTab>>) {
    assert.equal(await tab.evaluate(`(async () => (await fetch('/api/auth', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'email-link', email: 'fixture@example.com' })
    })).status)()`), 200)
  }
  async function signIn(tab: Awaited<ReturnType<typeof openTab>>, code: string) {
    await requestLink(tab)
    await tab.go(`/auth/callback?code=${code}`)
  }

  await first.go("/")
  await first.visible("Sign in to your workspace")
  await signIn(first, "account-a")
  await first.visible("Owner A notebook")
  await first.click("Owner A notebook")
  await first.visible("Owner A private chat")
  await first.type(composer, draftText)
  await until("A's local draft was edited", () => first.evaluate(`${draft} === ${JSON.stringify(draftText)}`))
  await first.evaluate(`(() => {
    const verified = history.state
    const { galaxyFrogAccount, ...legacy } = verified
    history.replaceState(legacy, '', location.href)
    history.pushState(verified, '', location.href)
    history.pushState(verified, '', location.href)
  })()`)
  await first.click(source.filename, true)
  const blob = await until("bound PDF preview", () => first.evaluate("document.querySelector('img[src^=blob]')?.src"))
  await first.click("New notebook")
  await first.type("#notebook-title", "Owner A late notebook")
  await first.click("Create")
  await until("A's pending mutation", async () => Promise.race([createStarted.promise.then(() => true), Bun.sleep(50).then(() => false)]))
  assert.equal(await first.evaluate(draft), draftText)

  const second = await openTab(debuggerOrigin)
  await second.go("/")
  await second.visible("Owner A notebook")
  const scopeA = await first.evaluate("document.cookie.match(/(?:^|; )gf-workspace-session=([^;]+)/)?.[1]")
  const { cookies } = await first.command("Network.getCookies", { urls: [origin] })
  const authCookie = cookies.find((item: { name: string }) => item.name === `gf-auth-${scopeA}-sb-workspace-fixture-auth-token`)
  assert.ok(authCookie?.value.startsWith("base64-"), "A's session must be stored in its own cookie scope")
  const oldSession = JSON.parse(Buffer.from(authCookie.value.slice("base64-".length), "base64url").toString())
  oldSession.expires_at = Math.floor(Date.now() / 1_000) - 10
  await first.command("Network.setCookie", {
    name: authCookie.name, value: "base64-" + encode(oldSession), url: origin, path: "/", httpOnly: true,
  })
  await first.evaluate(`void (window.pendingWorkspaceRefresh = fetch('/api/proxy/v1/workspace/notebooks', {
    headers: { 'X-Workspace-Account': '${a}', 'X-Workspace-Session': '${scopeA}' }
  }).then(response => response.status))`)
  await until("A's in-flight cookie refresh", async () => Promise.race([
    refreshStarted.promise.then(() => true), Bun.sleep(50).then(() => false),
  ]))
  await signIn(second, "account-b")
  await second.visible("Owner B notebook")
  await first.visible("Owner B notebook")
  lateCreate.resolve()
  lateRefresh.resolve()
  assert.equal(await first.evaluate("window.pendingWorkspaceRefresh"), 200)
  for (const entry of ["account-tagged", "pre-update"]) {
    const restored = await first.evaluate(`(async () => {
      const result = new Promise(resolve => addEventListener('popstate', () => resolve({
        search: location.search, owner: history.state?.galaxyFrogAccount ?? null,
      }), { once: true }))
      history.back()
      return await result
    })()`)
    assert.ok(!restored.search.includes(notebookA) && !restored.search.includes(chatId), `${entry} history leaked old workspace IDs`)
    assert.notEqual(restored.owner, a, `${entry} history retained the old account tag`)
  }
  assert.deepEqual(accountsCreated, [a])
  for (const tab of [first, second]) {
    assert.ok(!(await tab.evaluate("document.body.innerText")).includes("Owner A"))
    assert.notEqual(await tab.evaluate(draft), draftText)
  }
  assert.equal(await first.evaluate(`fetch(${JSON.stringify(blob)}).then(() => true, () => false)`), false)
  const marker = await first.evaluate("document.cookie.match(/(?:^|; )gf-workspace-session=([^;]+)/)?.[1]")
  assert.ok(marker)
  assert.equal(await first.evaluate(`(async () => (await fetch('/api/proxy/v1/workspace/notebooks/${notebookA}/chats', {
    headers: { 'X-Workspace-Account': '${b}', 'X-Workspace-Session': '${marker}' }
  })).status)()`), 404)
  assert.equal(await first.evaluate(`(async () => (await fetch('/api/proxy/v1/workspace/notebooks', {
    headers: { 'X-Workspace-Account': '${a}', 'X-Workspace-Session': '${marker}' }
  })).status)()`), 409)
  assert.equal(sessionMismatch, 1)

  await requestLink(first)
  await second.click("Sign out")
  await second.visible("Sign in to your workspace")
  await first.visible("Sign in to your workspace")
  await second.go("/auth/callback?code=account-a")
  await first.visible("Owner A private chat")
  await until("A's draft is recovered only after signing back into A", () => first.evaluate(`${draft} === ${JSON.stringify(draftText)}`))
  await first.command("Page.reload")
  await until("A's draft survives refresh", () => first.evaluate(`${draft} === ${JSON.stringify(draftText)}`))
  console.log("PASS: two tabs, shared login/logout, late mutation and refresh, scoped preview, owner-checked reads, and unsynced draft recovery")
} finally {
  lateCreate.resolve()
  lateRefresh.resolve()
  for (const socket of sockets) socket.close()
  web.kill()
  chrome.kill()
  await Promise.all([web.exited, chrome.exited])
  fixture.stop(true)
  await rm(profile, { recursive: true, force: true, maxRetries: 5 })
}
