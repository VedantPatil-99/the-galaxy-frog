# Galaxy Frog Project Handoff

## Project

Galaxy Frog: A Video RAG

Galaxy Frog is a provider-independent temporal multimodal retrieval
system for YouTube videos. It will retrieve transcript, OCR, and visual
evidence and produce timestamp-grounded answers.

## Current status

- Phase 0 — Foundation and Contracts
- Step 1 — Scope, repository, and invariants: completed
- Step 2 — Frontend and backend foundations: not started
- No Next.js application has been scaffolded
- No FastAPI application has been scaffolded
- No database, retrieval, ingestion, OCR, ASR, or AI provider work has started

## Verified local tools

- Git: 2.46.2.windows.1
- Bun: 1.3.9
- Python: 3.13.2
- uv: 0.12.3
- Docker: not installed
- Operating environment: Windows PowerShell

Docker is not required for Step 2. It will be installed before Step 3.

## Current task

Implement Phase 0 Step 2 in four reviewed batches:

1. Step 2A — Scaffold Next.js and Python workspaces
2. Step 2B — Configure the frontend design foundation
3. Step 2C — Configure FastAPI and Python quality tooling
4. Step 2D — Add root commands and verify both workspaces

Do not begin Step 3 until Step 2 acceptance criteria pass.

## Step 2 technology requirements

Frontend:

- Next.js 16 App Router
- React 19
- TypeScript strict mode
- Bun
- Tailwind CSS v4
- shadcn/ui using Base UI
- next-themes
- Lucide and Phosphor icons

Backend:

- Python 3.13
- uv
- FastAPI
- Pydantic v2
- Packaged `src/galaxy-frog` layout
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