import {
  isApiErrorResponse,
  isAnswerResponse,
  isHealthResponse,
  isImportVideoResponse,
  isIngestionEventsResponse,
  isIngestionJobResponse,
  isReadinessResponse,
  isTranscriptResponse,
  type ApiErrorResponse,
  type AnswerResponse,
  type HealthResponse,
  type ImportVideoResponse,
  type IngestionEventsResponse,
  type IngestionJobResponse,
  type ReadinessResponse,
  type TranscriptResponse,
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
  init: RequestInit = {},
): Promise<T> {
  let response: Response

  try {
    response = await fetcher(`${PROXY_BASE_PATH}${path}`, {
      ...init,
      cache: "no-store",
      headers: {
        accept: "application/json",
        ...(init.body === undefined ? {} : { "content-type": "application/json" }),
        ...init.headers,
      },
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

export async function importVideo(
  sourceUrl: string,
  fetcher: Fetcher = fetch,
): Promise<ImportVideoResponse> {
  return requestApi("/v1/videos/import", isImportVideoResponse, fetcher, {
    method: "POST",
    body: JSON.stringify({ source_url: sourceUrl }),
  })
}

export async function getIngestionJob(
  jobId: string,
  fetcher: Fetcher = fetch,
): Promise<IngestionJobResponse> {
  return requestApi(
    `/v1/jobs/${encodeURIComponent(jobId)}`,
    isIngestionJobResponse,
    fetcher,
  )
}

export async function getIngestionEvents(
  jobId: string,
  fetcher: Fetcher = fetch,
): Promise<IngestionEventsResponse> {
  return requestApi(
    `/v1/jobs/${encodeURIComponent(jobId)}/events`,
    isIngestionEventsResponse,
    fetcher,
  )
}

export async function retryIngestionJob(
  jobId: string,
  fetcher: Fetcher = fetch,
): Promise<IngestionJobResponse> {
  return requestApi(
    `/v1/jobs/${encodeURIComponent(jobId)}/retry`,
    isIngestionJobResponse,
    fetcher,
    { method: "POST" },
  )
}

export async function cancelIngestionJob(
  jobId: string,
  fetcher: Fetcher = fetch,
): Promise<IngestionJobResponse> {
  return requestApi(
    `/v1/jobs/${encodeURIComponent(jobId)}/cancel`,
    isIngestionJobResponse,
    fetcher,
    { method: "POST" },
  )
}

export interface IngestionWaitOptions {
  pollIntervalMs?: number
  maxPolls?: number
  onUpdate?: (job: IngestionJobResponse) => void | Promise<void>
}

const terminalIngestionStatuses = new Set(["succeeded", "failed", "cancelled"])

export async function waitForIngestionJob(
  initialJob: IngestionJobResponse,
  fetcher: Fetcher = fetch,
  options: IngestionWaitOptions = {},
): Promise<IngestionJobResponse> {
  const pollIntervalMs = options.pollIntervalMs ?? 1000
  const maxPolls = options.maxPolls ?? 300
  let job = initialJob

  await options.onUpdate?.(job)
  for (let poll = 0; poll <= maxPolls; poll += 1) {
    if (terminalIngestionStatuses.has(job.status)) return job
    if (poll === maxPolls) break
    if (pollIntervalMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, pollIntervalMs))
    }
    job = await getIngestionJob(job.job_id, fetcher)
    await options.onUpdate?.(job)
  }

  throw new ApiClientError(
    "The ingestion job did not finish within the local wait limit.",
    408,
    null,
  )
}

export async function getTranscript(
  videoId: string,
  fetcher: Fetcher = fetch,
): Promise<TranscriptResponse> {
  return requestApi(
    `/v1/videos/${encodeURIComponent(videoId)}/transcript`,
    isTranscriptResponse,
    fetcher,
  )
}

export async function askVideoQuestion(
  videoId: string,
  question: string,
  fetcher: Fetcher = fetch,
): Promise<AnswerResponse> {
  return requestApi(
    `/v1/videos/${encodeURIComponent(videoId)}/questions`,
    isAnswerResponse,
    fetcher,
    { method: "POST", body: JSON.stringify({ question }) },
  )
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
