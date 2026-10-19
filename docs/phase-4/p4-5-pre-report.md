# P4.5 Pre-Implementation Report: OCR Persistence, Scene Association & Indexing

## Scope

1. **Alembic Migration (`20261020_0008_scene_and_ocr_pipeline.py`)**:
   - Table `scenes`: visual scene boundaries (`start_ms`, `end_ms`, `score`, `detector`, `detector_revision`, FK to `videos.id` CASCADE).
   - Table `video_frames`: extracted frames (`sequence`, `start_ms`, `end_ms`, `source`, `dhash`, `is_duplicate`, `image_path`, FK to `videos.id` CASCADE, optional FK to `scenes.id` SET NULL).
   - Table `ocr_blocks`: extracted text regions (`text`, `start_ms`, `end_ms`, `confidence`, `confidence_method`, `provider`, `provider_revision`, FK to `videos.id` CASCADE, FK to `video_frames.id` CASCADE).
     - Generated `search_vector` column with `to_tsvector('simple'::regconfig, text)` and GIN index for lexical search.
   - Table `ocr_embeddings`: dense vector embeddings (`embedding` vector(1024), `input_fingerprint`, FK to `ocr_blocks.id` CASCADE, FK to `embedding_collections.id` RESTRICT).

2. **SQLAlchemy ORM Models (`galaxy_frog.db.models`)**:
   - `SceneRow`
   - `VideoFrameRow`
   - `OCRBlockRow`
   - `OCREmbeddingRow`
   - All check constraints and indexes declared to match Alembic migration.

3. **Repositories & Search Services (`galaxy_frog.db.vision_repository`, `galaxy_frog.db.ocr_search`)**:
   - Store scenes, deduplicated frames, and OCR blocks atomically and idempotently.
   - Embed and index OCR text using existing `EmbeddingProvider` protocol.
   - Video-scoped OCR lexical search (tsvector/tsquery) and dense vector search (cosine distance via pgvector).
   - Strict isolation: OCR evidence is stored and retrieved independently from transcript units/cues.

4. **Tests**:
   - Model unit tests for ORM mappings and constraints.
   - Repository unit tests with fake sessions/mocked queries (100% statement and branch coverage).
   - Real PostgreSQL integration tests matching `test_postgres_lexical_search.py` and `test_durable_ingestion.py`.

## Non-Goals for P4.5

- No multimodal hybrid fusion or query routing (P4.6).
- No web frontend display or video player seeking to OCR frames (P4.7).

## Affected Files

| File | Change |
|---|---|
| `backend/migrations/versions/20261020_0008_scene_and_ocr_pipeline.py` | New Alembic migration |
| `backend/src/galaxy_frog/db/models.py` | Add `SceneRow`, `VideoFrameRow`, `OCRBlockRow`, `OCREmbeddingRow` |
| `backend/src/galaxy_frog/db/vision_repository.py` | New repository for persisting scenes, frames, and OCR blocks |
| `backend/src/galaxy_frog/db/ocr_search.py` | Lexical and dense vector search over OCR blocks |
| `backend/tests/unit/db/test_vision_repository.py` | Unit tests for vision persistence |
| `backend/tests/unit/db/test_ocr_search.py` | Unit tests for OCR lexical and vector search |
| `docs/phase-4/p4-5-pre-report.md` | This pre-report |

## Required Services / API Keys

- Unit tests run locally with zero services.
- Integration tests (opt-in) require local Docker PostgreSQL/pgvector (`docker compose up -d postgres`).

## Acceptance Checks

- [ ] Migration applies cleanly (`alembic upgrade head`) and downgrades cleanly (`alembic downgrade -1`).
- [ ] Cascades work: deleting a video cleanly removes scenes, frames, OCR blocks, and embeddings.
- [ ] OCR blocks preserve temporal intervals and frame sequence numbers.
- [ ] GIN index enables fast lexical search over OCR text.
- [ ] 100% statement and branch coverage maintained across all backend tests.
- [ ] `ruff check` and `pyright` pass with 0 errors.
