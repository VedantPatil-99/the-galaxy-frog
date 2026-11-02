# Phase 5 plan and verification

Date: 2026-11-02. Status: **locally complete**. Branch: `feat/visual-retrieval-vlm`.
Base: verified Phase 4 implementation `6a5a903`; Phase 4 was not assumed merged into main.
No PR or Notion page was created. This record is the single end-of-phase Markdown closeout.

## Completed work packets

- P5.1: framework-independent visual embedding/reasoning contracts, identities, bounded evidence,
  explicit failures, deterministic signals, sufficiency and privacy decisions.
- P5.2: pinned offline SigLIP, explicit interpreter/device, normalized vectors, four-frame batches,
  bounded subprocess output/deadlines; real Transformers 5 output compatibility was verified.
- P5.3: 768-D frame vectors, immutable collections, source hashes, video isolation, resumable batches.
  Migrations `0009`–`0012` add visual vectors, compatible collection dimensions, cache and capture time.
- P5.4: adaptive transcript/OCR/visual retrieval, per-stage ranks/timings, RRF, deduplication and
  expansion only after seeds, bounded to four neighbors within ±15 seconds of the best capture.
- P5.5: query-only selective local Qwen3-VL and optional Gemini adapter; one local frame, bounded
  evidence, validated IDs, explicit provider/quality/privacy degradation, no retrieval-only generation.
- P5.6: video/frame/hash/model/prompt/evidence cache isolation, current permission checks before
  cache reuse, frame revalidation and durable traces (64 KiB cache / 256 KiB trace limits).
- P5.7: provider-independent opt-in pointwise VLM reranking with original candidate objects;
  strict failures retained and no production promotion.
- P5.8: generated API contracts, separate transcript/OCR/visual presentation, frame provenance,
  capture-time seeking, reasoning/cache/degraded state, frozen evaluation and local exit gate.

The user-approved integration packet connects existing Phase 4 extraction/indexing to a bounded
durable worker stage. `capture_ms` is additive: original IDs and scene intervals stay intact.
Periodic frame 6 was captured at 36,033 ms while sharing frame 5's scene `[26033,46300)`;
it must never enter the `[26000,30000)` query. SQL and application scope checks cover this.
Only the 56 frames created by this acceptance run had capture metadata recovered from their
retained sampler inputs, after validating every sequence/interval/source/hash. No arbitrary
legacy timestamp was guessed and no vector/image/ID was rewritten.

## Automated local verification

- `bun run check`: generated OpenAPI/types, Ruff lint/format, strict Pyright, ESLint, TypeScript,
  backend/frontend tests, benchmark integrity and production Next.js build pass.
- Backend: **1,121 passed**, 16 opt-in skips, **100% statement and branch coverage**;
  6,541 statements / 1,654 branches across 139 source files.
- Frontend: **35 passed**. Phase 3 benchmark tests: seven. Phase 5 integrity/metric tests: three.
- Real PostgreSQL/pgvector: **11 passed** across visual retrieval/cache, lexical/migration and
  durable-ingestion integration modules. Restart/idempotency, cross-video isolation, unknown
  capture exclusion, temporal boundaries, cache durability/cascade and collection safety pass.
- Alembic upgrade through `20261102_0012` succeeded locally. `bun run precommit` passes.
- Logo work remains uncommitted and unchanged: `apps/web/app/page.tsx` SHA-256
  `5E13F3B84733F3BCB304F93586C5A167783FA30D13DF613DBF16D72CC682700D`;
  `apps/web/public/logo.png` SHA-256
  `0D6F782992E6C5E02F1814C76915CF74D2CF8D41511E01808BCD88512E31F9AE`.

## Real local provider acceptance

User-provisioned Python 3.14.7 on Windows, i5-11400H, 15.7 GiB RAM, RTX 2050 4 GiB.
The isolated runtime has Torch 2.11.0+cu128, Transformers 5.17.0, Pillow 13.0.0,
SentencePiece 0.2.2 and Protobuf 7.36.2. No machine-level install, driver/security change,
automatic model download, CPU/cloud substitute or fake live-provider result was used.

- Bounded production frame pipeline acquired the public source `_TJ61eCWQvQ`, stored 56 real
  frames and removed its temporary source video. This live extraction composed production
  components directly; the durable worker wiring is separately covered by automated tests.
- Real SigLIP revision `7fd15f0689c79d79e38b1c2e2e2370a7bf2761ed` returned 768-D unit vectors
  on CUDA. All 56 frames indexed in 87.15 seconds. The full-index turtle query's top frames were
  visually inspected and relevant. This live SQL smoke is separate from the closed-pool benchmark.
- Real Ollama Qwen3-VL 2B Q4_K_M digest
  `afc393e3572c07db0bc84aa460d928a16c75f3fbb9ca9e0dccc4fbd2dafb573e`
  returned `TOMORROW IS A MYSTERY`, citing frame
  `ebc456b6-bbcb-4b30-ac0f-d91f0291e918`. The adapter check took 6.79 seconds, verified full
  GPU residency (about 1.64 GiB/context 2048), then unloaded the model.
- Public FastAPI question/search/trace paths ran against the real database and local models.
  First reasoning call missed cache; second reused it. Retrieval-only search had no reasoning.
  Safe persisted traces retained exact capture/source/collection/provider lineage.
- An earlier open-description probe added an unverified glowing-mouth detail. Caption success
  and valid IDs do not establish factual accuracy for arbitrary descriptions or reasoning.

## Frozen retrieval and reranking measurements

[`visual-v1.json`](visual-v1.json) is an eight-frame, manually inspected **closed pool** from one
public video. Caption labels are human transcription, not claimed OCR output. It includes visual,
OCR-targeted, mixed, temporal and empty-window queries; transcript-only, duplicate/irrelevant,
provider-failure and VLM-disabled behavioral cases are represented and tested separately.
The quality runner reads existing real vectors/pixels and rejects source/hash/provenance changes.
It projects the fusion baseline onto visual evidence: with one visual list, RRF preserves cosine
ordering. It is not an end-to-end three-modality recall benchmark or an OCR accuracy benchmark.

- [`visual-v1-local-result.json`](visual-v1-local-result.json): all seven positive queries hit a
  relevant frame at rank 1 (and within top 3), reciprocal rank 1.0; the empty interval returns none.
  Final rerun query latency: median **24.25s**, range **23.11–29.04s**; earlier runs were about
  6–9s. These are observed cold-process/local-load timings, not throughput or latency guarantees.
- [`visual-v1-rerank-local-result.json`](visual-v1-rerank-local-result.json): four optional local
  reranking trials. Two completed with all candidate grades tied at 2, no quality improvement,
  and **20.48s / 21.34s** additional latency. Two failed strict output validation. No silent
  substitution is scored, and reranking remains off by default. This is a pointwise reasoning
  experiment, not a claim to run separately provisioned Qwen3-VL Reranker weights.
- [`visual-v1-baseline-timeout-result.json`](visual-v1-baseline-timeout-result.json) retains an
  unsuccessful rerun. Docker stopped during checking; the user restarted it. The runner was also
  changed to close its read transaction before model work. The subsequent complete baseline passed.
- Fingerprints normalize CRLF to LF for Windows/Linux portability; reports retain the original
  measurement-byte fingerprints. Annotation content, query labels and measured values are unchanged.
  Generated-answer quality is explicitly excluded from retrieval metrics.

## Browser acceptance

Real local frontend/proxy/API/database/model checks verified:

- Correct caption, original visual citation, one retrieved thumbnail, model identity/cache state.
- Original scene/capture/frame/hash/collection provenance and video-scoped saved trace inspection.
- Clicking the visual frame's seek control showed the YouTube player at **26 seconds**.
- With VLM explicitly disabled, the UI showed degraded reasoning and retained the original frame,
  image, capture time and seek control; it did not invent a supported answer.
- A live visual timeout also rendered an explicit provider warning and safe transcript/OCR fallback.

Ignored local proof files: `tmp/phase5-browser-evidence.jpg`, `tmp/phase5-browser-seek.jpg`,
`tmp/phase5-browser-disabled.jpg`. No simulated UI response was used for these browser checks.
The actual cloud OCR/Gemini UI path was not exercised; automated rendering/transport tests cover
their contracts, not cloud accuracy or service acceptance.

## Operational limits and handoff

Source defaults intentionally keep embeddings/VLM/external processing disabled. Acceptance used
process-local configuration; `.env` was not changed. Follow the runbook to enable the verified
local runtime. Existing terminal imports are reused; frame backfill requires explicit verified
processing. Unknown legacy captures stay excluded from temporal frame/OCR queries.

The deterministic signal policy and single selected frame have limited recall; lexical support
is not calibrated sufficiency. Larger and diverse datasets, broader answer-quality evaluation,
Qwen3-VL Embedding, live Gemini/cloud OCR and general provider UI remain deferred/unverified.
No Phase 6 player action, learning artifacts, LangGraph, AWS or full-video VLM processing was added.
Commits are pushed to the Phase 5 branch. Hosted CI, PR creation and merge are separate and are
not claimed by this local completion record.
