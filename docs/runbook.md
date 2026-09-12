# Galaxy Frog local runbook

This runbook covers the local Next.js, FastAPI, PostgreSQL/pgvector, Phase 1 transcript-first stack,
and the Phase 2 durable-ingestion and bounded-audio foundations. Run commands from the repository
root in Git Bash unless a section says otherwise.

## Prerequisites

- Git `2.55.0.windows.5`
- Bun `1.4.0`
- Python `3.14.7`
- uv `0.12.7`
- FFmpeg and ffprobe `9.0.1`
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

Start the three application processes (Next.js, FastAPI, and the durable ingestion worker):

```bash
bun run dev
```

Run the worker independently when diagnosing durable jobs:

```bash
bun run dev:worker
```

`INGESTION_LEASE_SECONDS` and `INGESTION_POLL_SECONDS` control the local lease and idle polling
intervals. Leave `INGESTION_WORKER_ID` unset for a hostname/process-derived identity, or set a unique
value per process. The public import returns a durable job immediately. Keep one worker running to
process queued jobs; stopping it leaves work safely queued until a worker starts again.

Phase 1 uses user-managed Ollama models. Install and start Ollama manually, then provision the exact
models named in `.env`; these commands download model weights and therefore are never run by Codex:

```bash
ollama pull bge-m3
ollama pull qwen3:4b
```

See [`docs/ollama.md`](ollama.md) for Windows installation, GPU verification, model checks, and the
provider troubleshooting workflow.

The development endpoints are:

- Web application: `http://localhost:3000`
- FastAPI documentation: `http://127.0.0.1:8000/docs`
- FastAPI liveness: `http://127.0.0.1:8000/health/live`
- FastAPI readiness: `http://127.0.0.1:8000/health/ready`
- Browser-to-FastAPI proxy: `http://localhost:3000/api/proxy/health/live`
- Durable video import: `POST http://127.0.0.1:8000/v1/videos/import`
- Job detail and events: `GET http://127.0.0.1:8000/v1/jobs/{job_id}` and
  `GET http://127.0.0.1:8000/v1/jobs/{job_id}/events`
- Job actions: `POST http://127.0.0.1:8000/v1/jobs/{job_id}/retry` and
  `POST http://127.0.0.1:8000/v1/jobs/{job_id}/cancel`

Use `Ctrl+C` to stop the application processes. Stop the local database separately when desired:

```bash
bun run infra:down
```

`infra:down` preserves the named database volume. Do not add `--volumes` unless intentionally
discarding all local database data.

## Dispatch modes

`JOB_DISPATCHER=local` is the default and requires no hosted service or credential. Imports and
retries commit durable PostgreSQL state, then the local adapter acknowledges the job already visible
to `bun run dev:worker` polling.

QStash is optional. To exercise hosted delivery, create the QStash resource manually and add these
values only to the untracked `.env` file:

```bash
JOB_DISPATCHER=qstash
QSTASH_TOKEN=replace-with-local-secret
QSTASH_CALLBACK_URL=https://your-public-host/internal/qstash/dispatch
QSTASH_CURRENT_SIGNING_KEY=replace-with-local-secret
QSTASH_NEXT_SIGNING_KEY=replace-with-local-secret
```

The callback URL must be the exact public URL configured as the QStash destination because signature
verification binds the message subject to it. QStash sends only `job_id` and `requested_action`.
`/internal/qstash/dispatch` is deliberately absent from OpenAPI and the generated frontend types,
and the Next.js proxy returns `404` for every `/internal/*` path. Keep FastAPI and the persistent
worker running; the callback acknowledges the durable pointer quickly while the worker claims from
PostgreSQL. Never place QStash tokens or signing keys in source control.

The current local workflow intentionally keeps `JOB_DISPATCHER=local`; hosted QStash setup is
deferred to a later phase and is not required for audio acquisition.

## Bounded local audio tools

FFmpeg and ffprobe are user-managed machine tools. Verify them from Git Bash after installation or
after opening a new terminal so it receives the updated `PATH`:

```bash
command -v ffmpeg
command -v ffprobe
ffmpeg -version | head -n 1
ffprobe -version | head -n 1
```

P2.5 uses the locked Python yt-dlp package to download only a single best-audio stream, then uses
ffprobe and FFmpeg to verify and normalize it to mono 16 kHz PCM WAV. The default local limits are:

- `MEDIA_WORKSPACE_ROOT=tmp/media`
- `MEDIA_MAX_DURATION_SECONDS=7200`
- `MEDIA_MAX_DOWNLOAD_BYTES=268435456`
- `MEDIA_MAX_OUTPUT_BYTES=268435456`
- `MEDIA_TIMEOUT_SECONDS=600`
- `MEDIA_MAX_CONCURRENCY=1`
- `MEDIA_RETAIN_ON_SUCCESS=false`

Every attempt is isolated under `tmp/media/{job_id}/attempt-{attempt}`. Failed and cancelled
attempts clean that directory immediately. Successful audio remains available for processing and is
removed when the later transcription path calls cleanup; set retention to `true` only for deliberate
local diagnosis. The provider records the canonical source, unavailable/unusable-caption reason,
the half-open `[0, duration_ms)` interval, acquisition time, and yt-dlp/FFmpeg revisions.

P2.5 does not yet send caption failures into audio or ASR. No media process needs to stay active;
P2.6 adds the transcription provider and P2.7 activates the durable fallback transition.

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

## Verification and smoke tests

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

Run the real migration, durable FastAPI-to-worker flow, idempotency, pgvector, and HTTP integration
test explicitly:

```bash
RUN_DATABASE_INTEGRATION=1 uv run --directory backend pytest --no-cov tests/integration/test_phase_one_slice.py
```

After applying the Phase 2 migration, prove real PostgreSQL idempotency, concurrent claim exclusion,
stale-lease recovery, persisted-stage restart resume, ordered events, cancellation, and retry rules:

```bash
RUN_DATABASE_INTEGRATION=1 uv run --directory backend pytest --no-cov tests/integration/test_durable_ingestion.py
```

Prove the installed local FFmpeg toolchain without network access or a running application:

```bash
export RUN_MEDIA_INTEGRATION=1
export FFMPEG_EXECUTABLE="$(command -v ffmpeg)"
export FFPROBE_EXECUTABLE="$(command -v ffprobe)"
uv run --directory backend pytest --no-cov tests/integration/test_media_tools.py
```

The test generates a one-second stereo/48 kHz fixture in pytest temporary storage, normalizes it
through the production adapter, re-probes mono/16 kHz PCM output, and verifies the exact duration.

These tests create uniquely identified jobs and delete them when they finish. Do not complete a
durable-ingestion checkpoint based only on unit tests or an offline migration render.

Targeted backend tests must disable the repository-wide coverage gate; use the complete suite to
prove 100% coverage:

```bash
uv run --directory backend pytest --no-cov tests/unit/providers/test_ollama.py
bun run test:backend
```

After selecting and approving one stable public captioned video, run the Phase 1 exit smoke through
the browser-facing proxy. The question must be answerable from that video's captions:

```bash
export PHASE1_VIDEO_URL='https://www.youtube.com/watch?v=APPROVED_VIDEO_ID'
export PHASE1_QUESTION='What specific claim does the speaker make?'
bun run smoke:phase1
```

The Phase 1 runner requires a non-empty transcript, at least one valid timestamp citation, matching
video ownership, ordered cue provenance, and an idempotent second import. Citation-to-player seeking
is verified by the frontend player-command test and should also be clicked once during the live UI
smoke.

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

### Ollama models are missing or grounded generation returns 503

Confirm that the user-managed service is running and both exact model tags are present:

```bash
curl --fail-with-body http://127.0.0.1:11434/api/tags
ollama list
```

If a model pull ends with `unexpected EOF`, retry the same command so reusable layers resume. If
both models are present, restart `bun run dev` so FastAPI loads the current adapter. Galaxy Frog pins
`think: false` for `qwen3:4b`; without it, current Ollama versions can put structured output in the
thinking channel and leave the final response empty. See [`docs/ollama.md`](ollama.md) for the full
diagnostic sequence.

### `bun run smoke` tries to start WSL Bash

The supported manual shell is Git Bash. Do not run the smoke command from Command Prompt. In Git
Bash, run:

```bash
./scripts/smoke-test.sh
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

### Windows Application Control blocks Python or Next.js modules

If Python reports that `_sqlite3`, `select`, or another standard-library DLL was blocked, or Next.js
cannot load its native SWC module, stop and repair/allow the managed runtime through the machine's
Application Control policy. Do not disable coverage, substitute generated contracts, or install a
different runtime to claim the exit gate. Temporary diagnostic shims are not part of the supported
development workflow.

## Deployment boundary

The local Phase 1 stack uses PostgreSQL with pgvector in Docker and a user-managed native Ollama
service. Hosted Supabase and cloud generation remain later deployment decisions. FastAPI remains
the only database and provider owner in either environment, and existing portable migrations are
the path to a hosted PostgreSQL service.
