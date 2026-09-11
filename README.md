# Galaxy Frog: A Video RAG

Galaxy Frog is a YouTube-first temporal multimodal retrieval system. Its flagship capability will be cross-modal temporal retrieval with timestamp-grounded answers: the product should explain what was said, what was shown, and when it occurred.

## Current checkpoint

Phase 0 — Foundation and contracts — and Phase 1 — Transcript-first vertical slice — are complete.
Phase 2 — Durable ingestion and ASR fallback — is in progress on
`feat/resumable-caption-asr-fallback`.

Steps 2A–2D are complete: the Next.js presentation shell, Base UI design foundation, packaged
Python workspace, FastAPI application boundary, quality tooling, and root Bun orchestration are
established. Steps 3A–3D established local PostgreSQL/pgvector, operational API contracts,
deterministic OpenAPI-derived frontend types, and verified browser-to-FastAPI connectivity through
the thin Next.js proxy. Step 4 added reproducible GitHub Actions, the local runbook, and the final
Phase 0 exit gate. Phase 1 imports captioned public YouTube videos, preserves cue-to-segment
provenance, performs video-scoped dense retrieval, generates validated transcript citations, and
seeks the player from evidence intervals. The live exit gate verified import, transcript rendering,
duplicate-free reuse, grounded answering, validated timestamp citations, and citation-to-player
seeking.

The first seven Phase 2 packets add framework-independent ingestion job and event models, PostgreSQL
tables and repository operations, lease-safe claims and heartbeats, deterministic idempotency
fingerprints, cancellation/retry transitions, a standalone local worker, and resumable caption-first
stage handlers. Real PostgreSQL gates prove concurrent claim exclusion, stale-lease recovery, and
restart from the last completed stage. FastAPI import now returns a durable job promptly, exposes its
current state and ordered events, and keeps retry/cancel schemas generated into the frontend client.
Provider-neutral wake-up hints use local PostgreSQL polling by default; the optional QStash adapter
sends identifier-only messages to a signature-verified internal callback that is neither exported in
OpenAPI nor reachable through the browser proxy.

P2.5–P2.7 add bounded audio acquisition and local multilingual faster-whisper behind Python
interfaces, then activate ASR only after the selected caption track is unavailable or unusable.
PostgreSQL checkpoints the media identity, one complete transcription run, and its ordered exact
millisecond cues before final transcript persistence. Restart reuses durable outputs without
duplicating work; successful cleanup removes the temporary file while preserving database lineage.
FastAPI exposes safe provider/model/revision/device/language/confidence/timing/fallback evidence
through generated frontend declarations and never exposes the local media path.

## Architecture direction

- `apps/web`: presentation-only Next.js/React/TypeScript application.
- `backend`: all server-side application, data, and AI logic in Python.
- FastAPI owns the API contract and exports OpenAPI.
- The web application consumes generated TypeScript types and reaches FastAPI through a thin proxy.
- PostgreSQL is the system of record; Phase 1 stores versioned BGE-M3 vectors in pgvector and runs
  video-scoped cosine retrieval without later-phase hybrid search or reranking.
- Phase 2 stores the durable ingestion projection and append-only event history in PostgreSQL;
  workers claim bounded leases and resume only from persisted stage checkpoints.
- Local dispatch is the default; optional QStash delivery carries no media, transcript, prompt, or
  evidence data and never becomes a second source of truth.
- Audio acquisition is local, bounded by typed duration/size/deadline/concurrency settings, and
  retains every artifact's source interval plus yt-dlp/FFmpeg revision provenance.
- Local ASR uses an immutable multilingual `small` model and persists its exact execution and cue
  provenance before final transcript indexing.
- Provider-specific integrations stay behind Python interfaces and configuration.

See [docs/architecture.md](docs/architecture.md), [docs/mvp-scope.md](docs/mvp-scope.md), and [PLANS.md](PLANS.md).
For setup, daily commands, and recovery procedures, see [docs/runbook.md](docs/runbook.md). For the
manual local AI setup, see [docs/ollama.md](docs/ollama.md).

## Phase 0 checkpoints

1. Scope, repository, and invariants.
2. Frontend and backend foundations.
3. API contracts and local infrastructure.
4. CI, verification, and the Phase 0 exit gate.

## Local setup

```bash
bun run sync
```

This installs the frontend from the root `bun.lock` and synchronizes the backend from
`backend/uv.lock`.

## Development

After Docker Desktop is installed, prepare the local database:

```bash
cp .env.example .env
bun run infra:up
bun run db:migrate
```

Replace the sample database password in `.env` before starting the service.

Start the Next.js, FastAPI, and durable ingestion worker processes together:

```bash
bun run dev
```

- Web: `http://localhost:3000`
- FastAPI docs: `http://127.0.0.1:8000/docs`
- OpenAPI: `http://127.0.0.1:8000/openapi.json`
- Liveness: `http://127.0.0.1:8000/health/live`
- Readiness: `http://127.0.0.1:8000/health/ready`
- Browser proxy: `http://localhost:3000/api/proxy/health/live`
- Video import: `POST http://127.0.0.1:8000/v1/videos/import`
- Job detail: `GET http://127.0.0.1:8000/v1/jobs/{job_id}`
- Job events: `GET http://127.0.0.1:8000/v1/jobs/{job_id}/events`
- Job retry: `POST http://127.0.0.1:8000/v1/jobs/{job_id}/retry`
- Job cancellation: `POST http://127.0.0.1:8000/v1/jobs/{job_id}/cancel`
- Transcript: `GET http://127.0.0.1:8000/v1/videos/{video_id}/transcript`
- Grounded question: `POST http://127.0.0.1:8000/v1/videos/{video_id}/questions`

Use `Ctrl+C` to stop all processes. Run a process independently with `bun run dev:web`,
`bun run dev:api`, or `bun run dev:worker`. `FASTAPI_BASE_URL` is read only by the Next.js server; it
is never exposed as a `NEXT_PUBLIC_` browser variable. Keep the worker running while importing;
otherwise jobs remain safely queued in PostgreSQL until a worker starts.

## API contracts

```bash
bun run contracts:generate
bun run contracts:check
```

Generation exports FastAPI's canonical schema to `backend/openapi.json`, then derives
`apps/web/lib/api/generated/schema.d.ts` with `openapi-typescript`. Neither command requires a
running API or database. Commit both generated artifacts with every API contract change.

## Verification

```bash
bun run check
bun run precommit
```

`bun run check` first verifies that both generated API artifacts are current, then runs frontend and
backend linting, formatting checks, strict type checks, tests, coverage enforcement, and the frontend
production build. `bun run precommit` executes all configured repository hooks.

With the local stack running, use `bun run smoke` from Git Bash to verify live browser-to-FastAPI
connectivity, PostgreSQL-backed readiness, and the structured deliberate-error response. For the
Phase 1 transcript-first exit gate, select an approved public captioned video and run:

```bash
export PHASE1_VIDEO_URL='https://www.youtube.com/watch?v=APPROVED_VIDEO_ID'
export PHASE1_QUESTION='What specific claim does the speaker make?'
bun run smoke:phase1
```
