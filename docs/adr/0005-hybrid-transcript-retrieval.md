# ADR 0005: Fuse transcript retrieval while preserving source evidence

- Status: Accepted; implementation begins with P3.1
- Date: 2026-09-27

## Context

Dense-only retrieval can miss exact names and phrases. Phase 3 needs inspectable lexical and dense
retrieval, local reranking, and temporal context without rebuilding the completed ingestion system.
Original transcript intervals and cue provenance remain the citation source of truth.

## Decision

Add a PostgreSQL-maintained `simple`-dictionary `tsvector` and GIN index on retrieval-unit text.
Backfill through a migration; do not re-ingest or re-embed existing videos. Query lexical text with
bound web-search expressions, rank deterministically, and preserve ordered cue links. The simple
dictionary avoids an English-only stemming assumption; it is token search, not fuzzy or arbitrary
substring matching.

Reuse the existing versioned BGE-M3 collection and dense adapter. A Python application service will
combine independent top-30 result sets using equal-weight RRF (constant 60), then rerank the best 30
through a provider interface. Keep lexical, dense, fusion, and reranker scores distinct. BGE reranker
v2-m3 targets CUDA FP16 with bounded input, batch, and concurrency; load native code lazily and only
use user-provisioned pinned weights. No device, model, or cloud substitution is implicit.

Query analysis is deterministic and supports explicit times/ranges plus named transcript events.
Named events first produce anchor candidates; ambiguous anchors require user selection. An unresolved
anchor cannot silently turn into an unrestricted query. Expanded/merged windows reference original
units/cues; answer citations continue to validate against those originals. Bound context and deduplicate
overlap. Retrieval-only execution never calls the generator.

Persist bounded, versioned traces and expose them through video-scoped FastAPI schemas. Next.js
consumes generated declarations through the existing proxy. Provider failures may retain lexical or
fused retrieval only when warnings, degraded status, and trace reasons expose the missing stage.
Strict evaluation disables fallbacks.

## Consequences

- PostgreSQL FTS becomes available to existing caption and ASR transcripts without ingestion changes.
- The additive generated column must be migrated before code selecting the updated model is run.
- Migration can hold a table lock while computing the column/index; run it with ingestion stopped.
- Exact-token behavior and ranking must be tested on real PostgreSQL, not only mocked SQL results.
- GPU coexistence on the 4 GB RTX 2050 is an acceptance gate, not an assumed capability.
- Deployment remains a modular monolith; OCR, VLM, LangGraph, cloud adapters, and later product
  capabilities remain outside Phase 3.
