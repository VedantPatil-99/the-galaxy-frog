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
3. **Cloud Consent**: In accordance with Phase 8 goals, since this is a cloud service, explicit user configuration of the API key is required. We will document how to configure it locally via `.env`.
4. **Data Privacy**: Local video frames are extracted, hashed, and deduplicated locally, but only selected unique frames are sent to LlamaParse for OCR extraction.

## Consequences

- **Positive**: We avoid Python 3.14 C-extension build issues and PaddleOCR dependencies.
- **Positive**: LlamaParse provides superior extraction for tables and diagrams compared to basic OCR engines.
- **Negative**: OCR is no longer strictly local by default. It introduces a network dependency and requires a LlamaCloud API key.
- **Mitigation**: We retain the `OCRProvider` protocol so a local alternative (like a lightweight PyTesseract or future Python 3.14 compatible tool) could be dropped in later if needed.

