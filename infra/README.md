# Infrastructure

The root `compose.yaml` provides the local PostgreSQL 17 development service using the pinned
`pgvector/pgvector:0.8.6-pg17` image. PostgreSQL is exposed only on the loopback interface and uses
a named volume so local data survives normal container replacement.

## Local database

On Windows, install WSL 2 and Docker Desktop before running the service. From an elevated Git Bash
or Windows Terminal session:

```bash
winget install --exact --id Microsoft.WSL --source winget
wsl.exe --install --no-distribution
```

Restart Windows if requested, then install and launch Docker Desktop:

```bash
winget install --exact --id Docker.DockerDesktop --source winget
```

Keep Docker Desktop on its WSL 2 backend and verify `docker --version` and
`docker compose version` before continuing.

Copy `.env.example` to `.env`, replace the sample PostgreSQL password, and keep the values in
`POSTGRES_*` synchronized with `DATABASE_URL`. Then run from the repository root:

```bash
bun run infra:up
bun run db:migrate
```

Inspect or stop the service with:

```bash
bun run infra:logs
bun run infra:down
```

The initial Alembic migration enables pgvector in the `extensions` schema. Application tables and
vector indexes are intentionally deferred to later phases.

AWS and hosted Supabase infrastructure remain deferred until the local architecture and costs have
been measured.
