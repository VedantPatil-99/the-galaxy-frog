# Galaxy Frog implementation plans

## Active plan: Phase 0 — Foundation and contracts

Duration target: 3–5 focused development days.

### Step 1 — Scope, repository, and invariants

Status: **complete**

Work packets: P0.1 repository scaffold and the foundation portion of P0.6 documentation/invariants.

- [x] Establish MVP and non-MVP boundaries.
- [x] Initialize the local git repository and modular monorepo structure.
- [x] Add `README.md`, `AGENTS.md`, `PLANS.md`, environment example, and editor rules.
- [x] Record the modular-monolith, Python-boundary, and OpenAPI-contract decisions.
- [x] Document the remaining Phase 0 sequence and acceptance checks.

### Step 2 — Frontend and backend foundations

Status: **planned**

Work packets: P0.2 Python quality tooling and P0.3 frontend design system.

- [ ] Scaffold Next.js 16, React 19, strict TypeScript, Bun, Tailwind CSS v4, and shadcn/ui with Base UI.
- [ ] Scaffold Python 3.14.7, uv, FastAPI, and Pydantic v2.
- [ ] Configure Ruff, Pyright, pytest, pre-commit, ESLint, and frontend tests.
- [ ] Add root commands that orchestrate both language workspaces without adding JavaScript server logic.

### Step 3 — Contracts and local infrastructure

Status: **planned**

Work packets: P0.4 OpenAPI generation and P0.5 Docker Compose infrastructure.

- [ ] Add local PostgreSQL with the pgvector image and a persistent development volume.
- [ ] Implement `/health/live` and dependency-aware `/health/ready`.
- [ ] Define the stable structured error envelope and correlation IDs.
- [ ] Export FastAPI OpenAPI and generate frontend TypeScript types.
- [ ] Add a thin Next.js proxy, API connectivity screen, and deliberate-error UI state.

### Step 4 — CI and exit gate

Status: **planned**

Work packets: remaining P0.5 CI and remaining P0.6 documentation/invariants.

- [ ] Add GitHub Actions for backend lint/type/test and frontend lint/type/test/build.
- [ ] Verify generated OpenAPI/types are current in CI.
- [ ] Add a local setup and troubleshooting runbook.
- [ ] Run and record the complete Phase 0 exit gate.

## Phase 0 exit gate

- [ ] `bun run build` passes.
- [ ] `uv run pytest` passes.
- [ ] FastAPI OpenAPI is generated deterministically.
- [ ] The browser calls FastAPI through the thin proxy.
- [ ] A deliberate backend error renders as a useful UI state.

## Decision log

- 2026-08-29: Project name confirmed as **Galaxy Frog: A Video RAG**.
- 2026-08-29: Use a modular monorepo with `apps/web` and `backend`.
- 2026-08-29: Keep all server and AI behavior in Python; TypeScript is presentation-only.
- 2026-08-29: Execute Phase 0 as four reviewable checkpoints and stop after each checkpoint for review.
