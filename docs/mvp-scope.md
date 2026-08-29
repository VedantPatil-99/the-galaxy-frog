# MVP and non-MVP boundary

## Product statement

Galaxy Frog is a temporal multimodal search engine that returns evidence-grounded answers with precise video intervals. The flagship contribution is cross-modal temporal retrieval across speech, OCR/screen text, and selected visual evidence.

## MVP capability boundary

The first usable vertical slice will support:

1. A user submits a supported YouTube URL.
2. The system obtains metadata and a timestamped transcript.
3. It creates timestamp-preserving transcript segments.
4. It indexes and retrieves relevant transcript evidence.
5. It returns a grounded answer with one or more evidence intervals.
6. The web player can jump to each returned timestamp.
7. Failures are structured, recoverable where possible, and useful in the UI.

## MVP engineering requirements

- Presentation-only web tier and 100% Python server tier.
- Provider-independent configuration and explicit fallback reporting.
- CPU-compatible local development path.
- Evidence provenance retained from ingestion through answer rendering.
- Measurable retrieval and latency behavior.

## Non-MVP capabilities

These are valuable but do not block the first vertical slice:

- Full-video VLM analysis or descriptions for every frame.
- OCR across all candidate frames.
- Query-adaptive visual analysis.
- Advanced multimodal fusion and learned rank fusion.
- Automatic chapters and semantic scene segmentation.
- Notes, flashcards, and quizzes.
- Multi-video libraries, collaboration, billing, or public sharing.
- AWS production deployment, autoscaling, or GPU fleet management.
- Non-YouTube remote providers beyond the source interface.

## Phase 0 boundary

Phase 0 builds foundations and contracts only. It must not process a video or call an AI model. Its final demonstration is browser-to-FastAPI connectivity, health/readiness reporting, generated API types, and graceful rendering of a deliberate structured error.
