# P4.4 Pre-Implementation Report: LlamaParse Cloud OCR Adapter

## Scope

1. Implement `LlamaParseOCRAdapter` fulfilling the `OCRProvider` protocol from P4.1.
2. Provide direct, robust REST communication with LlamaCloud API (`https://api.cloud.llamaindex.ai`) using the already installed `httpx` async client (avoids bloating dependencies with 39 packages from `llama-parse`/`llama-index-core`/`nltk`/`tiktoken`).
3. Handle the complete LlamaParse Cloud extraction lifecycle:
   - Upload deduplicated JPEG frame via multipart/form-data (`POST /api/parsing/upload`).
   - Poll parsing status (`GET /api/parsing/job/{job_id}`).
   - Retrieve raw structured JSON result (`GET /api/parsing/job/{job_id}/result/raw/json`).
   - Extract text segments and bounding blocks into `OCRBlock` instances preserving:
     - Text content
     - Temporal bounds (`start_ms`, `end_ms` inherited from `FrameSample`)
     - Source frame sequence (`source_frame_sequence`)
     - Provider specification (`OCRProviderSpec(provider="llamaparse", provider_revision=...)`)
     - Confidence metrics (if provided by LlamaParse, or None with explicit reason)
4. Add typed configuration settings in `galaxy_frog.config`:
   - `llama_cloud_api_key: SecretStr | None = None`
   - `llama_cloud_base_url: str = "https://api.cloud.llamaindex.ai"`
   - `ocr_timeout_seconds: float = 60.0`
   - `ocr_poll_interval_seconds: float = 1.0`
5. Unit tests with mocked HTTP responses (success, polling progression, rate-limiting 429, authentication 401/403 failure, timeout, unparseable response) maintaining 100% statement and branch coverage without live network access or real API credits in unit tests.

## Non-Goals for P4.4

- No database tables or Alembic migrations (OCR persistence is P4.5).
- No pipeline integration into ingestion workers (P4.5).
- No frontend UI or search routing (P4.6, P4.7).
- No live API calls during standard unit tests.

## Affected Files

| File | Change |
|---|---|
| `backend/src/galaxy_frog/config.py` | Add LlamaCloud settings (`llama_cloud_api_key`, `llama_cloud_base_url`, `ocr_timeout_seconds`) |
| `backend/src/galaxy_frog/adapters/vision/llamaparse.py` | New adapter implementing `OCRProvider` via `httpx.AsyncClient` |
| `backend/src/galaxy_frog/adapters/vision/__init__.py` | Export `LlamaParseOCRAdapter` |
| `backend/tests/unit/vision/test_llamaparse.py` | Comprehensive mock unit tests (100% coverage) |
| `docs/phase-4/p4-4-pre-report.md` | This pre-report |

## Required Services / API Keys

- Unit tests require no API key (fully mocked with `respx` / `unittest.mock`).
- Optional live probe requires `LLAMA_CLOUD_API_KEY` in `.env` (configured locally, never committed).

## Acceptance Checks

- [ ] `LlamaParseOCRAdapter` satisfies `isinstance(adapter, OCRProvider)`.
- [ ] Successfully constructs `OCRExtractionResult` with correct `video_id`, `job_id`, `FrameSample` interval inheritance, and `OCRProviderSpec`.
- [ ] Explicit error handling: missing API key, network timeout, rate limit, parse error.
- [ ] Zero unmocked network calls in test suite.
- [ ] 100% statement and branch coverage across all backend modules.
- [ ] `ruff check` and `pyright` pass with 0 errors.
