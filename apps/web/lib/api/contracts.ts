import type { components } from "@/lib/api/generated/schema"

export type ApiErrorResponse = components["schemas"]["ErrorResponse"]
export type HealthResponse = components["schemas"]["HealthResponse"]
export type ReadinessResponse = components["schemas"]["ReadinessResponse"]

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}

export function isApiErrorResponse(value: unknown): value is ApiErrorResponse {
  if (!isRecord(value) || !isRecord(value.error)) {
    return false
  }

  const { error } = value
  return (
    typeof error.code === "string" &&
    typeof error.message === "string" &&
    typeof error.correlation_id === "string" &&
    typeof error.retryable === "boolean" &&
    (error.details === undefined ||
      error.details === null ||
      isRecord(error.details)) &&
    (error.suggested_action === undefined ||
      error.suggested_action === null ||
      typeof error.suggested_action === "string")
  )
}

export function isHealthResponse(value: unknown): value is HealthResponse {
  return isRecord(value) && value.status === "ok"
}

export function isReadinessResponse(
  value: unknown,
): value is ReadinessResponse {
  if (!isRecord(value) || value.status !== "ready") {
    return false
  }

  if (value.dependencies === undefined) {
    return true
  }

  return (
    isRecord(value.dependencies) &&
    Object.values(value.dependencies).every((status) => status === "ready")
  )
}
