# Galaxy Frog implementation plans

## Completed plan: Phase 0 — Foundation and contracts

Duration target: 3–5 focused development days.

Status: **complete**.

### Step 1 — Scope, repository, and invariants

Status: **complete**

Work packets: P0.1 repository scaffold and the foundation portion of P0.6 documentation/invariants.

- [x] Establish MVP and non-MVP boundaries.
- [x] Initialize the local git repository and modular monorepo structure.
- [x] Add `README.md`, `AGENTS.md`, `PLANS.md`, environment example, and editor rules.
- [x] Record the modular-monolith, Python-boundary, and OpenAPI-contract decisions.
- [x] Document the remaining Phase 0 sequence and acceptance checks.

### Step 2 — Frontend and backend foundations

Status: **complete**

Work packets: P0.2 Python quality tooling and P0.3 frontend design system.

- [x] Scaffold Next.js 16, React 19, strict TypeScript, Bun, Tailwind CSS v4, and shadcn/ui with Base UI.
- [x] Scaffold Python 3.14.7, uv, FastAPI, and Pydantic v2.
- [x] Configure Ruff, Pyright, pytest, pre-commit, ESLint, and frontend tests.
- [x] Add root commands that orchestrate both language workspaces without adding JavaScript server logic.

### Step 3 — Contracts and local infrastructure

Status: **complete**

Work packets: P0.4 OpenAPI generation and P0.5 Docker Compose infrastructure.

- [x] Add local PostgreSQL with the pgvector image and a persistent development volume.
- [x] Implement `/health/live` and dependency-aware `/health/ready`.
- [x] Define the stable structured error envelope and correlation IDs.
- [x] Export FastAPI OpenAPI and generate frontend TypeScript types.
- [x] Add a thin Next.js proxy, API connectivity screen, and deliberate-error UI state.

#### Step 3A — Local PostgreSQL foundation

- [x] Pin PostgreSQL 17 with pgvector in Docker Compose and configure a persistent local volume.
- [x] Add async SQLAlchemy, asyncpg, Alembic, typed database configuration, and starter tests.
- [x] Add the initial migration that enables pgvector in the `extensions` schema.
- [x] Install and start Docker Desktop with the WSL 2 backend.
- [x] Apply the migration and verify PostgreSQL health, pgvector, and volume persistence.

#### Step 3B — API operational contracts

- [x] Implement dependency-independent liveness and PostgreSQL-aware readiness.
- [x] Add validated correlation IDs to request state, response headers, and error bodies.
- [x] Normalize deliberate, validation, HTTP, and unexpected failures into the stable error envelope.
- [x] Verify healthy, unavailable, invalid-input, not-found, and unexpected-error behavior.

#### Step 3C — Generated API contracts

- [x] Export a canonical, deterministic OpenAPI document without requiring a running API or database.
- [x] Generate frontend TypeScript declarations from FastAPI OpenAPI with `openapi-typescript`.
- [x] Add root generation and stale-artifact checks to the standard verification workflow.

#### Step 3D — Browser-to-API connectivity

- [x] Add a same-origin Next.js Route Handler that proxies only to the server-configured FastAPI base URL.
- [x] Consume generated OpenAPI types in a guarded browser client for liveness, readiness, and errors.
- [x] Add an interactive connectivity panel and render a deliberate backend `NOT_FOUND` envelope.
- [x] Verify proxy success, safe upstream failure, path rejection, and rendered UI behavior.

### Step 4 — CI and exit gate

Status: **complete**

Work packets: remaining P0.5 CI and remaining P0.6 documentation/invariants.

- [x] Add GitHub Actions for backend lint/type/test and frontend lint/type/test/build.
- [x] Verify generated OpenAPI/types are current in the first hosted CI run.
- [x] Add a local setup and troubleshooting runbook.
- [x] Run and record the complete local Phase 0 exit gate.

## Phase 0 exit gate

- [x] `bun run build` passes.
- [x] `uv run pytest` passes.
- [x] FastAPI OpenAPI is generated deterministically.
- [x] The browser calls FastAPI through the thin proxy.
- [x] A deliberate backend error renders as a useful UI state.

### Phase 0 exit-gate record — 2026-08-31

- `bun ci` reproduced the frontend workspace from `bun.lock` without changes.
- `uv sync --directory backend --locked --python 3.14.7` reproduced the backend environment.
- `bun run check` passed contract drift checks, both linters, formatting, both strict type checks,
  10 frontend tests, 28 backend tests at 100% coverage, and the Next.js production build.
- `bun run precommit` passed every configured repository hook.
- Step 3D browser verification passed liveness and PostgreSQL-backed readiness through the proxy,
  then rendered the deliberate correlated `NOT_FOUND` backend envelope. `scripts/smoke-test.sh`
  preserves those HTTP checks for repeatable local verification.
- Pull request #1 in `VedantPatil-99/the-galaxy-frog` passed the generated-contract, backend-quality,
  and frontend-quality GitHub Actions jobs. The clean runner also exposed and verified the fix for
  generating Next.js route-aware types before standalone TypeScript checks.

## Completed plan: Phase 1 — Transcript-first vertical slice

Duration target: 7–10 focused development days.

Status: **complete**. The approved plan is recorded in the Galaxy Frog Workspace as
`P1.0 — Phase 1: Transcript-first Vertical Slice`.

### P1.1 — Video source contract

- [x] Add immutable source-reference, metadata, caption-track, and source-cue value objects.
- [x] Define the asynchronous provider-independent `VideoSource` protocol.
- [x] Preserve canonical source identity and integer millisecond half-open intervals.

### P1.2 — YouTube identity and metadata

- [x] Allowlist supported YouTube hosts, reject playlists, and canonicalize video IDs.
- [x] Retrieve safe metadata through a metadata-only `yt-dlp` adapter.
- [x] Normalize source failures into stable application errors.

### P1.3 — Caption retrieval and normalization

- [x] Prefer requested-language manual captions, then requested-language automatic captions,
  without downloading media.
- [x] Normalize timestamped captions into ordered `TranscriptCue` records.
- [x] Return `TRANSCRIPT_UNAVAILABLE` rather than starting ASR.

### P1.4 — Temporal chunking

- [x] Produce deterministic, sentence-aware transcript retrieval units.
- [x] Target 150–300 approximate tokens, 20–45 seconds, and 3–8 seconds of overlap.
- [x] Preserve the ordered cue IDs and exact reconstructed interval for every unit.

### P1.5 — Persistence and idempotent import

- [x] Add portable video, cue, retrieval-unit, and provenance-link migrations.
- [x] Add repositories and a synchronous caption-only import use case.
- [x] Make canonical re-imports deterministic and duplicate-free.

### P1.6 — BGE-M3 embeddings

- [x] Verify the locked Python 3.14 environment can resolve the optional direct BGE-M3 stack;
  use the user-managed Ollama adapter so no model runtime or weights are installed automatically.
- [x] Add a provider-independent embedding protocol and 1,024-dimensional BGE-M3 adapter.
- [x] Version embedding collections by provider, model, revision, dimension, and normalization.

### P1.7 — Dense transcript retrieval

- [x] Store transcript embeddings in pgvector and query them with video-scoped cosine search.
- [x] Reject incompatible embedding collections.
- [x] Return ranked retrieval units with their complete cue provenance.

### P1.8 — Grounded answers

- [x] Add a provider-independent generation protocol and user-configured Ollama adapter.
- [x] Generate only from retrieved transcript evidence and validate citations deterministically.
- [x] Return the insufficient-evidence response when support is inadequate.

### P1.9 — Transcript-first UI

- [x] Add generated API contracts for import, transcript, and question flows.
- [x] Render the YouTube player, transcript, grounded answer, and evidence intervals.
- [x] Seek the player when a citation is selected.

### P1.10 — Exit gate

- [x] Import a public captioned video, display its transcript, and verify duplicate-free re-import.
- [x] Answer a caption-grounded question through the live UI with validated timestamp evidence.
- [x] Run the scripted Phase 1 smoke and click a citation to confirm live player seeking.
- [x] Verify re-import idempotency, database integration, generated contracts, and full quality gates.

Phase 1 is complete on `feat/video-source-contract`. On 2026-09-02,
`bun run check`, `bun run precommit`, both migrations, and the opt-in PostgreSQL/pgvector integration
test passed; the backend gate has 174 passing tests and 100% statement/branch coverage, and the
frontend has 14 passing tests. The user then installed the configured BGE-M3 and Qwen3 4B models and
verified live import, transcript rendering, idempotent reuse, and grounded answering. On 2026-09-03,
the scripted exit smoke passed with 217 cues, 20 retrieval units, two validated citations, and an
idempotent re-import; the user also confirmed that selecting a citation seeks the live player.

## Phase 1 non-goals

- Background jobs, workers, QStash, retries, cancellation, audio extraction, FFmpeg, and Whisper.
- OCR, frames, VLMs, visual retrieval, FTS, RRF, reranking, temporal expansion, and LangGraph.
- AWS deployment, chapters, notes, flashcards, quizzes, and other later-phase product features.

## Active plan: Phase 2 — Durable ingestion and ASR fallback

Duration target: 7–10 focused development days.

Status: **in progress**. The approved plan is recorded in the Galaxy Frog Workspace as
`P2.0 — Phase 2: Durable Ingestion and ASR Fallback`.

### P2.1 — Durable ingestion foundation

- [x] Add framework-independent ingestion job, stage, event, lease, cancellation, and retry values.
- [x] Add `ingestion_jobs` and append-only `job_events` with portable Alembic migrations.
- [x] Implement PostgreSQL-backed idempotent creation, ordered events, and lease-safe claiming.
- [x] Prove concurrent claims, stale-lease recovery, cancellation, retry, and terminal-state rules.

The framework-independent models, repository contract, PostgreSQL adapter, deterministic input
fingerprint, and persisted stage runner are implemented and covered by unit tests. On 2026-09-06,
Alembic applied revision `20260905_0004` to the configured PostgreSQL service and the opt-in P2.1
integration test proved duplicate-free creation, concurrent claim exclusion, stale-lease recovery,
ordered events, cancellation, retry, and terminal-state rules.

### P2.2 — Worker and persisted stage runner

- [x] Add a separate Python worker entry point that shares the modular backend package.
- [x] Run deterministic stages from durable checkpoints rather than one long HTTP request.
- [x] Heartbeat active leases and recover safely after worker restart.

The local worker now polls PostgreSQL with a unique lease owner, runs the caption-first path through
persisted source, metadata, caption, persistence, embedding, and cleanup checkpoints, and heartbeats
around provider operations. The real PostgreSQL restart test proves a replacement worker resumes at
the last completed stage without duplicating its event. The complete default backend suite passes
with 242 tests, three opt-in database tests skipped, and 100% statement and branch coverage.

### P2.3 — Job API and generated contracts

- [x] Make import create or reuse a durable job and return promptly.
- [x] Add FastAPI-owned job detail, ordered-event, retry, and cancellation endpoints.
- [x] Regenerate committed OpenAPI and frontend TypeScript declarations.

FastAPI now returns `202` with the durable job projection, exposes current state and ordered event
history, and enforces retry/cancel transitions with stable errors. The generated frontend client
polls that contract only through the Next.js proxy before loading the existing transcript view. The
real PostgreSQL end-to-end test proves duplicate import requests share one job, the worker completes
it once, event order remains intact, and Phase 1 transcript/retrieval/question data stays readable.

### P2.4 — Dispatch adapters

- [x] Define a provider-independent `JobDispatcher` protocol.
- [x] Keep local PostgreSQL dispatch as the default development path.
- [x] Add optional QStash dispatch and signature verification using identifier-only messages.
- [x] Make duplicate authenticated delivery idempotent.

Imports and retries now send provider-neutral wake-up hints after durable state is committed. Local
development acknowledges jobs already visible to PostgreSQL polling; optional QStash publishing
carries only `job_id` and `requested_action`, uses deterministic message deduplication, and verifies
the exact raw callback body against the configured URL with current/next signing keys. The internal
callback is excluded from OpenAPI and blocked by the Next.js proxy. Repeated authenticated delivery
only reads and acknowledges the current job projection, so it cannot duplicate events or outputs.

### P2.5 — Bounded audio acquisition

- [x] Acquire audio only after captions are unavailable or unusable.
- [x] Enforce duration, file-size, timeout, concurrency, and isolated-workspace limits.
- [x] Delete temporary audio after successful processing when configured.

Audio requests require an explicit unavailable/unusable-caption reason and return normalized audio
with the exact whole-source half-open millisecond interval plus downloader/normalizer revisions.
Local yt-dlp and FFmpeg providers run through shell-free bounded subprocesses in deterministic
job-attempt workspaces. Typed defaults cap duration at two hours, source and normalized files at
256 MiB each, the complete acquisition at ten minutes, and local concurrency at one. Failed and
cancelled attempts clean their isolated workspaces; successful media is removed after processing
unless retention is explicitly enabled. P2.5 defined and proved this boundary before P2.7 activated
the caption-to-audio transition and durable cleanup lifecycle.

### P2.6 — Faster-whisper provider

- [x] Prove faster-whisper and CTranslate2 compatibility with Python 3.14.7 before locking packages.
- [x] Add a provider-independent transcription protocol and faster-whisper adapter.
- [x] Support explicit CPU/GPU configuration and store model, revision, language, and confidence.

Python 3.14.7 imports and executes faster-whisper 1.2.1 with CTranslate2 4.8.2. The local provider
loads lazily, limits concurrency, enables multilingual detection and word timestamps, converts
provider intervals outward to bounded integer milliseconds, records per-cue mean word confidence,
and returns the exact provider/model revision, requested device/compute mode, detected language,
language confidence, source lineage, fallback reason, and processing time. The multilingual
`small` model is pinned to Hugging Face commit `536b0662742c02347bc0e980a01041f333bce120`.

The opt-in live probe transcribed the 11-second JFK fixture on CPU `int8` into two timestamped cues
in 4.88 seconds. After the user installed CUDA 12.8.2 and cuDNN 9.26, the same pinned multilingual
model passed on the RTX 2050 with CUDA `int8_float16`, producing one bounded cue in 31.72 seconds.
The local GPU runtime gate completed before P2.7 activated the provider.

### P2.7 — Resumable caption-to-ASR fallback

- [x] Prefer captions and invoke audio/ASR only for transcript insufficiency.
- [x] Persist ASR cues through the existing integer-millisecond provenance model.
- [x] Resume failed transcription from its durable checkpoint without duplicate output.

Caption retrieval now assesses the selected track's actual cue/chunk viability and records an
explicit `captions_unavailable` or `captions_unusable` decision before audio can run. Alembic revision
`20260910_0005` adds durable `media_assets`, `transcription_runs`, and
`transcription_run_cues` checkpoints. ASR output retains the source/audio interval, fallback reason,
provider/model revisions, requested device/compute type, detected language/confidence, processing
time, ordered cue confidence, and exact integer-millisecond intervals; final transcript cues link
back to that run. A restart reuses the audio checkpoint, stays at transcription after an inference
failure, reuses completed inference output, and cannot create a second run or final cue set. Cleanup
removes successful temporary media only after persistence/indexing while keeping its database
lineage. FastAPI exposes the safe execution evidence through regenerated OpenAPI and frontend types
without leaking a local path. On 2026-09-11, revision `20260910_0005` applied to real PostgreSQL and
all three durable-ingestion integration tests passed, including the fail/restart/complete ASR path.

### P2.8 — Progress and recovery UI

- [x] Consume only generated job API types through the thin Next.js proxy.
- [x] Render stage progress, recoverable errors, cancellation, and retry controls.
- [x] Display the actual ASR provider, model, revision, device, selection reason, fallback reason and
  measured processing time from FastAPI-owned job data as read-only execution evidence.
- [x] Preserve transcript, retrieval, answer, and citation-seeking behavior after completion.

The client now observes each generated durable job projection while polling and refreshes its ordered
event history through the thin proxy. A typed presentation model covers every FastAPI stage/status;
the workspace renders progress, attempt count, recent durable events, cancellation-pending state,
safe terminal errors, and retry only when `last_error_retryable` permits it. Successful jobs retain
the Phase 1 player/transcript/question/citation experience. Caption-backed transcripts explicitly
show that local ASR was skipped; ASR-backed transcripts show the actual provider/model revisions,
device/compute type, selection/fallback reason, language confidence, measured processing time,
source interval, run identity, and cue confidence as read-only evidence. The interactive provider
policy remains out of scope. On 2026-09-11, all 25 frontend tests, ESLint, strict TypeScript, and the
Next.js production build passed, followed by a local browser layout inspection.

### P2.9 — Operational hardening

- [x] Document worker startup, recovery, cleanup, limits, and manual provider setup.
- [x] Make retry, fallback, dispatcher, device, and cleanup decisions observable.
- [x] Add complete automated and database-backed failure-path coverage.

The standalone worker now enables INFO-level key-value logs and publishes safe structured fields for
its configured lease/poll limits, media ceilings, retention policy, fixed ASR provider/model revision,
device/compute mode, claims, completed-stage decisions, safe failures, retryability, and shutdown.
Dispatch success/failure is observable without logging credentials, raw provider errors, source
titles, or local media paths. The UI summarizes persisted fallback, device, retry, interval, reuse,
and cleanup decisions from FastAPI event data. Native faster-whisper imports only when transcription
actually starts. A fourth real PostgreSQL test proves a retryable cleanup failure retains its file
and database lineage, then resumes cleanup alone with one acquisition, transcription, cue set, and
index operation. On 2026-09-12, 507 default backend tests pass with seven opt-in integrations skipped
and 100% statement/branch coverage; all four durable-ingestion database tests, 26 frontend tests,
Ruff, Pyright, ESLint, strict TypeScript, and the production build pass.

### P2.10 — Exit gate

- [ ] Transcribe an approved captionless video and render exact timestamp cues.
- [ ] Prove worker restart recovery and transcription-stage resume.
- [ ] Prove duplicate dispatch does not duplicate jobs, events, cues, units, or embeddings.
- [ ] Verify stage-specific progress, cancellation, retry, and safe errors in the UI.
- [ ] Run the complete quality, pre-commit, migration, integration, scripted smoke, and manual gates.

## Phase 2 non-goals

- PostgreSQL FTS, lexical retrieval, RRF, reranking, and temporal expansion.
- OCR, scenes, frames, visual embeddings, VLM reasoning, and LangGraph.
- Chapters, notes, flashcards, quizzes, interactive provider/model selection, editable fallback
  profiles, cloud-consent controls, live pricing configuration, and AWS deployment. P2.8 may display
  the provider decision that actually ran; Phase 8 owns changing that policy from the UI.

## Decision log

- 2026-08-29: Project name confirmed as **Galaxy Frog: A Video RAG**.
- 2026-08-29: Use a modular monorepo with `apps/web` and `backend`.
- 2026-08-29: Keep all server and AI behavior in Python; TypeScript is presentation-only.
- 2026-08-29: Execute Phase 0 as four reviewable checkpoints and stop after each checkpoint for review.
- 2026-08-30: Standardize the project environment on Python 3.14.7; later media and ML dependencies require explicit compatibility verification.
- 2026-08-30: Use Bun's root workspace and native parallel/sequential task runner to orchestrate frontend and backend commands without adding a JavaScript server layer.
- 2026-08-30: Use pinned PostgreSQL 17 plus pgvector in Docker Compose for local development and
  keep migrations portable to a later hosted Supabase deployment.
- 2026-08-31: Commit deterministic FastAPI OpenAPI and generated TypeScript declarations; verify
  both artifacts are current through the root quality workflow.
- 2026-08-31: Keep `FASTAPI_BASE_URL` server-only and route browser API traffic through a no-store,
  fixed-upstream Next.js proxy that preserves FastAPI status and correlation metadata.
- 2026-08-31: Split CI into contract, backend, and frontend jobs with locked toolchains and
  commit-pinned third-party actions; keep the live database smoke test local for Phase 0.
- 2026-08-31: Phase 0 completed after all local exit gates and the first hosted pull-request CI run
  passed.
- 2026-08-31: Approved and started Phase 1 as ten sequential transcript-first work packets. Keep
  caption ingestion synchronous, preserve cue provenance, and stop before every Phase 2+ capability.
- 2026-09-02: Pin Ollama generation to `think: false` because the current `qwen3:4b` tag otherwise
  returns structured output in a separate thinking channel and leaves the final response empty.
- 2026-09-05: Start Phase 2 on `feat/durable-ingestion-foundation`; use PostgreSQL as the durable
  job source of truth, local dispatch as the default, and QStash only as an optional identifier-only
  adapter. Stop at manual media-runtime, model, credential, or system-configuration gates.
- 2026-09-06: Complete P2.1 after applying Alembic revision `20260905_0004` and passing the real
  PostgreSQL durable-ingestion integration gate, including concurrent claim and stale-lease recovery.
- 2026-09-06: Complete P2.2 with the standalone local worker, caption-first persisted handlers,
  provider-operation heartbeats, and a real PostgreSQL restart-resume acceptance test. Keep P2.3
  responsible for replacing the synchronous import contract and exposing job APIs.
- 2026-09-06: Complete P2.3 with prompt durable import, job detail/events/retry/cancel endpoints,
  regenerated OpenAPI/TypeScript contracts, and a PostgreSQL-backed FastAPI-to-worker acceptance
  test that preserves the Phase 1 read and grounded-answer paths.
- 2026-09-06: Complete P2.4 with a provider-neutral dispatcher, local PostgreSQL polling as the
  default, an optional identifier-only QStash adapter with URL-bound current/next-key signature
  verification, a private callback outside generated browser contracts, and idempotent duplicate
  delivery acknowledgement. Keep live QStash credentials optional and stop before P2.5's manual
  FFmpeg/media-runtime gate.
- 2026-09-07: Complete P2.5 after manually installing FFmpeg 9.0.1 and passing a real local
  normalization probe. Require a caption-fallback reason on every audio request; cap duration,
  source/output size, total deadline, and concurrency; isolate files by job attempt; retain exact
  source intervals and tool revisions; and clean temporary media after successful processing by
  default. Keep the runtime unwired until P2.7 and stop before P2.6's faster-whisper/CTranslate2
  compatibility and model/device setup gate.
- 2026-09-07: Keep Phase 2 provider presentation read-only: P2.8 will expose which ASR provider,
  model, revision and device actually ran plus its selection/fallback reason. Phase 8 owns the
  interactive automatic/manual policy, editable fallback chains, explicit cloud consent and
  pricing-aware provider configuration.
- 2026-09-07: Select Cloudflare Workers AI `@cf/openai/whisper-large-v3-turbo` as the first
  recurring-free cloud ASR candidate to benchmark in Phase 8. Reverify its quota and pricing at
  implementation time, require explicit cloud-processing consent, and keep it out of the local
  Phase 2 faster-whisper path.
- 2026-09-10: Complete P2.6 with faster-whisper 1.2.1, CTranslate2
  4.8.2, an immutable multilingual `small` model revision, bounded GPU/CPU configuration, and
  timestamp/confidence provenance. CPU `int8` and RTX 2050 CUDA `int8_float16` live probes pass
  after the user-managed CUDA 12.8.2 and cuDNN 9.26 installation. Keep GPU primary for P2.7.
- 2026-09-11: Complete P2.7 with caption-viability routing, durable audio/transcription checkpoints,
  exact ASR-to-transcript provenance, safe API execution evidence, post-index cleanup, and a real
  PostgreSQL fail/restart/complete proof. Keep provider policy fixed and read-only until Phase 8;
  P2.8 owns only progress/recovery presentation through generated FastAPI contracts.
- 2026-09-11: Complete P2.8 with observable polling, stage/event progress, safe cancellation/retry
  controls, and read-only caption/ASR execution evidence. Preserve the completed Phase 1 experience
  and keep provider selection, fallback editing, cloud consent, and pricing in Phase 8.
- 2026-09-12: Complete P2.9 with visible safe worker/dispatcher/stage records, durable-event decision
  summaries, truly lazy native ASR loading, documented recovery procedures, and a PostgreSQL cleanup
  failure/retry proof that does not duplicate completed outputs. Keep PostgreSQL events authoritative;
  logs are diagnostic, and P2.10 retains the live captionless-video and full multi-service exit gate.
