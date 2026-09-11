import type { TranscriptionRunResponse } from "@/lib/api/contracts"
import {
  fallbackReasonLabel,
  formatProcessingTime,
} from "@/lib/ingestion-progress"

interface TranscriptionEvidenceProps {
  transcription: TranscriptionRunResponse | null
}

function confidence(value: number | null): string {
  return value === null ? "Not reported" : `${Math.round(value * 100)}%`
}

export function TranscriptionEvidence({
  transcription,
}: TranscriptionEvidenceProps) {
  if (transcription === null) {
    return (
      <article className="rounded-3xl border bg-card p-5 shadow-sm sm:p-6">
        <p className="font-mono text-[0.68rem] tracking-[0.18em] text-primary uppercase">
          Execution evidence
        </p>
        <div className="mt-2 flex flex-col gap-1 sm:flex-row sm:items-baseline sm:justify-between">
          <h2 className="font-heading text-xl font-semibold">Caption-backed transcript</h2>
          <span className="text-xs text-muted-foreground">Local ASR not invoked</span>
        </div>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          A viable timestamped caption track supplied the evidence, so Galaxy Frog skipped audio
          acquisition and transcription.
        </p>
      </article>
    )
  }

  const fields = [
    ["Provider", `${transcription.provider} ${transcription.provider_revision}`],
    ["Model", transcription.model],
    ["Model revision", transcription.model_revision],
    ["Execution", `${transcription.device.toUpperCase()} · ${transcription.compute_type}`],
    [
      "Language",
      `${transcription.language_code} · ${confidence(transcription.language_confidence)} confidence`,
    ],
    ["Measured time", formatProcessingTime(transcription.processing_seconds)],
    [
      "Source interval",
      `${transcription.audio_start_ms.toLocaleString()}–${transcription.audio_end_ms.toLocaleString()} ms`,
    ],
    ["Audio attempt", String(transcription.audio_attempt)],
  ] as const

  return (
    <article className="rounded-3xl border border-primary/20 bg-card p-5 shadow-sm sm:p-6">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="font-mono text-[0.68rem] tracking-[0.18em] text-primary uppercase">
            ASR execution evidence
          </p>
          <h2 className="mt-1 font-heading text-xl font-semibold">Local transcription run</h2>
        </div>
        <span className="w-fit rounded-full border border-primary/20 bg-primary/10 px-2.5 py-1 text-xs font-semibold text-primary">
          {transcription.device} · {transcription.compute_type}
        </span>
      </div>

      <div className="mt-4 rounded-2xl bg-primary/5 p-4">
        <p className="text-xs font-semibold text-primary">Why this model ran</p>
        <p className="mt-1 text-sm leading-6">
          {fallbackReasonLabel(transcription.fallback_reason)}. The worker used the configured local
          provider; it did not silently switch provider or device.
        </p>
      </div>

      <dl className="mt-5 grid gap-x-6 gap-y-4 sm:grid-cols-2">
        {fields.map(([label, value]) => (
          <div key={label} className="min-w-0 border-t pt-3">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="mt-1 break-all font-mono text-xs leading-5">{value}</dd>
          </div>
        ))}
      </dl>

      <p className="mt-5 text-xs leading-5 text-muted-foreground">
        Run {transcription.run_id} · transcribed {new Date(transcription.transcribed_at).toLocaleString()}
      </p>
    </article>
  )
}
