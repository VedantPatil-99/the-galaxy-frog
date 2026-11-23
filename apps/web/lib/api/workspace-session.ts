import { ApiClientError, apiResponseError, requestApi } from "@/lib/api/client"
import { createWorkspaceApi } from "@/lib/api/workspace"
import { readWorkspaceSessionMarker, workspaceAccountHeader, workspaceSessionHeader } from "@/lib/workspace-session-marker"

export function createWorkspaceSession(
  accountId: string,
  marker: string,
  onChanged: () => void,
  readMarker = readWorkspaceSessionMarker,
  fetcher: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response> = fetch,
) {
  const controller = new AbortController()
  const signal = controller.signal

  function invalidate() {
    if (signal.aborted) return
    controller.abort()
    onChanged()
  }

  function isCurrent() {
    if (!signal.aborted && readMarker() !== marker) invalidate()
    return !signal.aborted
  }

  function assertCurrent() {
    isCurrent()
    if (signal.aborted) throw new DOMException("The workspace session ended.", "AbortError")
  }

  async function send(input: RequestInfo | URL, init?: RequestInit) {
    assertCurrent()
    const headers = new Headers(init?.headers)
    headers.set(workspaceAccountHeader, accountId)
    headers.set(workspaceSessionHeader, marker)
    return fetcher(input, {
      ...init,
      headers,
      cache: "no-store",
    })
  }

  async function withSession<T>(requestSignal: AbortSignal | null | undefined, request: (boundSignal: AbortSignal) => Promise<T>) {
    assertCurrent()
    const combined = requestSignal ? new AbortController() : null
    const abort = () => combined?.abort()
    if (requestSignal) {
      signal.addEventListener("abort", abort, { once: true })
      requestSignal.addEventListener("abort", abort, { once: true })
      if (requestSignal.aborted) abort()
    }
    const boundSignal = combined?.signal ?? signal
    const checkRequest = () => {
      assertCurrent()
      if (boundSignal.aborted) throw new DOMException("The workspace request was cancelled.", "AbortError")
    }
    try {
      checkRequest()
      const result = await request(boundSignal)
      checkRequest()
      return result
    } catch (error) {
      checkRequest()
      if (error instanceof ApiClientError && (
        error.status === 401 || error.response?.error.code === "WORKSPACE_SESSION_CHANGED"
        || error.response?.error.code === "WORKSPACE_INVITATION_REQUIRED"
      )) invalidate()
      throw error
    } finally {
      signal.removeEventListener("abort", abort)
      requestSignal?.removeEventListener("abort", abort)
    }
  }

  const api = createWorkspaceApi(
    (path, guard, init) => withSession(init?.signal, boundSignal => requestApi(path, guard, send, { ...init, signal: boundSignal })),
    (path, requestSignal) => withSession(requestSignal, async boundSignal => {
      const response = await send(`/api/proxy${path}`, { signal: boundSignal })
      if (!response.ok) throw await apiResponseError(response)
      return response.blob()
    }),
  )

  return { accountId, marker, signal, api, isCurrent, assertCurrent, invalidate, dispose: () => controller.abort() }
}

export type WorkspaceSession = ReturnType<typeof createWorkspaceSession>
