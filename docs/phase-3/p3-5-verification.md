# P3.5 trace and API verification

Local acceptance on `feat/retrieval-traces-api`, based on P3.4 `97a3a0b`, completed 2026-09-28.
Hosted CI/merge and browser/model-quality acceptance are separate gates. No PR is created automatically.

## Behavior and results

- Migration `20260927_0007` adds video-scoped version-1 trace records. Python and PostgreSQL enforce
  the 256 KiB serialized limit; a foreign key cascades trace deletion with its parent video.
- Search and questions share the same temporal retrieval service. Requests accept `mode` (`lexical`,
  `dense`, `hybrid`, `reranked`), `limit` (1–8), optional `selected_anchor`, and `allow_fallback`.
  Default mode is `reranked`; fallback is visible and can be disabled explicitly.
- `POST /v1/videos/{video_id}/search` never constructs a generator. Search returns original evidence,
  merged display windows, status, anchor choices, warnings, and trace identity.
- `GET /v1/videos/{video_id}/retrieval-traces/{trace_id}` rejects unknown/foreign traces with 404.
- Questions retain answer/confidence/evidence/warnings/degraded-mode fields and add retrieval metadata
  and status. Unresolved/ambiguous anchors return an empty answer without generation. Original citation
  validation remains; temporal quotes must also occur in cues overlapping the requested scope.
- Traces contain configuration, provider identities, stage/fusion/reranker records, timings, fallback
  reasons, anchor decisions, and original unit/cue/ASR lineage. Stage/group copies omit transcript
  text; records are bounded rather than silently truncated. Queries and bounded anchor snippets are
  retained. No automatic age-based deletion is configured.
- Known retrieval failures return a trace ID. Generation/citation failures include the already saved
  retrieval trace ID. A database/trace-storage failure cannot return a successful durable result.
- One app-scoped reranker instance enforces concurrency. Proxy search/question deadlines are five
  minutes; backend provider deadlines still apply. Overrides above that can outlast the proxy.
- Full `bun run check`: 664 backend tests, 14 opt-in skips, 100% statement/branch coverage, 27 frontend
  tests, generated contract checks, lint/format/types, and production build passed.
- Ten PostgreSQL integration checks passed, including real HTTP persistence across sessions,
  cross-video reads, database size/version constraints, cascade cleanup, and ingestion regressions.
- Pre-commit and `git diff --check` passed after documentation synchronization.
- PostgreSQL integrations use deterministic embeddings. Real-model quality/coexistence is P3.7.

## Git Bash procedure

Open the installed Docker Desktop and wait for PostgreSQL to become healthy. These integration
checks need only PostgreSQL, without Ollama, GPU runtime, FastAPI server, Next.js, or the worker:

```bash
cd '/e/Projects/Galaxy Frog Root/galaxy-frog'
bun run infra:up
docker compose ps
bun run db:migrate
RUN_DATABASE_INTEGRATION=1 uv run --directory backend python -m pytest --no-cov \
  tests/integration/test_postgres_lexical_search.py \
  tests/integration/test_durable_ingestion.py \
  tests/integration/test_phase_one_slice.py
bun run check
bun run precommit
```

For a manual lexical HTTP check, leave PostgreSQL running and start FastAPI in a separate terminal.
The export reuses the already provisioned reranker environment for later full-mode requests; no
installation or download is performed. Exports must be repeated in a new terminal.

```bash
cd '/e/Projects/Galaxy Frog Root/galaxy-frog'
export RERANKER_PYTHON='tmp/reranker-probe/.venv/Scripts/python.exe'
uv run --directory backend python -m uvicorn galaxy_frog.api.app:app --host 127.0.0.1 --port 8000
```

Choose an already imported video ID and a term in its transcript. This lists existing IDs without
modifying data; replace the placeholders in the subsequent commands.

```bash
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT id,title FROM videos ORDER BY created_at DESC LIMIT 5"'
video_id='PASTE-EXISTING-VIDEO-UUID'
curl --fail-with-body -sS "http://127.0.0.1:8000/v1/videos/$video_id/search" \
  -H 'Content-Type: application/json' \
  -d '{"query":"PASTE A TRANSCRIPT TERM","mode":"lexical","allow_fallback":false}'
trace_id='PASTE-TRACE-UUID-FROM-RESPONSE'
curl --fail-with-body -sS "http://127.0.0.1:8000/v1/videos/$video_id/retrieval-traces/$trace_id"
```

Hybrid/full-mode live tests additionally need Ollama with the existing BGE-M3 model. Full reranking
uses the provisioned CUDA environment and pinned cached weights. Questions also need the configured
generation model. Browser acceptance adds Next.js; fresh ingestion adds the existing worker/media
setup. No machine installation, model download, or security-policy change is required by this packet.
