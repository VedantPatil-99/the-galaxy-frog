import assert from "node:assert/strict"
import { mkdtemp, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { basename, dirname, join, resolve } from "node:path"

import { openWorkspaceTab, until } from "./workspace-browser-test"

if (!process.env.DATABASE_URL || !["localhost", "127.0.0.1", "[::1]"].includes(new URL(process.env.DATABASE_URL).hostname)) {
  throw new Error("Set DATABASE_URL to a migrated disposable loopback PostgreSQL database.")
}

const root = join(import.meta.dir, "..")
const account = crypto.randomUUID()
const encode = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url")
const token = `${encode({ alg: "HS256", typ: "JWT" })}.${encode({ sub: account, exp: Math.floor(Date.now() / 1_000) + 3600 })}.fixture`
const release = Promise.withResolvers<void>()
const releaseStopped = Promise.withResolvers<void>()
const model = "fixture-local:4b"
const quote = "Ice flows downhill."
const exactExcerpt = `${quote} ${"a".repeat(1_200 - quote.length - 1)}`
const unusedTail = "This part is outside the sent excerpt."
const answer = "Ice moves downhill under its own weight."
const recoveryQuestion = "Can this local run be stopped?"
let generations = 0
let recoveryDispatches = 0
const provider = Bun.serve({ hostname: "127.0.0.1", port: 0, async fetch(request) {
  const { pathname } = new URL(request.url)
  if (pathname === "/auth/v1/otp") return Response.json({})
  if (pathname === "/auth/v1/token") {
    const body = await request.json()
    assert.ok(body.code_verifier && body.auth_code === "durable-browser")
    return Response.json({ access_token: token, refresh_token: "fixture-refresh", token_type: "bearer",
      expires_in: 3600, expires_at: Math.floor(Date.now() / 1_000) + 3600,
      user: { id: account, email: "owner@example.com", role: "authenticated", aud: "authenticated",
        created_at: "2026-11-23T00:00:00Z", app_metadata: {}, user_metadata: {} },
    })
  }
  if (pathname === "/api/tags") return Response.json({ models: [{ model, digest: "fixture-digest" }] })
  if (pathname === "/api/generate") {
    const body = await request.json()
    assert.equal(body.model, model)
    assert.ok(body.prompt.includes(quote))
    assert.ok(!body.prompt.includes(unusedTail))
    generations++
    await release.promise
    if (body.prompt.includes(recoveryQuestion) && ++recoveryDispatches === 1) await releaseStopped.promise
    return Response.json({ model, done: true, response: JSON.stringify({ blocks: [
      { text: answer, citations: [{ reference: "E1", quote }] },
    ] }) })
  }
  return new Response(null, { status: 404 })
} })

function reservePort() {
  const reservation = Bun.serve({ hostname: "127.0.0.1", port: 0, fetch: () => new Response(null) })
  const port = reservation.port
  reservation.stop(true)
  return port
}

const apiPort = reservePort()
const webPort = reservePort()
assert.notEqual(apiPort, webPort)
const apiOrigin = `http://127.0.0.1:${apiPort}`
const origin = `http://127.0.0.1:${webPort}`
const profile = await mkdtemp(join(tmpdir(), "galaxy-frog-durable-test-"))
const browserBin = process.env.WORKSPACE_TEST_CHROME ?? Bun.which("google-chrome")
  ?? "C:/Program Files/Google/Chrome/Application/chrome.exe"
const python = process.platform === "win32"
  ? join(root, "backend", ".venv", "Scripts", "python.exe")
  : join(root, "backend", ".venv", "bin", "python")
const backend = Bun.spawn([
  python, "-m", "uvicorn", "tests.workspace_browser_app:app", "--port", String(apiPort), "--no-access-log",
], { cwd: join(root, "backend"), env: { ...process.env, WORKSPACE_BROWSER_ACCOUNT: account,
  WORKSPACE_BROWSER_TOKEN: token, WORKSPACE_BROWSER_OLLAMA: provider.url.origin }, stdout: "inherit", stderr: "inherit" })
const web = Bun.spawn([process.execPath, "--preload", join(import.meta.dir, "workspace-auth-fixture.ts"),
  "node_modules/next/dist/bin/next", "start", "-p", String(webPort), "-H", "127.0.0.1"], {
  cwd: join(root, "apps", "web"), env: { ...process.env, FASTAPI_BASE_URL: apiOrigin,
    WORKSPACE_TEST_AUTH_UPSTREAM: provider.url.origin, WORKSPACE_WEB_ORIGIN: origin,
    SUPABASE_URL: "https://workspace-fixture.test", SUPABASE_PUBLISHABLE_KEY: "sb_publishable_workspace_fixture" },
  stdout: "inherit", stderr: "inherit",
})
const chrome = Bun.spawn([browserBin, "--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
  "--window-size=1440,960", "--no-first-run", "--disable-background-networking", "about:blank"], { stderr: "inherit" })
const sockets: WebSocket[] = []

async function api(path: string, body?: object) {
  const response = await fetch(`${apiOrigin}/v1/workspace/notebooks${path}`, {
    method: body ? "POST" : "GET",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  })
  assert.ok(response.ok, `${path} returned ${response.status}`)
  return response.json()
}

try {
  await until("FastAPI", async () => (await fetch(`${apiOrigin}/v1/workspace/status`)).ok)
  await until("Next.js", async () => (await fetch(`${origin}/api/proxy/v1/workspace/status`)).ok)
  const port = await until("Chrome DevTools", async () => (await Bun.file(join(profile, "DevToolsActivePort")).text()).split(/\r?\n/)[0])
  const tab = await openWorkspaceTab(`http://127.0.0.1:${port}`, origin, sockets)
  const notebook = await api("", { title: "Durable glacier notebook" })
  const base = `/${notebook.notebook_id}`
  const source = await api(`${base}/text-sources`, { title: "Original field notes", format: "plain", text: `${exactExcerpt} ${unusedTail}` })
  const chat = await api(`${base}/chats`, { chat_id: crypto.randomUUID(), title: "Saved glacier answers" })
  const chatUrl = `/?notebook=${notebook.notebook_id}&chat=${chat.chat_id}`
  const turnPath = `${base}/chats/${chat.chat_id}/turns`
  const editor = '[aria-label="Chat question"] textarea'
  const question = "How does ice move?"
  const field = `document.querySelector(${JSON.stringify(editor)})`
  const pickPassage = async () => {
    await tab.type('input[type="search"]', "downhill")
    await tab.click("Search")
    await until("pinned search result", () => tab.evaluate(`Boolean(document.querySelector('input[type="checkbox"]'))`))
    await tab.evaluate(`document.querySelector('input[type="checkbox"]').click()`)
  }

  await tab.go("/")
  await tab.visible("Sign in to your workspace")
  assert.equal(await tab.evaluate(`(async () => (await fetch('/api/auth', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: 'email-link', email: 'owner@example.com' }),
  })).status)()`), 200)
  await tab.go("/auth/callback?code=durable-browser")
  await tab.visible("Durable glacier notebook")
  await tab.go(chatUrl)
  await tab.visible("Saved glacier answers")
  await tab.type(editor, question)
  await tab.command("Page.reload")
  await until("recovered W4.1 draft", () => tab.evaluate(`${field}?.value === ${JSON.stringify(question)}`))
  await pickPassage()
  await tab.click("Send")
  await tab.visible("Check submission")
  await tab.visible("Running in Ollama")
  const running = (await api(turnPath)).turns[0]
  assert.deepEqual(running.passage, { source_id: source.source_id, version_id: source.version_id, block_id: source.blocks[0].block_id })
  await tab.command("Page.reload")
  await tab.visible("Running in Ollama")
  await until("accepted draft cleared", () => tab.evaluate(`${field}?.value === ''`))
  await tab.click("Check submission")
  await until("submission acknowledgement recovered", () => tab.evaluate(`!document.body.innerText.includes('Check submission')`))
  assert.equal((await api(turnPath)).turns.length, 1)
  release.resolve()
  await tab.visible(answer)
  await tab.command("Page.reload")
  await tab.visible(answer)
  const saved = (await api(turnPath)).turns[0]
  assert.equal(saved.turn_id, running.turn_id)
  assert.equal(saved.run.run_id, running.run.run_id)
  assert.equal(saved.run.evidence.excerpt, exactExcerpt)
  assert.equal(saved.run.evidence.end_char, 1_200)
  assert.equal(generations, 1)

  await tab.evaluate(`document.querySelector('[aria-label="Saved conversation"] li button').click()`)
  await until("citation opened the exact original excerpt", () => tab.evaluate(`document.querySelector('mark')?.textContent === ${JSON.stringify(exactExcerpt)}`))
  await tab.go(`/?notebook=${notebook.notebook_id}&chat=question`)
  await tab.visible("Quick question")
  await pickPassage()
  await tab.click("Ask about selected passages", true)
  await tab.type('textarea[maxlength="1000"]', "Where does the ice go?")
  await tab.click("Ask with Ollama")
  await tab.visible(answer)
  assert.equal((await api(turnPath)).turns.length, 1)
  assert.equal(generations, 2)
  console.log("PASS: real FastAPI and PostgreSQL, dropped acknowledgement and same-key replay after refresh, saved answer and original citation, and existing quick question")

  const recoveryChat = await api(`${base}/chats`, { chat_id: crypto.randomUUID(), title: "Recoverable local run" })
  const recoveryPath = `${base}/chats/${recoveryChat.chat_id}/turns`
  await tab.go(`/?notebook=${notebook.notebook_id}&chat=${recoveryChat.chat_id}`)
  await tab.visible("Recoverable local run")
  await pickPassage()
  await tab.type(editor, recoveryQuestion)
  await tab.click("Send")
  await tab.visible("Running in Ollama")
  const activeRun = (await api(recoveryPath)).turns[0]
  const nextDraft = "Keep this newer question."
  await tab.type(editor, nextDraft)
  await tab.visible("Draft saved")

  const proxy = "/api/proxy/v1/workspace/notebooks"
  const pageReads = `performance.getEntriesByName(location.origin + ${JSON.stringify(proxy + recoveryPath)}).length`
  const runReads = `performance.getEntriesByName(location.origin + ${JSON.stringify(proxy + `${base}/chats/${recoveryChat.chat_id}/runs/${activeRun.run.run_id}`)}).length`
  const readsBeforeOffline = await tab.evaluate(pageReads)
  await tab.command("Network.enable")
  const connection = { latency: 0, downloadThroughput: -1, uploadThroughput: -1 }
  await tab.command("Network.emulateNetworkConditions", { ...connection, offline: true })
  await tab.visible("Offline. Run status will refresh when you reconnect.")
  await tab.command("Network.emulateNetworkConditions", { ...connection, offline: false })
  await until("reconnected history from FastAPI", () => tab.evaluate(`${pageReads} > ${readsBeforeOffline}`))

  await until("visible conversation polls the active run", () => tab.evaluate(`${runReads} > 0`))
  await tab.command("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true })
  const clickPanel = (label: string) => tab.evaluate(`[...document.querySelectorAll('[aria-label="Workspace panels"] button')]
    .find(button => button.textContent.trim() === ${JSON.stringify(label)}).click()`)
  await clickPanel("Sources")
  await until("mobile Sources hides the conversation", () => tab.evaluate(`document.querySelector('[aria-label="Saved conversation"]').getClientRects().length === 0`))
  await Bun.sleep(250)
  const readsWhileHidden = await tab.evaluate(runReads)
  await Bun.sleep(2_600)
  assert.equal(await tab.evaluate(runReads), readsWhileHidden)
  const readsBeforeReturn = await tab.evaluate(pageReads)
  await clickPanel("Workspace")
  await until("mobile Workspace refreshes the conversation", () => tab.evaluate(`${pageReads} > ${readsBeforeReturn}`))
  await tab.command("Emulation.clearDeviceMetricsOverride")

  await tab.evaluate(`(() => {
    const stop = [...document.querySelectorAll('button')].find(button => button.textContent.trim() === 'Stop');
    stop.focus(); stop.click();
  })()`)
  await tab.visible("Run stopped")
  assert.equal(await tab.evaluate("document.activeElement?.textContent"), "Run stopped")
  await tab.visible("Ollama may already have received this question.")
  releaseStopped.resolve()
  await tab.command("Page.reload")
  await tab.visible("Run stopped")
  await tab.click("Restore question")
  await tab.visible("Your current draft is still in the editor.")
  assert.equal(await tab.evaluate(`${field}?.value`), nextDraft)

  await tab.evaluate(`document.querySelector('[aria-label="Open chat"]').click()`)
  await until("quick-question choice", () => tab.evaluate(`[...document.querySelectorAll('[role=option]')].some(option => option.textContent.includes('Quick question'))`))
  await tab.evaluate(`[...document.querySelectorAll('[role=option]')].find(option => option.textContent.includes('Quick question')).click()`)
  await until("quick-question location", () => tab.evaluate(`new URL(location.href).searchParams.get('chat') === 'question'`))
  await tab.evaluate("history.back()")
  await tab.visible("Run stopped")
  await tab.evaluate("history.forward()")
  await until("Forward reopens quick question", () => tab.evaluate(`new URL(location.href).searchParams.get('chat') === 'question' && !document.body.innerText.includes('Run stopped')`))
  await tab.evaluate("history.back()")
  await tab.visible("Run stopped")

  await tab.click("Retry run")
  await tab.visible(answer)
  const history = (await api(recoveryPath)).turns
  assert.equal(history.length, 2)
  assert.equal(history[0].run.status, "stopped")
  assert.equal(history[0].run.answer, null)
  assert.equal(history[1].run.retry_of_run_id, activeRun.run.run_id)
  assert.equal(history[1].run.status, "completed")
  assert.deepEqual(history[1].passage, activeRun.passage)
  assert.equal(await tab.evaluate(`${field}?.value`), nextDraft)
  await tab.evaluate(`(() => { const editor = ${field}; editor.focus(); editor.select() })()`)
  await tab.command("Input.dispatchKeyEvent", { type: "keyDown", key: "Backspace", windowsVirtualKeyCode: 8 })
  await tab.command("Input.dispatchKeyEvent", { type: "keyUp", key: "Backspace", windowsVirtualKeyCode: 8 })
  await until("empty draft is saved", async () => (await api(`${base}/chats/${recoveryChat.chat_id}`)).draft.text === "")
  await tab.click("Restore question")
  await until("stopped question restored with composer focus", () => tab.evaluate(`${field}?.value === ${JSON.stringify(recoveryQuestion)} && document.activeElement === ${field}`))
  assert.equal(recoveryDispatches, 2)
  console.log("PASS: offline/reconnect, mobile polling suspension, Stop and focus, refresh and Back/Forward, preserved and restored drafts, and explicit linked retry")
} finally {
  release.resolve()
  releaseStopped.resolve()
  for (const socket of sockets) socket.close()
  for (const process of [web, chrome, backend]) process.kill()
  await Promise.all([web.exited, chrome.exited, backend.exited])
  provider.stop(true)
  const target = resolve(profile)
  assert.equal(dirname(target), resolve(tmpdir()))
  assert.ok(basename(target).startsWith("galaxy-frog-durable-test-"))
  await rm(target, { recursive: true, force: true, maxRetries: 5 })
}
