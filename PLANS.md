# Galaxy Frog implementation plans

## Completed plan: Phase 0 — Foundation and contracts

Duration target: 3–5 focused development days.

Status: **complete**. Phase 1 has not started.

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
  passed. Phase 1 remains unstarted.
