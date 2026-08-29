# Galaxy Frog: A Video RAG

Galaxy Frog is a YouTube-first temporal multimodal retrieval system. Its flagship capability will be cross-modal temporal retrieval with timestamp-grounded answers: the product should explain what was said, what was shown, and when it occurred.

## Current checkpoint

Phase 0 — Foundation and contracts, Step 1 of 4.

This checkpoint intentionally contains repository governance, scope, architectural decisions, and directory contracts only. Framework applications and executable services are introduced in Step 2.

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

## PowerShell quick check

```powershell
Set-Location .\galaxy-frog
git status --short
Get-Content .\PLANS.md
Get-ChildItem -Recurse -File | Select-Object FullName
```

Do not run application build commands yet; application scaffolds are a Step 2 deliverable.
