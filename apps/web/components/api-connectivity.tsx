"use client"

import { ArrowsClockwiseIcon } from "@phosphor-icons/react/dist/csr/ArrowsClockwise"
import { CheckCircleIcon } from "@phosphor-icons/react/dist/csr/CheckCircle"
import { PlugsConnectedIcon } from "@phosphor-icons/react/dist/csr/PlugsConnected"
import { WarningCircleIcon } from "@phosphor-icons/react/dist/csr/WarningCircle"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import {
  ApiClientError,
  checkApiConnectivity,
  previewDeliberateApiError,
  type ConnectivitySnapshot,
} from "@/lib/api/client"
import type { ApiErrorResponse } from "@/lib/api/contracts"

type RequestState<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "success"; data: T }
  | { status: "error"; message: string; response: ApiErrorResponse | null }

const idleState = { status: "idle" } as const

function failedState(error: unknown): RequestState<never> {
  if (error instanceof ApiClientError) {
    return {
      status: "error",
      message: error.message,
      response: error.response,
    }
  }

  return {
    status: "error",
    message: "An unexpected browser error interrupted the request.",
    response: null,
  }
}

function ErrorDetails({ response }: { response: ApiErrorResponse }) {
  return (
    <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
      <p>
        <span className="text-muted-foreground">Code</span>
        <span className="ml-2 font-mono">{response.error.code}</span>
      </p>
      <p>
        <span className="text-muted-foreground">Retryable</span>
        <span className="ml-2 font-mono">
          {response.error.retryable ? "yes" : "no"}
        </span>
      </p>
      <p className="min-w-0 sm:col-span-2">
        <span className="text-muted-foreground">Correlation ID</span>
        <span className="ml-2 break-all font-mono">
          {response.error.correlation_id}
        </span>
      </p>
      {response.error.suggested_action && (
        <p className="sm:col-span-2">
          <span className="text-muted-foreground">Suggested action</span>
          <span className="ml-2">{response.error.suggested_action}</span>
        </p>
      )}
    </div>
  )
}

export function ApiConnectivity() {
  const [connection, setConnection] =
    useState<RequestState<ConnectivitySnapshot>>(idleState)
  const [errorPreview, setErrorPreview] =
    useState<RequestState<ApiErrorResponse>>(idleState)

  async function checkConnection() {
    setConnection({ status: "loading" })
    try {
      const data = await checkApiConnectivity()
      setConnection({ status: "success", data })
    } catch (error) {
      setConnection(failedState(error))
    }
  }

  async function previewError() {
    setErrorPreview({ status: "loading" })
    try {
      const data = await previewDeliberateApiError()
      setErrorPreview({ status: "success", data })
    } catch (error) {
      setErrorPreview(failedState(error))
    }
  }

  return (
    <div className="overflow-hidden rounded-[1.75rem] border bg-card/90 shadow-xl shadow-foreground/5 backdrop-blur-xl">
      <div className="grid gap-8 p-6 lg:grid-cols-[0.8fr_1.2fr] lg:p-8">
        <div>
          <div className="mb-4 grid size-10 place-items-center rounded-xl bg-primary/10 text-primary">
            <PlugsConnectedIcon aria-hidden="true" size={22} weight="duotone" />
          </div>
          <p className="font-heading text-xs font-semibold tracking-[0.16em] text-primary uppercase">
            Browser → proxy → FastAPI
          </p>
          <h2 className="mt-2 font-heading text-2xl font-semibold tracking-tight">
            API connectivity
          </h2>
          <p className="mt-3 text-sm leading-6 text-muted-foreground">
            Verify the presentation shell reaches Python through the same-origin
            Next.js proxy, then inspect a real structured backend error.
          </p>
          <div className="mt-5 flex flex-wrap gap-2">
            <Button
              type="button"
              size="lg"
              disabled={connection.status === "loading"}
              aria-busy={connection.status === "loading"}
              onClick={checkConnection}
            >
              {connection.status === "loading" ? (
                <ArrowsClockwiseIcon aria-hidden="true" className="animate-spin" />
              ) : (
                <PlugsConnectedIcon aria-hidden="true" />
              )}
              Check connection
            </Button>
            <Button
              type="button"
              size="lg"
              variant="outline"
              disabled={errorPreview.status === "loading"}
              aria-busy={errorPreview.status === "loading"}
              onClick={previewError}
            >
              {errorPreview.status === "loading" ? (
                <ArrowsClockwiseIcon aria-hidden="true" className="animate-spin" />
              ) : (
                <WarningCircleIcon aria-hidden="true" />
              )}
              Preview error
            </Button>
          </div>
        </div>

        <div className="grid gap-3" aria-live="polite">
          <div className="rounded-xl border bg-background/70 p-4">
            <p className="text-xs font-medium text-muted-foreground uppercase">
              Connection
            </p>
            {connection.status === "idle" && (
              <p className="mt-2 text-sm">Ready for a live and readiness check.</p>
            )}
            {connection.status === "loading" && (
              <p className="mt-2 text-sm">Contacting FastAPI through the proxy…</p>
            )}
            {connection.status === "success" && (
              <div className="mt-2 flex items-start gap-2 text-sm">
                <CheckCircleIcon
                  aria-hidden="true"
                  className="mt-0.5 shrink-0 text-chart-3"
                  size={18}
                  weight="fill"
                />
                <p>
                  FastAPI is <strong>{connection.data.live.status}</strong> and
                  PostgreSQL is <strong>{connection.data.ready.status}</strong>.
                </p>
              </div>
            )}
            {connection.status === "error" && (
              <div className="mt-2 text-sm text-destructive">
                <p>{connection.message}</p>
                {connection.response && (
                  <ErrorDetails response={connection.response} />
                )}
              </div>
            )}
          </div>

          <div className="rounded-xl border bg-background/70 p-4">
            <p className="text-xs font-medium text-muted-foreground uppercase">
              Structured error preview
            </p>
            {errorPreview.status === "idle" && (
              <p className="mt-2 text-sm">
                No deliberate backend error requested yet.
              </p>
            )}
            {errorPreview.status === "loading" && (
              <p className="mt-2 text-sm">Requesting the backend error envelope…</p>
            )}
            {errorPreview.status === "success" && (
              <div className="mt-2 text-sm">
                <div className="flex items-start gap-2 text-destructive">
                  <WarningCircleIcon
                    aria-hidden="true"
                    className="mt-0.5 shrink-0"
                    size={18}
                    weight="fill"
                  />
                  <p>{errorPreview.data.error.message}</p>
                </div>
                <ErrorDetails response={errorPreview.data} />
              </div>
            )}
            {errorPreview.status === "error" && (
              <div className="mt-2 text-sm text-destructive">
                <p>{errorPreview.message}</p>
                {errorPreview.response && (
                  <ErrorDetails response={errorPreview.response} />
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
