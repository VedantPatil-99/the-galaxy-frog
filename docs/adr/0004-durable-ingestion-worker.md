# ADR 0004: Persist ingestion state and lease work through PostgreSQL

- Status: Accepted
- Date: 2026-09-05

## Context

Phase 1 performs caption retrieval, chunking, persistence, embedding, and indexing inside one HTTP
request. Audio acquisition and ASR can outlive a request, must survive process restarts, and must not
duplicate transcript evidence when a dispatcher retries delivery.

Galaxy Frog needs a local-first path that works without a hosted queue while still allowing QStash
to wake a worker in later environments. PostgreSQL is already the system of record.

## Decision

Persist the current job projection in `ingestion_jobs` and append immutable progress records to
`job_events`. A separate Python worker claims eligible rows with PostgreSQL row locking and a bounded
lease. The worker heartbeats its lease, checkpoints completed stages, observes cancellation, and
allows an expired lease to be reclaimed.

The dispatcher is a provider-independent Python interface. Local development relies on PostgreSQL
polling. QStash is an optional adapter that carries only a job ID and requested action; it never owns
job state or transports media, transcripts, prompts, or other large payloads.

The Phase 2 stage vocabulary stops at transcript indexing and cleanup. OCR, visual, and artifact
stages are added only in their own phases.

Reusable stage outputs are committed separately from progress events: `media_assets` records bounded
audio lifecycle and tool provenance, while `transcription_runs` plus `transcription_run_cues` record
one complete provider result per job. Final ASR transcript cues link to that run. Restart can reuse a
completed output without treating an event payload or a local file path as the source of truth.

## Consequences

- Worker restart and duplicate delivery can recover from durable state.
- Claim, heartbeat, event append, and stage transitions require transactional repository methods.
- A running job has one lease owner and expiry; terminal jobs have no active lease.
- The API can return quickly and expose progress without running media work in FastAPI.
- QStash is not required for the local Phase 2 path and cannot become a second source of truth.
- Worker adapters must keep timestamp intervals and output provenance attached to persisted stage
  results, even after temporary media is removed.
