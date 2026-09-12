import type { ApiErrorResponse } from "@/lib/api/contracts"

const DEFAULT_FASTAPI_BASE_URL = "http://127.0.0.1:8000"
const CORRELATION_ID_HEADER = "X-Correlation-ID"
const CORRELATION_ID_PATTERN = /^[A-Za-z0-9._-]{1,128}$/
const REQUEST_HEADERS = [
  "accept",
  "authorization",
  "content-type",
  "x-correlation-id",
] as const
const RESPONSE_HEADERS = [
  "content-type",
  "retry-after",
  "x-correlation-id",
] as const
const METHODS_WITHOUT_BODY = new Set(["GET", "HEAD"])

type Fetcher = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>
type StreamingRequestInit = RequestInit & { duplex?: "half" }

class ProxyInputError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message)
  }
}

function correlationIdFor(request: Request): string {
  const candidate = request.headers.get(CORRELATION_ID_HEADER)
  return candidate !== null && CORRELATION_ID_PATTERN.test(candidate)
    ? candidate
    : crypto.randomUUID()
}

function proxyErrorResponse(
  request: Request,
  status: number,
  code: string,
  message: string,
  retryable: boolean,
  suggestedAction: string,
): Response {
  const correlationId = correlationIdFor(request)
  const body: ApiErrorResponse = {
    error: {
      code,
      message,
      correlation_id: correlationId,
      retryable,
      suggested_action: suggestedAction,
    },
  }

  return Response.json(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      [CORRELATION_ID_HEADER]: correlationId,
    },
  })
}

export function buildUpstreamUrl(
  requestUrl: string,
  path: readonly string[],
  baseUrl: string,
): URL {
  if (
    path.length === 0 ||
    path.some(
      (segment) =>
        segment.length === 0 ||
        segment === "." ||
        segment === ".." ||
        segment.includes("/") ||
        segment.includes("\\") ||
        segment.includes("\0"),
    )
  ) {
    throw new ProxyInputError(400, "INVALID_PROXY_PATH", "The proxy path is invalid.")
  }
  if (path[0] === "internal") {
    throw new ProxyInputError(
      404,
      "PROXY_ROUTE_NOT_FOUND",
      "The requested proxy route does not exist.",
    )
  }

  let upstream: URL
  try {
    upstream = new URL(baseUrl)
  } catch {
    throw new ProxyInputError(
      500,
      "PROXY_CONFIGURATION_ERROR",
      "The API proxy is not configured correctly.",
    )
  }

  if (
    !["http:", "https:"].includes(upstream.protocol) ||
    upstream.username !== "" ||
    upstream.password !== ""
  ) {
    throw new ProxyInputError(
      500,
      "PROXY_CONFIGURATION_ERROR",
      "The API proxy is not configured correctly.",
    )
  }

  const basePath = upstream.pathname.replace(/\/$/, "")
  upstream.pathname = `${basePath}/${path.map(encodeURIComponent).join("/")}`
  upstream.search = new URL(requestUrl).search
  upstream.hash = ""
  return upstream
}

function forwardedRequestHeaders(request: Request): Headers {
  const headers = new Headers()
  for (const name of REQUEST_HEADERS) {
    const value = request.headers.get(name)
    if (value !== null) {
      headers.set(name, value)
    }
  }
  return headers
}

function forwardedResponseHeaders(response: Response): Headers {
  const headers = new Headers({ "Cache-Control": "no-store" })
  for (const name of RESPONSE_HEADERS) {
    const value = response.headers.get(name)
    if (value !== null) {
      headers.set(name, value)
    }
  }
  return headers
}

export async function proxyRequest(
  request: Request,
  path: readonly string[],
  fetcher: Fetcher = fetch,
  baseUrl = process.env.FASTAPI_BASE_URL ?? DEFAULT_FASTAPI_BASE_URL,
): Promise<Response> {
  let upstreamUrl: URL
  try {
    upstreamUrl = buildUpstreamUrl(request.url, path, baseUrl)
  } catch (error) {
    if (error instanceof ProxyInputError) {
      return proxyErrorResponse(
        request,
        error.status,
        error.code,
        error.message,
        false,
        "Check the requested path or server-side FASTAPI_BASE_URL setting.",
      )
    }
    throw error
  }

  const isLongRunningPhaseOneRequest =
    request.method === "POST" &&
    path[0] === "v1" &&
    path[1] === "videos" &&
    (path[2] === "import" || path[3] === "questions")
  const init: StreamingRequestInit = {
    method: request.method,
    headers: forwardedRequestHeaders(request),
    cache: "no-store",
    redirect: "manual",
    signal: AbortSignal.timeout(isLongRunningPhaseOneRequest ? 120_000 : 10_000),
  }

  if (!METHODS_WITHOUT_BODY.has(request.method) && request.body !== null) {
    init.body = request.body
    init.duplex = "half"
  }

  try {
    const upstreamResponse = await fetcher(upstreamUrl, init)
    return new Response(upstreamResponse.body, {
      status: upstreamResponse.status,
      statusText: upstreamResponse.statusText,
      headers: forwardedResponseHeaders(upstreamResponse),
    })
  } catch (error) {
    const timedOut = error instanceof Error && error.name === "TimeoutError"
    return proxyErrorResponse(
      request,
      timedOut ? 504 : 502,
      timedOut ? "UPSTREAM_TIMEOUT" : "UPSTREAM_UNAVAILABLE",
      timedOut
        ? "The API did not respond before the proxy timeout."
        : "The API is currently unavailable.",
      true,
      "Confirm FastAPI is running, then retry the request.",
    )
  }
}
