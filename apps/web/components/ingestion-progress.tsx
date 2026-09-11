import type {
  IngestionEventsResponse,
  IngestionJobResponse,
} from "@/lib/api/contracts"
import {
  ingestionEventLabel,
  ingestionStatusLabel,
  presentStage,
} from "@/lib/ingestion-progress"

interface IngestionProgressProps {
  job: IngestionJobResponse
  events: IngestionEventsResponse["events"]
  actionPending: boolean
  onCancel: () => void
  onRetry: () => void
}

const statusTone: Record<IngestionJobResponse["status"], string> = {
  queued: "border-border bg-muted text-muted-foreground",
  running: "border-primary/25 bg-primary/10 text-primary",
  succeeded: "border-chart-3/25 bg-chart-3/10 text-chart-4 dark:text-chart-2",
  failed: "border-destructive/25 bg-destructive/10 text-destructive",
  cancelled: "border-border bg-muted text-muted-foreground",
}

function eventTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value))
}

export function IngestionProgress({
  job,
  events,
  actionPending,
  onCancel,
  onRetry,
}: IngestionProgressProps) {
  const stage = presentStage(job.stage)
  const canCancel =
    (job.status === "queued" || job.status === "running") &&
    job.cancel_requested_at == null
  const canRetry = job.status === "failed" && job.last_error_retryable === true
  const recentEvents = events.slice(-5).reverse()

  return (
    <section
      aria-label="Ingestion job progress"
      className="mb-5 overflow-hidden rounded-3xl border bg-card shadow-sm"
    >
      <div className="p-5 sm:p-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${statusTone[job.status]}`}
              >
                {ingestionStatusLabel(job.status)}
              </span>
              <span className="font-mono text-xs text-muted-foreground">
                Attempt {job.attempt}
              </span>
            </div>
            <h2 className="mt-3 font-heading text-xl font-semibold">{stage.label}</h2>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
              {stage.description}
            </p>
          </div>

          <div className="flex shrink-0 gap-2">
            {canCancel ? (
              <button
                type="button"
                onClick={onCancel}
                disabled={actionPending}
                className="h-10 rounded-xl border px-4 text-sm font-semibold transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60"
              >
                {actionPending ? "Requesting…" : "Cancel job"}
              </button>
            ) : null}
            {canRetry ? (
              <button
                type="button"
                onClick={onRetry}
                disabled={actionPending}
                className="h-10 rounded-xl bg-primary px-4 text-sm font-semibold text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {actionPending ? "Queuing…" : "Retry from checkpoint"}
              </button>
            ) : null}
          </div>
        </div>

        <div className="mt-5 h-2 overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-primary transition-[width] duration-500"
            style={{ width: `${stage.progress}%` }}
          />
        </div>
        <div className="mt-2 flex justify-between text-xs text-muted-foreground">
          <span>{job.status === "failed" ? "Checkpoint retained" : `${stage.progress}%`}</span>
          <span className="font-mono">{job.job_id.slice(0, 8)}</span>
        </div>

        {job.cancel_requested_at ? (
          <p className="mt-4 rounded-xl border bg-muted/50 px-3 py-2 text-sm text-muted-foreground">
            Cancellation requested. The worker will stop at a safe checkpoint.
          </p>
        ) : null}

        {job.status === "failed" ? (
          <div role="alert" className="mt-4 rounded-2xl border border-destructive/20 bg-destructive/5 p-4">
            <p className="font-mono text-xs font-semibold text-destructive">
              {job.last_error_code ?? "PROCESSING_FAILED"}
            </p>
            <p className="mt-1 text-sm leading-6">
              {job.last_error_message ?? "The ingestion job could not continue."}
            </p>
            <p className="mt-2 text-xs text-muted-foreground">
              {job.last_error_retryable
                ? "The durable checkpoint is safe to retry."
                : "This failure cannot be retried without changing the input or configuration."}
            </p>
          </div>
        ) : null}
      </div>

      <div className="border-t bg-muted/20 px-5 py-4 sm:px-6">
        <div className="flex items-center justify-between">
          <p className="font-mono text-[0.68rem] tracking-[0.16em] text-muted-foreground uppercase">
            Durable event trail
          </p>
          <span className="text-xs text-muted-foreground">
            {events.length} event{events.length === 1 ? "" : "s"}
          </span>
        </div>
        {recentEvents.length === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">Waiting for the first persisted event…</p>
        ) : (
          <ol className="mt-3 space-y-2">
            {recentEvents.map((event) => (
              <li
                key={event.event_id}
                className="grid grid-cols-[2.25rem_1fr_auto] items-baseline gap-2 text-sm"
              >
                <span className="font-mono text-xs text-muted-foreground">#{event.sequence}</span>
                <span>{ingestionEventLabel(event)}</span>
                <time
                  dateTime={event.occurred_at}
                  className="font-mono text-[0.68rem] text-muted-foreground"
                >
                  {eventTime(event.occurred_at)}
                </time>
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  )
}
