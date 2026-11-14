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
  const finished = attempts.filter(item => item.status !== "dispatch_finished")
  return (
    <details className="rounded-lg border bg-card px-4 py-3 text-sm">
      <summary className="cursor-pointer font-semibold">Provider decisions</summary>
      <ol className="mt-3 space-y-2">
        {finished.map((attempt, index) => (
          <li key={`${attempt.created_at}-${index}`} className="rounded border px-3 py-2">
            <p className="font-medium">{attempt.capability.replaceAll("_", " ")} · {attempt.status.replaceAll("_", " ")}</p>
            <p className="text-xs text-muted-foreground">Requested {attempt.profile.profile_id} v{attempt.profile.version}{attempt.requested_candidates?.length ? ` · ${attempt.requested_candidates.join(" → ")}` : ""}</p>
            <p className="text-xs">Candidate {attempt.provider} · {attempt.model} · {attempt.revision ?? "revision unknown"} · {attempt.device ?? "device unknown"}</p>
            {attempt.reason ? <p className="text-xs text-muted-foreground">{attempt.reason.replaceAll("_", " ").toLowerCase()}{attempt.retry_at ? ` · retry after ${new Date(attempt.retry_at).toLocaleString()}` : ""}</p> : null}
            {attempt.processing_ms != null ? <p className="text-xs text-muted-foreground">{(attempt.processing_ms / 1000).toFixed(2)} s processing{attempt.input_duration_ms ? ` · ${(attempt.input_duration_ms / 1000).toFixed(2)} s source` : ""}</p> : null}
            {attempt.pricing ? <p className="text-xs text-muted-foreground"><a href={attempt.pricing.source_url} target="_blank" rel="noreferrer" className="underline">{attempt.pricing.stale ? "Stale pricing" : "Provider pricing"}</a> · {attempt.estimated_cost ?? "Estimate unavailable"}</p> : null}
          </li>
        ))}
      </ol>
    </details>
  )
}
