# Python backend

This directory contains Galaxy Frog's FastAPI application boundary. Later server-side domain,
data, background-job, and AI capabilities remain Python-owned and must keep provider SDKs behind
typed adapters.

## Development

From `backend/`:

```bash
uv sync --locked
uv run fastapi dev
```

The development API runs at `http://127.0.0.1:8000`, with interactive documentation at
`http://127.0.0.1:8000/docs`.

Operational endpoints:

- `GET /health/live` reports process liveness without consulting PostgreSQL.
- `GET /health/ready` executes a real PostgreSQL query and returns `503` when the dependency is
  missing or unavailable.

Every response includes `X-Correlation-ID`. API failures use the canonical `ErrorResponse` envelope
with a stable code, safe message, matching correlation ID, retryability, optional suggested action,
and optional JSON details. Framework debug tracebacks remain disabled in every environment so an
unexpected exception cannot replace the public error contract with internal details.

## Quality checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

The repository-level pre-commit hooks run the Ruff and Pyright checks for backend changes.

## Database migrations

Start PostgreSQL from the repository root, then apply migrations from `backend/`:

```bash
uv run alembic upgrade head
```

Alembic reads `DATABASE_URL` from the environment or the local `.env` file and never stores the
database password in `alembic.ini`. The initial migration enables pgvector only; no application
tables are introduced in Phase 0 Step 3A.

From the repository root, use `bun run dev:api` to start this process or `bun run check` to run the
complete frontend and backend quality gate.
