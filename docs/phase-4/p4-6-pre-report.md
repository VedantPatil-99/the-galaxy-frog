# P4.6 Pre-Implementation Report: Multi-modal Retrieval & Query Routing

## Scope

1. **Multimodal Query Routing (`galaxy_frog.domain.retrieval.multimodal` / `galaxy_frog.application.retrieval`)**:
   - Classify / route queries based on intent:
     - Transcript-only (spoken queries without visual intent)
     - OCR-only (visual queries explicitly asking for slides, code on screen, diagrams, text on screen)
     - Multimodal hybrid (retrieve both transcript and visual OCR evidence)
   - Preserve explicit user intent and default to unified multimodal search when visual evidence may enrich spoken content.

2. **Multimodal Fusion & Evidence (`galaxy_frog.domain.retrieval.multimodal`)**:
   - Model `MultimodalRetrievedEvidence`:
     - Distinguishes modality (`transcript` vs `ocr`)
     - Contains either `RetrievedEvidence` (transcript unit with cue lineage) or `RetrievedOCREvidence` (OCR block with frame sequence and image path)
     - Preserves temporal intervals (`start_ms`, `end_ms`), confidence, score, and provenance
   - Fuses transcript and OCR candidate rankings via Reciprocal Rank Fusion (RRF) with temporal co-occurrence bonuses or interleaved rank alignment.

3. **Multimodal Retrieval Service (`galaxy_frog.application.retrieval.multimodal_search`)**:
   - Orchestrates lexical and dense transcript retrieval alongside lexical and dense OCR retrieval.
   - Respects temporal window filters across both modalities.
   - Preserves degraded states and warnings if one modality (e.g. OCR embeddings) is unavailable, gracefully returning single-modality results without failing.

4. **Tests & Coverage**:
   - Unit tests for query routing, multimodal fusion, and search orchestration.
   - 100% statement and branch coverage with zero Pyright or Ruff errors.

## Non-Goals for P4.6

- No frontend video player UI seeking (P4.7).
- No VLM-based visual reranking or bounding box overlay generation.

## Affected Files

| File | Change |
|---|---|
| `backend/src/galaxy_frog/domain/retrieval/multimodal.py` | Multimodal evidence, query intent, and fusion models |
| `backend/src/galaxy_frog/application/retrieval/multimodal.py` | Orchestration service blending transcript and OCR retrieval |
| `backend/tests/unit/retrieval/test_multimodal.py` | Unit tests for multimodal routing and fusion |
| `docs/phase-4/p4-6-pre-report.md` | This pre-report |
| `PLANS.md` | Progress and acceptance tracking |

## Required Services / API Keys

- Pure-Python domain and mock-backed tests run with zero external services.
- Full test suite runs locally in < 10s.

## Acceptance Checks

- [ ] Query intent classification distinguishes spoken vs visual vs hybrid intent.
- [ ] Multimodal fusion preserves interval provenance and source cue/frame lineage.
- [ ] Degraded execution when one modality is unavailable or empty returns surviving modality with structured warning.
- [ ] 100% statement and branch coverage maintained across backend test suite.
- [ ] `ruff check` and `pyright` pass with 0 errors.
