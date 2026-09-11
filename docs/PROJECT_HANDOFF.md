# Galaxy Frog Project Handoff

## Project

Galaxy Frog: A Video RAG

Galaxy Frog is a provider-independent temporal multimodal retrieval
system for YouTube videos. It will retrieve transcript, OCR, and visual
evidence and produce timestamp-grounded answers.

## Current status

- Phase 0 — Foundation and Contracts
- Step 1 — Scope, repository, and invariants: completed
- Step 2 — Frontend and backend foundations: completed
- Step 2A — Next.js and Python workspaces: completed
- Step 2B — Frontend design foundation: completed
- Step 2C — FastAPI and Python quality tooling: completed
- Step 2D — Root commands and complete local verification: completed
- Step 3A — Local PostgreSQL foundation: completed
- Step 3B — API health and structured error contracts: completed
- Step 3C — Deterministic OpenAPI and generated frontend types: completed
- Step 3D — Thin proxy and browser connectivity UI: completed
- Step 4 — CI and the Phase 0 exit gate: completed
- Phase 1 — Transcript-first vertical slice: complete
- P1.1–P1.9 — Transcript-first implementation: complete
- P1.10 — Quality/database gates, scripted live smoke, and manual citation seeking: complete
- Phase 2 — Durable ingestion and ASR fallback: in progress
- P2.1 — Durable ingestion foundation: complete
- P2.2 — Worker and persisted stage runner: complete
- P2.3 — Job API and generated contracts: complete
- P2.4 — Local and optional QStash dispatch adapters: complete
- P2.5 — Bounded local audio acquisition: complete
- P2.6 — Faster-whisper provider: complete
- P2.7 — Resumable caption-to-ASR fallback: complete
- The Next.js presentation shell uses React 19, strict TypeScript,
  Tailwind CSS v4, shadcn/ui with Base UI, and system-aware themes
- The FastAPI application factory, typed settings, CLI entrypoint, and starter tests are established
- Root Bun commands synchronize, run, and verify both language workspaces without JavaScript server logic
- The Next.js page and FastAPI documentation were verified together through `bun run dev`
- PostgreSQL 17 and pgvector 0.8.6 run locally through Docker Compose with a persistent named volume
- Alembic revision `20260830_0001` enables pgvector in the `extensions` schema
- FastAPI exposes dependency-independent `/health/live` and PostgreSQL-aware `/health/ready`
- All API failures use one correlated, human-safe error envelope
- FastAPI OpenAPI and frontend TypeScript declarations are deterministic, committed artifacts with
  local stale-file checks
- The browser reaches FastAPI only through the server-configured Next.js proxy and renders both
  healthy dependency state and correlated backend errors
- Durable ingestion domain values, PostgreSQL job/event persistence, lease-safe repository
  operations, deterministic idempotency, a standalone local worker, and caption-first persisted
  stage handlers are implemented
- Caption-to-ASR routing and durable ASR persistence are active; progress UI, OCR, visual retrieval,
  reranking, and later-phase orchestration have not started
- Provider-neutral dispatch uses PostgreSQL polling by default; optional QStash messages contain
  only a job identifier/action and terminate at a URL-bound signature-verified internal callback
- FastAPI now exposes prompt durable import, job detail/events/retry/cancel, video detail,
  transcript, and grounded-question contracts
- YouTube metadata/captions remain download-free; transcript cues and retrieval units retain exact
  millisecond intervals and ordered cue provenance
- Caption-insufficient jobs checkpoint bounded audio and one complete ASR run in PostgreSQL; final
  cues retain the run/model/device/confidence lineage after temporary media cleanup
- Versioned 1,024-dimensional BGE-M3 collections and video-scoped pgvector cosine retrieval are
  implemented behind provider-independent ports
- The presentation-only UI imports a video, renders its transcript, asks grounded questions, and
  seeks the YouTube IFrame player from validated evidence citations

## Verified local tools

- Git: `2.55.0.windows.5`
- Bun: `1.4.0 (34cbb9a40)`
- Python: `3.14.7`
- uv: `0.12.7 (61291a8ca 2026-08-27 x86_64-pc-windows-msvc)`
- WSL: `2.7.12.0`, kernel `6.18.33.2-2`
- Docker Engine: `29.7.2` (verified 2026-09-11)
- Docker Compose: `v5.5.0` (verified 2026-09-11)
- FFmpeg and ffprobe: `9.0.1-full_build-www.gyan.dev` (verified 2026-09-07)
- faster-whisper: `1.2.1` with CTranslate2 `4.8.2` on Python `3.14.7`
- Multilingual `small` model: Hugging Face revision
  `536b0662742c02347bc0e980a01041f333bce120` (download and SHA-256 verified 2026-09-07)
- CPU ASR: `int8`, 11-second speech fixture produced two timestamped cues in 4.88 seconds
- GPU ASR: CUDA 12.8.2 and cuDNN 9.26, `int8_float16`; 11-second speech fixture produced one
  timestamped cue in 31.72 seconds on the RTX 2050
- Preferred command shell: Git Bash

Docker was not required for Step 2. Step 3A uses Docker Desktop with the WSL 2 backend for local
PostgreSQL rather than installing PostgreSQL and pgvector natively on Windows.

## Current state and next task

Phase 0 Step 2 is complete in four reviewed batches:

1. Step 2A — Scaffold Next.js and Python workspaces: completed
2. Step 2B — Configure the frontend design foundation: completed
3. Step 2C — Configure FastAPI and Python quality tooling: completed
4. Step 2D — Add root commands and verify both workspaces: completed

Step 3 — API contracts and local infrastructure — is complete. Step 3A established PostgreSQL 17.11,
became healthy, Alembic applied revision `20260830_0001`, pgvector 0.8.6 was verified in the
`extensions` schema, and both states survived container recreation through the named volume.
Step 3B is complete: live verification proved liveness stays `200` while PostgreSQL is stopped,
readiness changes from `200` to a correlated `503`, and readiness recovers after PostgreSQL becomes
healthy. Step 3C is complete: FastAPI exports a deterministic schema without a running service or
database, `openapi-typescript` 7.13.0 derives the frontend declarations, and `bun run check` rejects
stale artifacts. Step 3D is complete: live browser verification rendered successful liveness and
readiness through the Next.js proxy, then rendered the backend's deliberate correlated `NOT_FOUND`
envelope. Step 3 is complete; Step 4 — CI, troubleshooting documentation, and the final Phase 0 exit
gate — now has commit-pinned contract, backend, and frontend GitHub Actions jobs, a local runbook,
and a repeatable proxy smoke test. The complete local exit gate passes. Pull request #1 in
`VedantPatil-99/the-galaxy-frog` passed all three hosted jobs after the frontend type-check command
was made clean-runner-safe with `next typegen`.

Phase 0 is complete. Phase 1 — the transcript-first vertical slice — is implemented through P1.9 on
`feat/video-source-contract`. Its authoritative Notion page is
`P1.0 — Phase 1: Transcript-first Vertical Slice` at
<https://app.notion.com/p/3cd7942fa8e481239d63c35a8466f917>. On 2026-09-02, `bun run check`
passed with 174 backend tests, 100% statement/branch coverage, 14 frontend tests, current generated
contracts, strict type checks, and a production build. `bun run precommit` passed. Alembic applied
both Phase 1 revisions to the configured PostgreSQL database, and the opt-in integration test passed
through FastAPI, duplicate-free re-import, pgvector indexing/search, and validated citations.

The user manually installed Ollama and the configured BGE-M3/Qwen3 4B models. Live verification now
proves public-caption import, transcript rendering, duplicate-free reuse, and a grounded answer. A
Qwen3 compatibility issue was found and fixed: the provider request pins `think: false` so structured
JSON is returned in the final `response` field instead of only the thinking channel. The targeted
provider suite passes 19 tests, and the complete quality gate still passes.

On 2026-09-03, `bun run smoke:phase1` passed with 217 cues, 20 retrieval units, two validated
citations, and an idempotent re-import. The user also confirmed that clicking a citation seeks the
live YouTube player, completing P1.10 and the Phase 1 exit gate. The Ollama setup and recovery guide
is [`docs/ollama.md`](ollama.md). The matching Notion records are:

- <https://app.notion.com/p/3ce7942fa8e481ff8d79fb7fe241b2d7>
- <https://app.notion.com/p/3ce7942fa8e4815191abcf289fea5ecc>

Phase 2 started on 2026-09-05 on `feat/durable-ingestion-foundation`. P2.1 adds the
framework-independent ingestion lifecycle, an Alembic revision for `ingestion_jobs` and append-only
`job_events`, a PostgreSQL repository with idempotent creation and bounded lease operations, a
deterministic source-input fingerprint, and a resumable application stage runner.

The migration renders successfully in Alembic offline mode. On 2026-09-06, Docker Desktop and the
configured PostgreSQL service were healthy, Alembic revision `20260905_0004` was current, and
`tests/integration/test_durable_ingestion.py` passed against the real database. The gate proved
duplicate-free concurrent creation, concurrent claim exclusion, stale-lease recovery, ordered
events, cancellation, retry, and non-retryable terminal behavior. P2.1 is complete.

P2.2 is complete on 2026-09-06. A separate Python worker now polls PostgreSQL with a unique bounded
lease, drives source resolution, metadata, caption retrieval, persistence, embedding, and cleanup
from durable checkpoints, and heartbeats around provider operations. `bun run dev` includes this
worker, while `bun run dev:worker` runs it independently. The real PostgreSQL restart test proves a
replacement process resumes at metadata after source resolution was checkpointed and does not append
a duplicate source-completed event. The default backend suite passes 242 tests with three opt-in
database tests skipped and 100% statement/branch coverage; the two durable-ingestion database tests
also pass.

P2.3 is complete on 2026-09-06. `POST /v1/videos/import` now creates or reuses one durable job and
returns `202` without running provider work. FastAPI owns job detail, ordered event, retry, and cancel
schemas; committed OpenAPI and TypeScript declarations are regenerated from those schemas. The thin
frontend client waits through the Next.js proxy before loading the existing transcript view, without
implementing the later P2.8 progress controls. The real PostgreSQL FastAPI-to-worker test proves two
imports share one job, the worker completes it once, ordered event provenance is retained, and the
Phase 1 transcript, pgvector retrieval, and grounded-question paths remain readable. The default
backend suite passes 253 tests with three opt-in database tests skipped and 100% coverage; all three
database integration tests and 16 frontend tests passed at that checkpoint.

P2.4 is complete on 2026-09-06. A provider-independent `JobDispatcher` sends wake-up hints after
imports and retries have committed durable state. Local PostgreSQL polling remains the zero-credential
default. The optional QStash adapter sends only `job_id` and `requested_action`, deduplicates the
message by job/action, and translates provider failures without losing the queued job. The internal
callback verifies the exact raw body and configured destination URL with current/next signing keys,
forbids extra data, is excluded from OpenAPI/generated frontend types, and is blocked by the Next.js
proxy. Duplicate authenticated deliveries only read and acknowledge the current job projection.
The default backend suite passes 270 tests with three opt-in database tests skipped and 100% statement
and branch coverage; all three real PostgreSQL integration tests and 17 frontend tests pass. A live
hosted QStash smoke remains optional and requires user-managed credentials plus a public HTTPS
callback. The local workflow intentionally defers that hosted setup.

P2.5 is complete on 2026-09-07 on the stacked `feat/bounded-audio-acquisition` branch. Audio
requests require a durable job/attempt, canonical source, expected duration, and explicit
unavailable/unusable-caption reason. Provider-neutral interfaces separate acquisition from yt-dlp
and FFmpeg. Argument-array subprocess execution bounds diagnostics, deadlines, cancellation, and
Windows process cleanup. Deterministic job-attempt workspaces prevent cross-job file access; source
and output duration/size are both verified; normalized audio is mono 16 kHz `pcm_s16le`; and the
artifact retains `[0, duration_ms)` plus acquisition and tool-revision provenance. Failures and
cancellation clean immediately, while successful artifacts are removed after later processing by
default. The real FFmpeg 9.0.1 integration gate passed on a generated one-second fixture. The default
backend suite passed 400 tests with three database and one media opt-in test skipped and 100%
statement/branch coverage.

P2.6 provider implementation is complete on 2026-09-07 on `feat/multilingual-asr-provider`.
Python 3.14.7 compatibility was proven before locking faster-whisper 1.2.1 and CTranslate2 4.8.2.
Provider-independent request/result/error contracts retain the complete audio job, source interval,
caption-fallback reason, provider/model revision, explicit device/compute mode, detected language,
language confidence, cue confidence method, and processing time. The adapter loads the immutable
multilingual `small` model lazily, limits concurrency, enables VAD and word timestamps, and never
silently changes devices. Its opt-in live test passed on CPU `int8` with two timestamped cues from an
11-second speech fixture in 4.88 seconds. On 2026-09-10, the user installed CUDA 12.8.2 and cuDNN
9.26 and the CUDA `int8_float16` probe passed on the RTX 2050 with one bounded cue in 31.72 seconds.
The default backend suite passed 466 tests with five opt-in integrations skipped and 100% statement
and branch coverage at the P2.6 checkpoint.

P2.7 is complete on 2026-09-11 on `feat/resumable-caption-asr-fallback`. Caption retrieval now
validates the selected track's actual cues before recording `captions_unavailable` or
`captions_unusable`; only those decisions can advance into bounded audio and local ASR. Alembic
revision `20260910_0005` adds `media_assets`, `transcription_runs`, and
`transcription_run_cues`. The durable run records source/audio interval, fallback reason,
provider/model revisions, requested CUDA/compute configuration, language/confidence, processing
time, and ordered cue intervals/confidence. Final transcript cues link back to that run, while local
paths stay out of job events and FastAPI responses.

Restart behavior is proven at the actual failure boundary: a first-attempt inference failure leaves
the job at `transcription` with its audio checkpoint available; a replacement worker session reuses
that attempt-one audio, creates exactly one transcription run and final cue set, completes indexing,
then removes the file while retaining its database lineage. Canonical duplicate import remains
read-only and cannot create another output. Revision `20260910_0005` applied successfully to the
configured PostgreSQL service, and all three durable-ingestion integration tests pass. The complete
default backend gate passed 506 tests with five opt-in integrations skipped at 100% coverage before
the new database-only case was added. Current Ruff and Pyright checks, all three real PostgreSQL
durability tests, 17 frontend tests, the production build, and generated contract checks pass. A
Codex-host full-suite rerun is blocked during collection by Windows Application Control rejecting
NumPy's unsigned `_umath_linalg` binary; the same installed environment passed the user-run CUDA ASR
probe from Git Bash. Re-run `bun run check` from a fresh user Git Bash session before the P2.10 exit
gate. P2.8 progress and recovery UI is next.

Provider presentation remains intentionally split across phases. P2.8 may show read-only execution
evidence from FastAPI—actual ASR provider, model, revision, device, selection/fallback reason and
processing time—but Phase 2 does not add an interactive provider selector. Phase 8 owns the complete
automatic-local, automatic-cloud-permitted and manual selection modes, editable fallback chains,
explicit cloud-processing consent, capability/health/pricing presentation and the durable provider
decision trail. Cloud consent defaults to denied, a strict manual choice never falls back silently,
and every provider path must retain the original timestamp intervals and evidence provenance.

## Step 2 technology requirements

Frontend:

- Next.js 16 App Router
- React 19
- TypeScript strict mode
- Bun
- Tailwind CSS v4
- shadcn/ui using Base UI
- next-themes
- Phosphor icons with direct CSR/SSR imports

Backend:

- Python 3.14.7
- uv
- FastAPI
- Pydantic v2
- Distribution name `galaxy-frog` with packaged `src/galaxy_frog` import layout
- Ruff
- Pyright
- pytest
- pytest-cov
- pytest-asyncio
- HTTPX
- pre-commit

## Permanent boundaries

- Next.js is presentation-only.
- Python owns AI, retrieval, provider selection, database access,
  storage, ingestion, evaluation, and background processing.
- API contracts originate from FastAPI OpenAPI.
- Never maintain duplicate handwritten frontend/backend API types.
- Timestamps and evidence provenance must never be discarded.
- Provider calls must go through interfaces.
- Embeddings from incompatible collections must never be mixed.
- Ingestion must be resumable and idempotent.
- Expensive visual reasoning must be query-adaptive.
- New functionality requires tests.
- Do not implement future-phase features early.

## Phase 1 non-goals

Do not implement:

- Background jobs and QStash
- Audio extraction and FFmpeg
- ASR
- OCR
- VLM processing
- Visual retrieval
- FTS, RRF, and reranking
- LangGraph
- AWS deployment

## Documentation

The detailed plan and checkpoint pages are maintained in the
“Galaxy Frog Workspace” Notion database.

Local repository documentation is the coding source of truth.
Notion is the broader planning and presentation record.
