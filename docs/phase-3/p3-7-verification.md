# P3.7 exit gate

Status: in progress on `test/phase-3-exit-gate`, stacked on P3.6 `66c7917`.
Manual browser acceptance remains open. No quality improvement or final Phase 3 completion is claimed
until the measured results and remaining acceptance checks are recorded here.

## Frozen comparison

`benchmark-v1.json` contains 12 transcript-derived acceptance cases across one manual-caption video
(423 cues, 38 retrieval units) and one existing CUDA ASR video (59 cues, seven units). Labels were
chosen from transcript intervals before scoring. They include exact phrases/names, paraphrases,
spoken questions, and four explicit timestamp constraints. Every original unit overlapping a labeled
answer interval counts as relevant. These are not exhaustive independent human relevance judgments.

The script verifies transcript fingerprints before running. It does not re-ingest, download, generate
answers, change provider settings, or silently fall back. Reports retain failed trials, trace IDs,
model identities, configuration, warnings, timing, and sampled device memory. The initial interrupted
run (two completed baseline trials before yielding the GPU for planned manual testing) is not a full
benchmark; fresh complete runs are reported separately.

- Dense, fused, and reranked variants measure original unit rankings from saved traces, before
  expansion, using the unconstrained baseline query.
- Expanded retrieval measures original units in ranked display groups and applies the case's
  timestamp constraint where present. Non-temporal cases reuse the reranked request.
- Recall@5 is the fraction of labeled relevant units found in the first five units/groups. MRR@10
  is reciprocal rank of the first item containing relevant evidence; expanded responses have at most
  eight groups, so that variant cannot expose ranks nine or ten.
- Temporal overlap is intersection-over-union of the union of the first five predicted intervals
  and the labeled answer intervals. Over-expansion is penalized instead of counting mere overlap.
- HTTP latency includes expansion and trace persistence even when scoring pre-expansion rankings.
  Reranker model-loading, processing, total execution, allocator peaks, and truncation are separate.
- Device memory is sampled every two seconds using the existing `nvidia-smi`; it includes Ollama
  and other GPU applications. Unavailable sampling is explicitly null. Short peaks may be missed.
- Aggregates cover successful trials only, with attempted/passed/failed counts. Do not compare
  averages from different success subsets without accounting for failures. Each trial gets one attempt.

The backend setting `RETRIEVAL_FUSION_LIMIT` accepts 1–30 and defaults to 30. It bounds the fused
candidate pool used for reranking and expansion; lexical and dense each still retrieve up to 30.
It is recorded in trace configuration. A 20-candidate comparison does not alter the 30 default.
The seven-unit ASR sample cannot distinguish those candidate budgets; use caption results for that
part of the comparison. Startup/model-loading overhead remains even when fewer passages are scored.

## Git Bash commands

Services: PostgreSQL with migration `20260927_0007`, Ollama with existing BGE-M3, and FastAPI with
the provisioned pinned CUDA reranker environment. The benchmark needs neither Next.js nor the worker
or generator. Avoid simultaneous GPU tests/manual requests when measuring this single-concurrency
provider. No installation or model download is performed.

API terminal, starting with 30 candidates:

```bash
cd '/e/Projects/Galaxy Frog Root/galaxy-frog'
export RERANKER_PYTHON='E:/Projects/Galaxy Frog Root/galaxy-frog/tmp/reranker-probe/.venv/Scripts/python.exe'
export RETRIEVAL_FUSION_LIMIT=30
uv run --directory backend python -m uvicorn galaxy_frog.api.app:app --host 127.0.0.1 --port 8000
```

Benchmark terminal:

```bash
cd '/e/Projects/Galaxy Frog Root/galaxy-frog'
bun run test:benchmark
bun run benchmark:phase3 --output ../tmp/phase-3-benchmark-30.json
```

Stop the API with Ctrl+C, export `RETRIEVAL_FUSION_LIMIT=20` in its terminal, and restart the same
command. Repeat the benchmark with `--output ../tmp/phase-3-benchmark-20.json`. Restore 30 afterward
unless measured quality/latency supports adopting 20. Keep both outputs, including failures.
The native provider deadline remains 120 seconds unless explicitly configured otherwise; any
override must be recorded alongside results. Do not change the deadline between candidate-budget
runs and then attribute timing differences solely to the budget.

For a different database, the frozen video IDs and transcript fingerprints must be available. Do
not replace labels after observing rankings to force a pass. A fresh ASR transcription can differ;
that is a new dataset version, not the same frozen run.

## Remaining acceptance

- Complete strict 30/20 comparisons and report quality, latency, memory, failures, and limitations.
- Verify question generation and original citation grounding with the shared retrieval path.
- Verify explicit provider fallback and unresolved/ambiguous anchors without generation.
- Complete manual visual/player-seeking checks in `p3-6-verification.md`.
- Run the complete quality gate, PostgreSQL/native integrations, pre-commit, and final contract/build
  checks. Keep local completion distinct from hosted CI and merge.
