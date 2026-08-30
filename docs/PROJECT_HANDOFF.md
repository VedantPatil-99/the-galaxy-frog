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
- The Next.js presentation shell uses React 19, strict TypeScript,
  Tailwind CSS v4, shadcn/ui with Base UI, and system-aware themes
- The FastAPI application factory, typed settings, CLI entrypoint, and starter tests are established
- Root Bun commands synchronize, run, and verify both language workspaces without JavaScript server logic
- The Next.js page and FastAPI documentation were verified together through `bun run dev`
- PostgreSQL 17 and pgvector 0.8.6 run locally through Docker Compose with a persistent named volume
- Alembic revision `20260830_0001` enables pgvector in the `extensions` schema
- FastAPI exposes dependency-independent `/health/live` and PostgreSQL-aware `/health/ready`
- All API failures use one correlated, human-safe error envelope
- No application schema, retrieval, ingestion, OCR, ASR, or AI provider work has started

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

## Current task

Phase 0 Step 2 is complete in four reviewed batches:

1. Step 2A — Scaffold Next.js and Python workspaces: completed
2. Step 2B — Configure the frontend design foundation: completed
3. Step 2C — Configure FastAPI and Python quality tooling: completed
4. Step 2D — Add root commands and verify both workspaces: completed

Step 3 — API contracts and local infrastructure — is active. Step 3A is complete: PostgreSQL 17.11
became healthy, Alembic applied revision `20260830_0001`, pgvector 0.8.6 was verified in the
`extensions` schema, and both states survived container recreation through the named volume.
Step 3B is complete: live verification proved liveness stays `200` while PostgreSQL is stopped,
readiness changes from `200` to a correlated `503`, and readiness recovers after PostgreSQL becomes
healthy. Step 3C — deterministic OpenAPI export and generated frontend types — is next.

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

## Step 2 non-goals

Do not implement:

- PostgreSQL or pgvector
- Docker Compose
- Supabase
- YouTube ingestion
- FFmpeg or yt-dlp
- Embeddings
- Retrieval
- LangGraph
- ASR
- OCR
- VLM processing
- Real provider API calls

## Documentation

The detailed plan and checkpoint pages are maintained in the
“Galaxy Frog Workspace” Notion database.

Local repository documentation is the coding source of truth.
Notion is the broader planning and presentation record.
