# Phase 3 — Hybrid text retrieval and reranking

## Verified baseline and scope

**Phase 2 is fully merged, including the caption-control-event fix.** [PR #11](https://github.com/VedantPatil-99/the-galaxy-frog/pull/11) merged on September 16. Remote `main` is `3566a01`, with generated-contract, backend, and frontend [CI checks passing](https://github.com/VedantPatil-99/the-galaxy-frog/actions/runs/35119965752).

The working tree is clean on `fix/youtube-caption-control-events`. Local `origin/main` matches GitHub; local `main` is stale. The current checkout has the same file contents as remote `main`.

[PLANS.md](</E:/Projects/Galaxy Frog Root/galaxy-frog/PLANS.md>) still identifies Phase 2 as active and awaiting merge. The handoff, README, and Notion checkpoint also need their completion status reconciled.

Phase 3 delivers:

- PostgreSQL lexical search alongside existing BGE-M3 dense retrieval.
- Reciprocal Rank Fusion, local cross-encoder reranking, and temporal expansion.
- Spoken, exact-term, timestamp, and named-event query handling.
- Persisted retrieval traces, retrieval-only debugging, and an evidence panel.
- Measured comparison against the existing dense-only baseline.

**Non-goals:** ingestion redesign, OCR, frames/scenes, visual embeddings, VLMs, LangGraph, learning artifacts, cloud deployment, provider-selection UI, and the later evaluation platform.

## Consecutive implementation packets

Use the approved **stacked packet branches**. Each packet receives atomic Conventional Commits, verification, a push, and ready-to-copy PR metadata.

| Packet | Proposed branch | Deliverable |
|---|---|---|
| P3.1 | `feat/transcript-lexical-retrieval` | FTS migration, lexical-search interface and adapter, provenance tests, and Phase 3 plan activation |
| P3.2 | `feat/hybrid-transcript-retrieval` | Shared retrieval service, deterministic query analysis, RRF, and stage-ranking records |
| P3.3 | `feat/local-transcript-reranking` | GPU-first BGE reranker, bounded execution, runtime probe, and observable fallback |
| P3.4 | `feat/temporal-evidence-retrieval` | Timestamp constraints, named-event anchors, expansion, merging, and diversity |
| P3.5 | `feat/retrieval-traces-api` | Durable traces, search endpoint, shared question retrieval, and generated contracts |
| P3.6 | `feat/retrieval-evidence-ui` | Retrieval-only mode, evidence inspection, anchor selection, warnings, and timestamp seeking |
| P3.7 | `test/phase-3-exit-gate` | Reproducible benchmark, live acceptance, regression checks, and documentation closeout |

**Smallest first packet — P3.1**

Create the first branch from freshly verified `origin/main` after approval. Add a stored PostgreSQL `tsvector` using the language-neutral `simple` configuration and a GIN index over retrieval-unit text. Existing rows become searchable through migration; new rows remain synchronized automatically.

Affected files:

- Backend retrieval ports and database models.
- New `db/transcript_lexical_search.py` adapter.
- New Alembic revision `0006` for transcript FTS.
- New lexical-search unit tests and PostgreSQL integration tests.
- Plan, handoff, README, architecture/runbook updates, and a retrieval ADR.

This packet leaves the existing question path operational. Its acceptance gate proves exact-term retrieval, video isolation, ordered cue provenance, migration of existing transcripts, and unchanged ingestion identities.

## Retrieval behavior and interfaces

- **Shared pipeline:** search and questions use one Python retrieval service. Reuse the existing collection-safe pgvector adapter and indexing lifecycle.
- **Initial tuning defaults:** retrieve 30 lexical and 30 dense candidates; deduplicate by unit ID; use equal-weight RRF with constant 60; rerank the best 30; return at most eight evidence groups. Preserve each stage’s separate scores and ranks.
- **Reranker:** use `BAAI/bge-reranker-v2-m3` through a Python provider interface and the documented Transformers implementation. Pin the model revision and verified dependencies. Start with CUDA FP16, batch size one, a 512-token input limit, and concurrency one. Record truncation and execution metadata. [Model documentation](https://huggingface.co/BAAI/bge-reranker-v2-m3)
- **Temporal handling:** support explicit timestamps/ranges and transcript-derived named events. Resolve named events through an anchor retrieval pass; show up to five distinct matching moments when ambiguous. The selected server-validated anchor constrains the next pass. An unresolved anchor never silently becomes unrestricted search.
- **Evidence expansion:** start with ±15 seconds, merge overlapping windows and gaps under five seconds, deduplicate cues, and bound total context. Keep merged display intervals separate from original unit/cue intervals; citation validation continues to resolve original evidence.
- **Visible fallback:** embedder failure permits lexical retrieval; reranker failure preserves fused ordering. Return warnings, degraded status, and trace reasons. No automatic CPU, model, or cloud substitution. Strict benchmark runs disable fallback.
- **HTTP contracts:** add `POST /v1/videos/{video_id}/search` and a video-scoped trace-read endpoint. Search accepts the query, retrieval mode, result limit, and optional selected anchor. Responses contain status, anchor choices, ranked evidence, merged intervals, trace identity, and warnings.
- **Question compatibility:** retain existing answer and citation fields; add retrieval metadata and an explicit anchor-selection status. Invoke generation only after retrieval is resolved. Retrieval-only requests never call the generator.
- **Trace storage:** persist bounded, versioned records containing query analysis, configuration, provider identity, rankings, timings, fallback decisions, anchor selection, and evidence lineage.
- **Frontend:** consume regenerated FastAPI types through the existing proxy. Present evidence, anchor choices, and warnings without implementing retrieval logic.

## Services, setup, and acceptance

Only Ollama was listening on the expected service ports during inspection. The RTX 2050 reports **4 GB VRAM**.

| Live check | Required services/setup |
|---|---|
| P3.1 migration and lexical integration | Docker Desktop and PostgreSQL/pgvector |
| Dense/hybrid retrieval | PostgreSQL and Ollama with existing BGE-M3 |
| Reranker probe | User-provisioned pinned weights and compatible CUDA PyTorch environment |
| Browser retrieval/QA | PostgreSQL, FastAPI, Next.js, Ollama; reranker available for full-mode checks |
| Fresh caption/ASR regression | Above, plus worker and existing FFmpeg/faster-whisper setup |

Official downloads include Python 3.14 Windows CUDA wheels, but local execution and coexistence with Ollama still require proof. No Python downgrade, driver change, system installation, or model download happens automatically. [PyTorch CUDA wheel index](https://download.pytorch.org/whl/cu128/torch/)

Acceptance checks:

- Exact names/phrases succeed where dense similarity is weak; paraphrases succeed without exact keywords.
- FTS covers existing and newly ingested caption/ASR transcripts without re-ingestion.
- Fusion is deterministic; incompatible collections and cross-video evidence are rejected.
- Timestamp boundaries, ambiguous/missing event anchors, neighboring intervals, and duplicate cues behave correctly.
- Search works without generation; provider failures produce the selected visible fallback.
- Original cue IDs, ASR lineage, citation validation, and player seeking remain intact.
- Compare dense-only, fused, reranked, and temporally expanded retrieval on a frozen labeled set spanning caption and ASR sources. Report Recall@5, MRR@10, temporal overlap, latency, and memory use; claim improvements only when measured.
- Run focused tests per packet and the complete `bun run check` gate, preserving 100% backend statement/branch coverage. Run pre-commit, generated-contract checks, PostgreSQL integrations, production build, and live browser acceptance.

The principal risks are GPU contention, native dependency compatibility, FTS tokenization, ambiguous event anchors, and context expansion weakening citation precision. Each has targeted tests and trace visibility.

## Delivery and approval boundary

After approval, synchronize repository documentation and create the matching **P3.0** Notion checkpoint in the verified Galaxy Frog Workspace. Preserve historical records and append packet evidence as work completes.

Push completed packets regularly. Supply each PR’s base branch, Conventional Commit title, description, tests, and risks. **Do not create PRs.** Distinguish local completion from hosted CI and merge completion.

**No branch, repository file, or Notion page has changed. Branch creation remains pending your explicit approval.**
