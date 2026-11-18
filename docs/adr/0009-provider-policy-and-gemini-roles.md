# ADR 0009 — Provider policy and Gemini task roles

Date: 2026-11-19

## Status

Accepted for the locally completed Phase 8 implementation. Hosted CI, merge and broader model
acceptance remain separate evidence.

## Context

Optional providers need visible selection, failures, consent and budgets without moving server
logic into Next.js. Interactive answers and background Study should use independently configured
Google projects while preserving the existing local retrieval and citation boundaries.

## Decision

- FastAPI owns the catalog, immutable profile versions, three explicit modes, compatibility,
  operation/source-bound grants, admission, cooldowns, budgets and provider decision records.
  OpenAPI generates browser types. Default operation stays local only.
- Use the official Python Gemini SDK for stateless bounded JSON text output with explicit model
  codes, low thinking by default, `store=False`, one dispatch, no automatic continuation and no
  hidden retries. Validate output locally and validate citations against original evidence.
- Interactive answer credentials and Study credentials are separate key/project pairs. Vision
  optionally overrides the interactive pair as a complete pair. No key or project rotation exists.
- PostgreSQL advisory locks and atomic reservations coordinate shared project RPM/TPM/RPD across
  processes and roles. Conservative input reservations are distinct from measured provider usage.
  Daily resets use Pacific midnight, including DST. Uncertain calls retain their reservation.
- Recheck consent immediately before external dispatch and before cloud cache reuse. Scope a grant
  to its source, profile version and operation. Check the worker's current lease before each Study
  call; stale workers cannot dispatch or publish. Revocation cannot undo an already sent request.
- Save Study selection with its idempotent run. Pin the first successful provider for subsequent
  chapters and publish its actual provider/model/prompt version. Local fallback chains retain
  local evidence bounds; strict Gemini Study may use the configured larger evidence budget.
- Return backend-owned consent-preflight metadata for Study profiles that require cloud execution.
  The UI warns before queueing when sharing is unchecked, without duplicating provider rules.
- Safety blocks, invalid input and unknown completion never silently retry or fall back. Rate
  limits, daily quota, authentication, setup and compatibility failures stay distinguishable.
- Evaluation uses complete local pins and existing matching collections with fallback disabled.
  Study evaluation fails closed until its reproducibility requirements are implemented.
- Keep secrets server-side, redact provider errors, constrain file delivery to verified frame
  locations, and report safe metadata from diagnostic tools. Generated text is never executable code.

## Consequences

Bounded costs and clear provenance take priority over filling a model's maximum context. Application
budgets do not discover or increase Google account limits, and API project pricing is independent
of a consumer subscription. Dollar cost remains unknown when no verified applicable rate or actual
charge is reported. Interactions returns a model code, not an immutable weights revision; the
catalog says so explicitly. Existing vision retains its exact `modelVersion` check.

The existing modular monolith and PostgreSQL are sufficient; no new queue, database, AWS SDK,
LangGraph, machine service or model download is introduced. Shared project admission deliberately
serializes those cloud calls. Long-input coverage, large Study sets and performance/quality tuning
need measured follow-up. Workspace redesign, chat memory and multi-document imports are deferred.
