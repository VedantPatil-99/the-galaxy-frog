# P3.4 temporal evidence verification

Branch `feat/temporal-evidence-retrieval`, base P3.3 `fba2630`.
Local acceptance is separate from hosted CI and merge. No PR is created automatically.

## Verified behavior

- Timestamp filters run before top-k in both PostgreSQL adapters, preserving video/collection scope.
- Half-open boundaries exclude units ending at the start or beginning at the end; overlapping units
  retain original intervals. `before 0 seconds` returns no evidence.
- One trailing constraint supports `before`, `after`, `during`, `around`, `at`, timestamp ranges,
  MM:SS, HH:MM:SS, and numeric seconds/minutes/hours. Examples: `quokka between 00:10 and 00:20`,
  `after 1.5 minutes`, `explain before the demo`. An unqualified point uses +/-15 seconds; `during`
  a single timestamp selects a one-millisecond point window. Quoted temporal words remain literal.
- Unsupported/multiple temporal constraints fail explicitly. Operators currently use English; event
  names/transcript text can be multilingual. Time-only browsing skips models and considers the first
  30 matching units chronologically, warning when more exist.
- Literal event anchors use cue intervals, or the original unit for phrases spanning cues. Full
  transcript inspection catches literal mentions outside top-k. Nearby literal mentions form moments.
- Up to five distinct moments are offered. A unique literal moment resolves automatically. Ambiguous
  literal moments and every semantic suggestion require a current, video/event-scoped selection.
  Missing anchors remain unresolved. Semantic relevance is not assumed from a candidate's existence.
- +/-15 second display windows merge overlaps and gaps strictly under five seconds. At most eight
  groups retain original units/cues. The 12,000-character context budget reserves matching units first,
  then includes whole neighbors. Cue IDs are deduplicated; caption/ASR provenance is preserved.
- Display windows may clip at scope boundaries; original evidence never does. Boundary-straddling
  originals and omitted context generate warnings. No ingestion identities or schema changed.

## Results

- 103 focused temporal/fusion/adapter checks passed.
- Nine real PostgreSQL integrations passed, covering temporal SQL scope plus lexical/migration,
  durable ingestion, and existing question/citation regression.
- Full `bun run check` passed: 646 backend tests, 13 opt-in skips, 100% statement/branch coverage,
  26 frontend tests, contract drift checks, lint, formatting, strict types, and production build.
- The final timestamp punctuation/overflow regression passed the same full backend coverage gate;
  pre-commit and `git diff --check` passed after documentation synchronization.
- PostgreSQL tests use deterministic embeddings; this packet makes no model-quality claims.
- HTTP integration, durable traces, browser controls, and quality benchmarks are subsequent packets.

## Reproduce in Git Bash

For focused/full non-live checks, no services are required:

```bash
cd '/e/Projects/Galaxy Frog Root/galaxy-frog'
uv run --directory backend python -m pytest --no-cov \
  tests/unit/application/test_temporal_retrieval.py \
  tests/unit/application/test_retrieve_transcript.py \
  tests/unit/db/test_transcript_search.py \
  tests/unit/db/test_transcript_lexical_search.py
bun run check
bun run precommit
```

For database acceptance, open the already installed Docker Desktop and wait for PostgreSQL to be
healthy. Ollama, reranker, API, web, and worker are not required; fixture embeddings are intentional.
The commands below start the existing database and apply existing migrations; P3.4 adds no migration.

```bash
bun run infra:up
docker compose ps
bun run db:migrate
RUN_DATABASE_INTEGRATION=1 uv run --directory backend python -m pytest --no-cov \
  tests/integration/test_postgres_lexical_search.py \
  tests/integration/test_durable_ingestion.py \
  tests/integration/test_phase_one_slice.py
```

Use `python -m pytest` to avoid the previously blocked Windows console launcher. Do not recreate
Python, install machine tools, or change Application Control policy to rerun these checks.
