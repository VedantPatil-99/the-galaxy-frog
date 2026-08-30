import {
  isApiErrorResponse,
  isHealthResponse,
  isReadinessResponse,
  type ApiErrorResponse,
  type HealthResponse,
  type ReadinessResponse,
} from "@/lib/api/contracts"

const PROXY_BASE_PATH = "/api/proxy"
const DELIBERATE_ERROR_PATH = "/phase-0-deliberate-error"

type Fetcher = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>
type ResponseGuard<T> = (value: unknown) => value is T

export interface ConnectivitySnapshot {
  live: HealthResponse
  ready: ReadinessResponse
}

export class ApiClientError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly response: ApiErrorResponse | null,
  ) {
    super(message)
    this.name = "ApiClientError"
  }
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    return null
  }
}

async function requestApi<T>(
  path: string,
  guard: ResponseGuard<T>,
  fetcher: Fetcher,
): Promise<T> {
  let response: Response

  try {
    response = await fetcher(`${PROXY_BASE_PATH}${path}`, {
      cache: "no-store",
      headers: { accept: "application/json" },
    })
  } catch {
    throw new ApiClientError(
      "The web application could not reach its API proxy.",
      0,
      null,
    )
  }

  const body = await readJson(response)

  if (!response.ok) {
    const errorResponse = isApiErrorResponse(body) ? body : null
    throw new ApiClientError(
      errorResponse?.error.message ?? "The API request failed.",
      response.status,
      errorResponse,
    )
  }

  if (!guard(body)) {
    throw new ApiClientError(
      "The API returned an unexpected response shape.",
      response.status,
      null,
    )
  }

  return body
}

export async function checkApiConnectivity(
  fetcher: Fetcher = fetch,
): Promise<ConnectivitySnapshot> {
  const [live, ready] = await Promise.all([
    requestApi("/health/live", isHealthResponse, fetcher),
    requestApi("/health/ready", isReadinessResponse, fetcher),
  ])

  return { live, ready }
}

export async function previewDeliberateApiError(
  fetcher: Fetcher = fetch,
): Promise<ApiErrorResponse> {
  try {
    await requestApi(DELIBERATE_ERROR_PATH, isHealthResponse, fetcher)
  } catch (error) {
    if (error instanceof ApiClientError && error.response !== null) {
      return error.response
    }
    throw error
  }

  throw new ApiClientError(
    "The deliberate backend error unexpectedly succeeded.",
    200,
    null,
  )
}
