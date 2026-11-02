# 7. Query-adaptive local visual retrieval and reasoning

Date: 2026-11-02

## Status

Accepted for the local Phase 5 implementation. Hosted CI and merge remain separate gates.

## Context

Temporal multimodal retrieval needs actual frame similarity and selective reasoning while
retaining evidence identity. Phase 4 components existed but were not connected to the durable
worker. Its frame interval represented a scene and discarded the periodic sample's exact capture
time. Python 3.14.7, Windows, the 4 GiB RTX 2050 and explicit user provisioning constrain runtimes.

## Decision

- Compose existing scene/hash/deduplication/persistence in a bounded worker indexing stage;
  add optional OCR and resumable visual indexing without redesigning transcript ingestion.
- Keep exact `capture_ms` separately from original scene intervals. Constrained frame/OCR
  retrieval filters capture time before ranking; unknown legacy captures cannot broaden scope.
- Use offline pinned SigLIP in an isolated, explicit interpreter/device. Store versioned 768-D
  vectors separately from transcript/OCR vectors and never mix collection identities.
- Use deterministic signals and lexical support as a routing heuristic, not calibrated accuracy.
  Retain every modality's rank/score; combine ranks with RRF and bound nearby expansion.
- Permit VLM calls only on the question path when retrieved evidence cannot resolve the request.
  The accepted local baseline uses pinned Qwen3-VL 2B, one frame, context 2048, bounded text,
  full GPU residency verification, citation-ID validation and explicit cleanup/failure.
- Check current privacy permission before image reads, cloud calls or cache reuse. Gemini needs
  server policy and per-question permission; worker cloud OCR needs both explicit flags. This
  strengthens ADR 0006: a configured key alone grants no upload permission.
- Cache by full ordered source/model/prompt/evidence identity. Persist bounded safe traces without
  image payloads, credentials or local paths. Keep frontend contracts generated from FastAPI.
- Keep pointwise VLM reranking experimental. The frozen comparison found no gain and strict
  failures, so production retains the simpler fusion path.

## Consequences

Model subprocess startup has visible latency; measured SigLIP query times varied from about
6–29 seconds between acceptance runs. Provider loss returns explicit degraded results without
changing model/device/cloud. The heuristic policy and one-frame VLM can miss visual information;
eight pooled frames and one caption smoke do not establish general retrieval or answer accuracy.
Legacy/terminal imports need explicit verified backfill rather than silent timestamp invention.
Gemini, larger embedding models, provider-selection UI and later orchestration remain outside
the accepted live local baseline. No system package/model download runs automatically.
