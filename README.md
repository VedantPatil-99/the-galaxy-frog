# Galaxy Frog: A Video RAG

Galaxy Frog is a YouTube-first temporal multimodal retrieval system. Its flagship capability will be cross-modal temporal retrieval with timestamp-grounded answers: the product should explain what was said, what was shown, and when it occurred.

## Current checkpoint

Phase 0 — Foundation and contracts — is complete. Phase 1 has not started.

Steps 2A–2D are complete: the Next.js presentation shell, Base UI design foundation, packaged
Python workspace, FastAPI application boundary, quality tooling, and root Bun orchestration are
established. Steps 3A–3D established local PostgreSQL/pgvector, operational API contracts,
deterministic OpenAPI-derived frontend types, and verified browser-to-FastAPI connectivity through
the thin Next.js proxy. Step 4 added reproducible GitHub Actions, the local runbook, and the final
Phase 0 exit gate without introducing Phase 1 behavior. The first hosted contract, backend, and
frontend jobs passed on pull request #1 in `VedantPatil-99/the-galaxy-frog`.

## Architecture direction

- `apps/web`: presentation-only Next.js/React/TypeScript application.
- `backend`: all server-side application, data, and AI logic in Python.
- FastAPI owns the API contract and exports OpenAPI.
- The web application consumes generated TypeScript types and reaches FastAPI through a thin proxy.
- PostgreSQL is the system of record; the pgvector extension is available locally while vector
  tables, indexes, and retrieval remain deferred to later phases.
- Provider-specific integrations stay behind Python interfaces and configuration.

See [docs/architecture.md](docs/architecture.md), [docs/mvp-scope.md](docs/mvp-scope.md), and [PLANS.md](PLANS.md).
For setup, daily commands, and recovery procedures, see [docs/runbook.md](docs/runbook.md).

## Phase 0 checkpoints

1. Scope, repository, and invariants.
2. Frontend and backend foundations.
3. API contracts and local infrastructure.
4. CI, verification, and the Phase 0 exit gate.

## Local setup

```bash
bun run sync
```

This installs the frontend from the root `bun.lock` and synchronizes the backend from
`backend/uv.lock`.

## Development

After Docker Desktop is installed, prepare the local database:

```bash
cp .env.example .env
bun run infra:up
bun run db:migrate
```

Replace the sample database password in `.env` before starting the service.

Start the Next.js and FastAPI development servers together:

```bash
bun run dev
```

- Web: `http://localhost:3000`
- FastAPI docs: `http://127.0.0.1:8000/docs`
- OpenAPI: `http://127.0.0.1:8000/openapi.json`
- Liveness: `http://127.0.0.1:8000/health/live`
- Readiness: `http://127.0.0.1:8000/health/ready`
- Browser proxy: `http://localhost:3000/api/proxy/health/live`

Use `Ctrl+C` to stop both processes. Run either process independently with `bun run dev:web` or
`bun run dev:api`. `FASTAPI_BASE_URL` is read only by the Next.js server; it is never exposed as a
`NEXT_PUBLIC_` browser variable.

## API contracts

```bash
bun run contracts:generate
bun run contracts:check
```

Generation exports FastAPI's canonical schema to `backend/openapi.json`, then derives
`apps/web/lib/api/generated/schema.d.ts` with `openapi-typescript`. Neither command requires a
running API or database. Commit both generated artifacts with every API contract change.

## Verification

```bash
bun run check
bun run precommit
```

`bun run check` first verifies that both generated API artifacts are current, then runs frontend and
backend linting, formatting checks, strict type checks, tests, coverage enforcement, and the frontend
production build. `bun run precommit` executes all configured repository hooks.

With the local stack running, use `bun run smoke` from Git Bash to verify live browser-to-FastAPI
connectivity, PostgreSQL-backed readiness, and the structured deliberate-error response.
