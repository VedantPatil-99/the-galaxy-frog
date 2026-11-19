"use client"

import { useEffect, useState } from "react"

import { getProviderRun, type ProviderAttempt, type ProviderOperation } from "@/lib/api/providers"

export function ProviderRun({ operation, runId, refresh = 0 }: {
  operation: ProviderOperation
  runId: string
  refresh?: number
}) {
  const [attempts, setAttempts] = useState<ProviderAttempt[]>([])
  const [unavailable, setUnavailable] = useState(false)

  useEffect(() => {
    let current = true
    void getProviderRun(operation, runId).then(values => {
      if (current) {
        setAttempts(values)
        setUnavailable(false)
      }
    }).catch(() => { if (current) setUnavailable(true) })
    return () => { current = false }
  }, [operation, runId, refresh])

  if (unavailable) return <p className="text-xs text-muted-foreground">Provider history is unavailable.</p>
  if (attempts.length === 0) return null
  const visible = attempts.filter(item => !item.status.startsWith("dispatch_"))
  const dispatches = new Map(attempts
    .filter(item => item.status.startsWith("dispatch_"))
    .map(item => [item.provider_id, item.status]))
  const activeDispatch = Array.from(dispatches.values()).includes("dispatch_started")
  return (
    <details className="rounded-lg border bg-card px-4 py-3 text-sm">
      <summary className="cursor-pointer font-semibold">Provider decisions</summary>
      {activeDispatch ? <p role="status" className="mt-2 text-xs text-muted-foreground">A cloud dispatch started. Its final outcome may still be pending.</p> : null}
      <ol className="mt-3 space-y-2">
        {visible.map((attempt, index) => (
          <li key={`${attempt.created_at}-${index}`} className="rounded border px-3 py-2">
            <p className="font-medium">{attempt.capability.replaceAll("_", " ")} · {attempt.status.replaceAll("_", " ")}</p>
            <p className="text-xs text-muted-foreground">Requested {attempt.profile.profile_id} v{attempt.profile.version}{attempt.requested_mode ? ` · ${attempt.requested_mode.replaceAll("_", " ")}` : ""}{attempt.requested_candidates?.length ? ` · ${attempt.requested_candidates.join(" → ")}` : ""}</p>
            <p className="text-xs">{attempt.status === "succeeded" ? "Ran" : "Candidate"} {attempt.provider} · {attempt.model} · {attempt.revision ?? "revision unknown"} · {attempt.device ?? "device unknown"}</p>
            {attempt.reason ? <p className="text-xs text-muted-foreground">{attempt.reason.replaceAll("_", " ").toLowerCase()}{attempt.retry_at ? ` · retry after ${new Date(attempt.retry_at).toLocaleString()}` : ""}</p> : null}
            {attempt.processing_ms != null ? <p className="text-xs text-muted-foreground">{(attempt.processing_ms / 1000).toFixed(2)} s processing{attempt.input_duration_ms ? ` · ${(attempt.input_duration_ms / 1000).toFixed(2)} s source` : ""}{attempt.input_duration_ms && attempt.processing_ms > 0 ? ` · ${(attempt.input_duration_ms / attempt.processing_ms).toFixed(2)}× real time` : ""}</p> : null}
            {attempt.pricing ? <p className="text-xs text-muted-foreground"><a href={attempt.pricing.source_url} target="_blank" rel="noreferrer" className="underline">{attempt.pricing.stale ? "Stale pricing" : "Provider pricing"}</a>{attempt.estimated_cost != null ? ` · estimated ${attempt.estimated_cost} ${attempt.pricing.currency ?? ""}` : " · estimate unavailable"}{attempt.actual_cost != null ? ` · billed ${attempt.actual_cost} ${attempt.pricing.currency ?? ""}` : " · billed amount unavailable"}</p> : null}
          </li>
        ))}
      </ol>
    </details>
  )
}
