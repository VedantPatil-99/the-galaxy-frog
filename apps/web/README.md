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
