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

P3.3 runs the local cross-encoder in one child process per request using the existing bounded
command runner. Queries and candidate text travel in a temporary JSON file, not command-line
arguments; cleanup follows success, failure, timeout, and cancellation. Native dependencies stay
optional and can use an explicitly provisioned interpreter. The child loads only the immutable
local snapshot, rejects unverified torch/Transformers versions and unavailable CUDA, and exits to
release VRAM. Concurrency is one per shared provider instance/API process. A concurrent request
fails observably with `reranker_busy`; there is no unbounded queue.

This deliberately accepts model-loading latency: the first full local 30-candidate provider run
took 44.2 seconds, including 22.9 seconds loading the model. Persistent model residency is not
implemented here; measure whole-pipeline latency and Ollama coexistence in the exit gate before
making performance claims or changing lifecycle policy. The runtime records raw finite logits,
input/original token counts, truncation, exact identity, device, timing, and allocated/reserved VRAM.

P3.4 resolves temporal scope before database top-k and treats intervals as half-open. A deterministic
English grammar supports one trailing timestamp/range or named event. Unrecognized temporal intent
fails explicitly rather than dropping the constraint. Literal events use cue boundaries where
possible and check the full stored transcript for ambiguity. Per the user's decision, semantic
suggestions are useful for implied wording but always require confirmation. Choices are bounded
to five and validated server-side against the video/event; missing or stale selections never broaden
the query. Reranked requests use a fused anchor pass to avoid loading the cross-encoder twice.

Expansion keeps whole original units, reserves seeds before neighbors, and caps context at 12,000
characters. Display intervals may merge and clip; citation intervals never do. Boundary-straddling
units and budget omissions are observable. This preserves citation identity but requires the answer
path and UI to distinguish original evidence from requested display scope.

## Consequences

P3.5 persists version-1, video-scoped trace rows before returning search or question retrieval.
Records are capped at 256 KiB in both Python and PostgreSQL. Preserve rankings/lineage and omit
duplicate transcript text from stage/group copies; reject oversized records explicitly. The trace
records declared provider identities even on failure, and actual reranker metadata when available.
Known provider, temporal, anchor, and integrity errors receive durable failure traces. Database
failure cannot claim durable success. No automatic retention deletion policy is introduced.

Question responses preserve answer/citation fields and add retrieval status/metadata. Anchor choices
block generation; retrieval-only requests never construct a generator. Original evidence drives
citations, with an additional overlapping-cue quote check on temporal queries. One app-scoped
reranker instance enforces concurrency; the presentation proxy allows five minutes for retrieval
and questions without making provider decisions itself.

- PostgreSQL FTS becomes available to existing caption and ASR transcripts without ingestion changes.
- The additive generated column must be migrated before code selecting the updated model is run.
- Migration can hold a table lock while computing the column/index; run it with ingestion stopped.
- Exact-token behavior and ranking must be tested on real PostgreSQL, not only mocked SQL results.
- GPU coexistence on the 4 GB RTX 2050 is an acceptance gate, not an assumed capability.
- Deployment remains a modular monolith; OCR, VLM, LangGraph, cloud adapters, and later product
  capabilities remain outside Phase 3.
