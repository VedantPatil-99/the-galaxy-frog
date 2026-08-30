# Galaxy Frog web

This workspace contains the presentation-only Next.js application. Server behavior, database access,
retrieval, and AI integrations remain in the Python backend.

## Development

From the repository root:

```bash
bun run dev:web
```

Or from `apps/web/`:

```bash
bun run dev
```

Open `http://localhost:3000`.

## FastAPI proxy

Browser requests use the same-origin `/api/proxy/[...path]` Route Handler. The handler reads the
server-only `FASTAPI_BASE_URL`, forwards an allowlisted set of headers, preserves upstream status and
correlation metadata, disables caching, and converts connection failures into a safe generated error
shape. It never accepts an upstream host from the browser.

The home-page connectivity panel checks `/health/live` and `/health/ready`. Its deliberate error
preview requests a recognizable nonexistent FastAPI path and renders the backend's real correlated
`NOT_FOUND` envelope; no failure-only backend endpoint is added.

## Generated API types

`lib/api/generated/schema.d.ts` is generated from `backend/openapi.json`; do not edit it directly.
From this directory:

```bash
bun run api:generate
bun run api:check
```

Prefer `bun run contracts:generate` at the repository root after backend contract changes because it
exports the canonical OpenAPI document before generating these declarations.

## Quality checks

From this directory:

```bash
bun run lint
bun run typecheck
bun run test
bun run build
```

`bun run check` at the repository root verifies generated contracts and runs these checks together
with the backend checks.
