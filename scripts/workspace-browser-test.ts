export async function until<T>(description: string, attempt: () => Promise<T>) {
  for (let count = 0; count < 160; count++) {
    const value = await attempt().catch(() => null)
    if (value) return value
    await Bun.sleep(125)
  }
  throw new Error(`Timed out waiting for ${description}`)
}

export async function openWorkspaceTab(debuggerOrigin: string, origin: string, sockets: WebSocket[]) {
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
