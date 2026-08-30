# Python backend

This directory contains Galaxy Frog's FastAPI application boundary. Later server-side domain,
data, background-job, and AI capabilities remain Python-owned and must keep provider SDKs behind
typed adapters.

## Development

From `backend/`:

```bash
uv sync
uv run fastapi dev
```

The development API runs at `http://127.0.0.1:8000`, with interactive documentation at
`http://127.0.0.1:8000/docs`.

## Quality checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

The repository-level pre-commit hooks run the Ruff and Pyright checks for backend changes.
