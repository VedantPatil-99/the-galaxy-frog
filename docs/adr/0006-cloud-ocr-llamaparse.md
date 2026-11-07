# 6. Cloud OCR using LlamaParse

Date: 2026-10-05

## Status

Accepted

## Context

Phase 4 of Galaxy Frog introduces the Scene and OCR pipeline. The original plan in `FULL_PLAN.md` specified using `PaddleOCR/PP-OCRv5`. However, PaddleOCR has known compatibility issues with newer Python versions, specifically Python 3.14 which is our standard environment.
Downgrading Python or introducing complex local build steps for PaddleOCR violates our project invariants (no downgrading Python, keep setup simple).

We need an OCR solution that:
1. Is highly accurate for diverse data types like tables, diagrams, images, and charts.
2. Integrates cleanly into our Python 3.14 backend without native dependency hell.
3. Fits behind a standard `OCRProvider` interface.

LlamaParse is a cloud-based parsing and OCR solution that excels at handling complex document layouts and images.

## Decision

We will use **LlamaParse Cloud** as the OCR adapter for Phase 4, bypassing the local PaddleOCR limitations.

1. **Architecture Boundary**: The FastAPI application will define an `OCRProvider` interface in the domain layer.
2. **Adapter**: A `LlamaParseOCRProvider` will implement this interface.
3. **Cloud Consent**: Explicit API-key configuration is required. Phase 5 strengthens this boundary in [ADR 0007](0007-query-adaptive-visual-retrieval.md): a key alone is insufficient; worker uploads also require `EXTERNAL_FRAME_PROCESSING_ENABLED=true` and `OCR_EXTERNAL_FRAMES_PERMITTED=true`.
4. **Data Privacy**: Local video frames are extracted, hashed, and deduplicated locally. Only bounded selected unique frames may be sent to LlamaParse after both explicit permission flags pass. The default is no external frame submission.

## Consequences

- **Positive**: We avoid Python 3.14 C-extension build issues and PaddleOCR dependencies.
- **Positive**: LlamaParse provides superior extraction for tables and diagrams compared to basic OCR engines.
- **Negative**: OCR is no longer strictly local by default. It introduces a network dependency and requires a LlamaCloud API key.
- **Mitigation**: We retain the `OCRProvider` protocol so a local alternative (like a lightweight PyTesseract or future Python 3.14 compatible tool) could be dropped in later if needed.

