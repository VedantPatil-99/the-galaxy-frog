# Galaxy Frog local runbook

This runbook covers the Phase 0 development stack: Next.js, FastAPI, and local PostgreSQL with
pgvector. Run commands from the repository root in Git Bash unless a section says otherwise.

## Prerequisites

- Git `2.55.0.windows.5`
- Bun `1.4.0`
- Python `3.14.7`
- uv `0.12.7`
- WSL 2 and Docker Desktop using the WSL 2 backend

PostgreSQL does not need to be installed directly on Windows. Docker Compose runs the pinned
PostgreSQL 17 and pgvector image defined in `compose.yaml`.

## First-time setup

Create the ignored local environment file and replace the sample password with a new local-only
password. Keep `POSTGRES_PASSWORD` and the password inside `DATABASE_URL` identical.

```bash
cp .env.example .env
```

Install the exact dependencies recorded in `bun.lock` and `backend/uv.lock`:

```bash
bun run sync
```

Start Docker Desktop manually and wait until its engine reports that it is running. Then start the
database and apply every migration:

```bash
bun run infra:up
bun run db:migrate
```

## Daily development

Start both application processes:

```bash
bun run dev
```

The development endpoints are:

- Web application: `http://localhost:3000`
- FastAPI documentation: `http://127.0.0.1:8000/docs`
- FastAPI liveness: `http://127.0.0.1:8000/health/live`
- FastAPI readiness: `http://127.0.0.1:8000/health/ready`
- Browser-to-FastAPI proxy: `http://localhost:3000/api/proxy/health/live`

Use `Ctrl+C` to stop the application processes. Stop the local database separately when desired:

```bash
bun run infra:down
```

`infra:down` preserves the named database volume. Do not add `--volumes` unless intentionally
discarding all local database data.

## API contract workflow

FastAPI is the source of truth for HTTP schemas. After an intentional API schema change, regenerate
and commit both derived artifacts:

```bash
bun run contracts:generate
```

This updates `backend/openapi.json` and `apps/web/lib/api/generated/schema.d.ts`. Check that neither
artifact is stale with:

```bash
bun run contracts:check
```

Never hand-edit the generated files.

## Verification and Phase 0 smoke test

Run the complete static and automated test gate:

```bash
bun run check
bun run precommit
```

With Docker Desktop, PostgreSQL, Next.js, and FastAPI running, verify the browser-to-FastAPI path:

```bash
bash scripts/smoke-test.sh
```

The smoke test requires liveness and readiness to return `200`, then confirms that a missing backend
route travels through the proxy as the structured `404 NOT_FOUND` response with a correlation ID.
Set `WEB_BASE_URL` only when the web application intentionally uses a different local address.

## Troubleshooting

### Docker commands cannot connect to the engine

Start Docker Desktop manually and wait for the engine to become ready. `docker --version` only
verifies the CLI installation; it does not prove that the Docker engine is running. Confirm the
engine before retrying:

```bash
docker info
docker compose ps
```

### PostgreSQL is unhealthy or readiness returns 503

Inspect the container and its recent logs:

```bash
docker compose ps
docker compose logs postgres
```

Confirm that `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` in `.env` match `DATABASE_URL`.
Changing `.env` after the named volume is initialized does not change the existing PostgreSQL role's
password. Restore the original local password or deliberately update that role; do not delete the
volume as a routine fix.

After PostgreSQL is healthy, apply migrations and retry readiness:

```bash
bun run db:migrate
curl --fail-with-body http://127.0.0.1:8000/health/ready
```

### A port is already in use

The defaults are web `3000`, API `8000`, and PostgreSQL `5432`. Stop the conflicting local process or
set the corresponding local environment value before startup. Keep `DATABASE_URL` synchronized if
`POSTGRES_PORT` changes.

### The proxy returns 502 or 504

The Next.js server could not reach FastAPI. Confirm that FastAPI is running and that the server-only
`FASTAPI_BASE_URL` points to it. Do not rename this value to a `NEXT_PUBLIC_` variable and do not call
PostgreSQL directly from Next.js.

### Generated-contract verification fails

If the API change was intentional, run `bun run contracts:generate`, review both generated files,
and commit them with the source schema change. If it was not intentional, inspect the FastAPI schema
change instead of accepting generated drift.

### A locked dependency install fails

Run the non-mutating version checks first:

```bash
bun --version
python --version
uv --version
```

Use Bun `1.4.0`, Python `3.14.7`, and uv `0.12.7`. `bun ci` and `uv sync --locked` intentionally fail
when a manifest and lockfile disagree; update dependencies and lockfiles as a separate reviewed
change rather than bypassing the frozen install.

## Deployment boundary

Local Phase 0 uses PostgreSQL with pgvector in Docker. Hosted Supabase remains a later deployment
choice. FastAPI remains the only database owner in either environment, and existing portable
migrations are the path to a hosted PostgreSQL service.
