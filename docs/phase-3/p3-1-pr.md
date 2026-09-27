# P3.1 PR metadata

- Base: `main`
- Head: `feat/transcript-lexical-retrieval`
- Title: `feat(retrieval): add transcript lexical search`

## Summary

- Add video-scoped PostgreSQL lexical retrieval alongside the existing dense adapter so exact
  names and quoted phrases can be found without invoking an AI provider.
- Preserve original retrieval-unit IDs, intervals, text, and ordered cue provenance.

## Changes

- Migration `20260927_0006` adds a stored `simple`-dictionary tsvector and GIN index. PostgreSQL
  backfills existing rows and maintains future inserts/updates without re-ingestion.
- Add a provider-independent retriever port and bound-query adapter with deterministic ranking.
- Use standard Python module entry points for test, migration, and pre-commit scripts following
  a Windows console-launcher execution block. No dependencies or security settings changed.
- Reconcile Phase 2 merge status and activate the approved Phase 3 plan, ADR, runbook, and Notion
  checkpoint. The existing question path and generated contracts remain unchanged.

## Testing

- 13 focused lexical/dense unit tests and seven real PostgreSQL integration checks passed.
- Verified exact phrases/names, video isolation, cue lineage, text-update maintenance, migration
  backfill/roundtrip, and existing ingestion/dense/question regressions.
- `bun run check` passed: generated contracts, lint, formatting, strict types, 514 backend tests
  with 100% statement/branch coverage, 26 frontend tests, and the production build.
- Default suite skips nine opt-in tests; seven database checks ran separately. Live ASR/media
  provider checks were not rerun for this database-only packet.
- `bun run precommit` passed. Migration head is `20260927_0006` locally.

## Risks / Notes

- Stop API/worker and run `bun run db:migrate` before code reads the updated model. Backfill/index
  creation takes a table lock; downgrade removes only the derived index/column.
- `simple` uses token/phrase matching without stemming or arbitrary substring matching.
- These are local results. Hosted CI and merge remain pending; no PR was created automatically.
