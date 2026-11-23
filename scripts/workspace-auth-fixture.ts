// Preloaded only by the browser regression. Next still runs the real Auth route handlers.
const upstream = process.env.WORKSPACE_TEST_AUTH_UPSTREAM
if (!upstream) throw new Error("The workspace Auth fixture needs a local upstream.")
const send = globalThis.fetch
globalThis.fetch = async (input, init) => {
  const address = input instanceof Request ? input.url : String(input)
  if (!address.startsWith("https://workspace-fixture.test/")) return send(input, init)
  const url = new URL(address)
  const target = `${upstream}${url.pathname}${url.search}`
  return send(input instanceof Request ? new Request(target, input) : target, init)
}
