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
- No Phase 2+ worker, audio, ASR, OCR, visual, reranking, or orchestration work has started
- FastAPI now exposes caption-only import, video detail, transcript, and grounded-question contracts
- YouTube metadata/captions remain download-free; transcript cues and retrieval units retain exact
  millisecond intervals and ordered cue provenance
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
- Docker Engine: `29.7.2`
- Docker Compose: `v5.4.0`
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
