# Phase 3 P3.1 manual verification

Verified on 2026-09-27 on `feat/transcript-lexical-retrieval`: 13 focused retrieval tests, seven
PostgreSQL integrations, migration head `20260927_0006`, and the complete `bun run check` gate
passed. The full suite reports 514 backend tests passed (nine opt-in tests skipped), 100% backend
statement/branch coverage, and 26 frontend tests passed. Generated contracts, lint, formatting,
strict typing, production build, and pre-commit also passed. These are local results; hosted CI and
merge are separate gates.

The user's Git Bash terminal ran Python 3.14.7 and Ruff while Windows Application Control blocked
the `pytest` console launcher (OS error 4551). Standard module invocation worked from Codex too.
The new test's untyped empty-list lambda and a unit/integration filename collision were corrected.
No Python installation or Windows security-policy change was needed.

Use the existing interpreter's standard `python -m` entry points below. The root test, migration,
and pre-commit scripts use the same form. This does not change Windows security settings or require
another Python installation. If module execution is also blocked, stop and report the exact error.

Run the following from your own Git Bash terminal. Stop at the first failure and send the command
and its full error output, with credentials removed. Do not recreate the environment, change Python
versions, or disable device protections to get around a policy failure.

## 1. Check execution from your terminal

```bash
cd '/e/Projects/Galaxy Frog Root/galaxy-frog'
git status --short --branch
uv run --directory backend python --version
```

Expected branch: `feat/transcript-lexical-retrieval`. Expected Python: `3.14.7`.
If this returns the same Application Control error, stop here and report the executable path and
error. The remaining steps cannot work until the interpreter can execute.

## 2. Check PostgreSQL

Start Docker Desktop manually if necessary, then:

```bash
docker compose ps
```

The `postgres` service should be healthy on `127.0.0.1:5432`. If it is stopped:

```bash
bun run infra:up
docker compose ps
```

Keep PostgreSQL running through steps 5–6. Stop any running FastAPI/worker before applying the new
migration. No live Ollama, model download, ASR, web server, or API server is needed for P3.1 checks.

## 3. Format and lint only the packet's Python files

```bash
packet_files=(
  src/galaxy_frog/db/models.py
  src/galaxy_frog/db/transcript_lexical_search.py
  src/galaxy_frog/domain/retrieval/ports.py
  migrations/versions/20260927_0006_transcript_fts.py
  tests/unit/db/test_transcript_lexical_search.py
  tests/integration/test_postgres_lexical_search.py
)
uv run --directory backend ruff check --fix "${packet_files[@]}"
uv run --directory backend ruff format "${packet_files[@]}"
```

These commands may make the expected formatting/import-order edits to this packet. Preserve them.

## 4. Run focused unit tests and strict typing

```bash
uv run --directory backend python -m pytest --no-cov \
  tests/unit/db/test_transcript_lexical_search.py \
  tests/unit/db/test_transcript_search.py
bun run typecheck:backend
```

Focused tests use `--no-cov` because the repository's 100% coverage gate applies to the whole suite.
No running services are needed for this step. Keep the locked Pyright version; its available-update
notice is informational, not a test failure.

## 5. Apply the migration and prove real PostgreSQL behavior

```bash
bun run db:migrate
uv run --directory backend python -m alembic current
RUN_DATABASE_INTEGRATION=1 uv run --directory backend python -m pytest --no-cov \
  tests/integration/test_postgres_lexical_search.py \
  tests/integration/test_phase_one_slice.py \
  tests/integration/test_durable_ingestion.py
```

Expected migration head: `20260927_0006`. The new tests verify exact phrases, names, video isolation,
ordered cue provenance, automatic text-index maintenance, and backfill/downgrade/upgrade against a
temporary shadow table. They never downgrade the application's real tables. Existing integration
tests prove the import, dense retrieval, question, and durable-ingestion paths still work.

## 6. Run the complete quality gate

```bash
bun run check
bun run precommit
git diff --check
git status --short --branch
```

`bun run check` covers generated-contract drift, frontend/backend lint, backend formatting, strict
types, unit tests, 100% backend statement/branch coverage, and the Next.js production build.
If pre-commit changes files, preserve those edits and run it again until it passes.

## 7. Send the results back

Send the migration-head output, integration-test summary, full-check summary, pre-commit result,
and final `git status`. If anything fails, send that command's error before continuing. Never send
the contents of `.env` or database credentials. The agent will inspect any formatter changes, resolve
failures, record verified evidence, commit/push P3.1, and supply its PR title/description before
advancing to the next approved packet. Do not create a PR as part of these verification steps.
