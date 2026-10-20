# P4.7 Pre-Implementation Report: Evidence UI & Phase 4 Exit Gate

## Scope

1. **API Contracts & Frame Serving (`galaxy_frog.api`)**:
   - Serve extracted frame images safely:
     - Add endpoint `GET /v1/videos/{video_id}/frames/{sequence}/image` returning `image/jpeg` with caching headers.
   - Extend `SearchResponse` or evidence structures in `galaxy_frog.api.retrieval_schemas`:
     - Expose multimodal evidence including OCR blocks and visual frame paths/URLs.
     - Include `modality` (`transcript` vs `ocr`) and visual frame metadata (`sequence`, `start_ms`, `end_ms`, `is_duplicate`).
   - Regenerate OpenAPI contract and frontend TypeScript types (`bun run generate:types`).

2. **Frontend UI Components (`apps/web/components/retrieval-evidence.tsx`)**:
   - Add visual frame cards to the retrieved evidence view:
     - Render frame thumbnail image with responsive styling.
     - Display extracted OCR text with confidence and temporal intervals.
     - Provide interactive click-to-seek: `onSeek(frame.start_ms)` to jump video playback to the exact moment the slide appeared.
   - Near-duplicate suppression:
     - Guarantee duplicate frames (`is_duplicate=true`) are omitted or collapsed so identical slides do not flood results.

3. **Phase 4 Exit Gate**:
   - Full acceptance check:
     - Ingestion with scene detection and periodic sampling (P4.2).
     - Perceptual hash deduplication (P4.3).
     - LlamaParse OCR extraction (P4.4).
     - Persistence and indexing in PostgreSQL/pgvector (P4.5).
     - Multimodal query routing and RRF fusion (P4.6).
     - Evidence UI presentation and duplicate suppression (P4.7).
   - Backend verification: 100% statement and branch coverage, zero Ruff or Pyright errors.
   - Frontend verification: `bun run check` (typecheck, lint, formatting, test, and Next.js build).

## Non-Goals for P4.7

- No VLM generative chat (Phase 5).
- No OCR bounding-box polygon SVG rendering over video player canvas (future enhancement).

## Affected Files

| File | Change |
|---|---|
| `backend/src/galaxy_frog/api/routes/videos.py` | Add frame image serving endpoint |
| `backend/src/galaxy_frog/api/retrieval_schemas.py` | Add visual frame / OCR evidence fields to SearchResponse |
| `apps/web/lib/api/contracts.ts` | Regenerate TypeScript types from OpenAPI |
| `apps/web/components/retrieval-evidence.tsx` | Render visual frame thumbnails and OCR evidence |
| `backend/tests/test_video_api.py` | Tests for frame serving endpoint |
| `docs/phase-4/p4-7-pre-report.md` | This pre-report |
| `PLANS.md` | Phase 4 exit gate record |

## Acceptance Checks

- [ ] Frame images are served securely with bounds checking on video ID and frame sequence.
- [ ] Multimodal evidence includes visual frame URLs and OCR text.
- [ ] Frontend displays frame images and seeks on click.
- [ ] Duplicate frames are cleanly suppressed.
- [ ] 100% backend coverage and 0 frontend type/lint/build errors.
