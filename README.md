# Galaxy Frog: A Video RAG

Galaxy Frog is a YouTube-first temporal multimodal retrieval system. Its flagship capability will be cross-modal temporal retrieval with timestamp-grounded answers: the product should explain what was said, what was shown, and when it occurred.

## Current checkpoint

Phase 0 — Foundation and contracts, Step 2 of 4.

Steps 2A, 2B, and 2C are complete: the Next.js presentation shell, Base UI design
foundation, packaged Python workspace, FastAPI application boundary, and Python quality tooling are
established. Step 2D adds root orchestration commands and completes local verification next.

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

## Git Bash quick check

```bash
git status --short
sed -n '1,220p' PLANS.md
find docs/adr -maxdepth 1 -type f -print
```

Frontend and backend verification commands are available now. Root commands that orchestrate both
workspaces are deferred to Step 2D.
