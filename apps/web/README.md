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

## Quality checks

From this directory:

```bash
bun run lint
bun run typecheck
bun run test
bun run build
```

`bun run check` at the repository root runs these checks together with the backend checks.
