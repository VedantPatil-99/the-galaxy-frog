# Galaxy Frog: A Video RAG

Galaxy Frog is a YouTube-first temporal multimodal retrieval system. Its flagship capability will be cross-modal temporal retrieval with timestamp-grounded answers: the product should explain what was said, what was shown, and when it occurred.

## Current checkpoint

Phase 0 — Foundation and contracts, Step 2 of 4 complete.

Steps 2A–2D are complete: the Next.js presentation shell, Base UI design foundation, packaged
Python workspace, FastAPI application boundary, quality tooling, and root Bun orchestration are
established. Step 3 adds API contracts and local infrastructure after review.

## Architecture direction

- `apps/web`: presentation-only Next.js/React/TypeScript application.
- `backend`: all server-side application, data, and AI logic in Python.
- FastAPI owns the API contract and exports OpenAPI.
- The web application consumes generated TypeScript types and reaches FastAPI through a thin proxy.
- PostgreSQL is the system of record; pgvector is added during the later retrieval phase.
- Provider-specific integrations stay behind Python interfaces and configuration.

See [docs/architecture.md](docs/architecture.md), [docs/mvp-scope.md](docs/mvp-scope.md), and [PLANS.md](PLANS.md).

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

Start the Next.js and FastAPI development servers together:

```bash
bun run dev
```

- Web: `http://localhost:3000`
- FastAPI docs: `http://127.0.0.1:8000/docs`
- OpenAPI: `http://127.0.0.1:8000/openapi.json`

Use `Ctrl+C` to stop both processes. Run either process independently with `bun run dev:web` or
`bun run dev:api`.

## Verification

```bash
bun run check
bun run precommit
```

`bun run check` runs frontend and backend linting, formatting checks, strict type checks, tests,
coverage enforcement, and the frontend production build. `bun run precommit` executes all configured
repository hooks.
