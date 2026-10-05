# P4.1 Pre-Implementation Report: Vision Domain Models and Provider Port

### Active checkpoint

`feat/scene-and-ocr-pipeline` (clean, on top of all Phase 4 docs commits).

### Scope

Define the **pure-Python domain layer** for vision processing. Zero framework dependencies, zero I/O. Everything else in Phase 4 builds on top of this.

**Files to create (new):**
| File | Content |
|---|---|
| `backend/src/galaxy_frog/domain/vision/__init__.py` | Module re-export |
| `backend/src/galaxy_frog/domain/vision/models.py` | `FrameSample`, `SceneBoundary`, `SceneDetectionResult`, `PerceptualHash`, `DeduplicatedFrameSet`, `OCRProviderSpec`, `OCRBlock`, `OCRExtractionRequest`, `OCRExtractionResult` |
| `backend/src/galaxy_frog/domain/vision/ports.py` | `SceneDetector` protocol, `OCRProvider` protocol |
| `backend/tests/unit/vision/__init__.py` | Empty |
| `backend/tests/unit/vision/test_vision_models.py` | 100% behaviour-driven unit tests for model invariants |

**Nothing is modified.** No `pyproject.toml`, no config, no migrations, no API, no adapter. 

### Domain model design

Following the exact pattern of `domain/media/models.py` and `domain/transcription/models.py` (frozen dataclasses, `__post_init__` invariant guards, StrEnums, no SDK imports):

- **`FrameSample`** — one extracted video frame with `video_id`, `start_ms`/`end_ms` (half-open, ≥0, end > start), `path: Path`, `sequence: int`, `source: Literal["scene_boundary", "periodic"]`
- **`PerceptualHash`** — wraps a hex string hash (e.g., `dhash`) with `algorithm: str` and `bits: int`. Validates hex, length.
- **`SceneBoundary`** — `start_ms`, `end_ms` half-open interval, `score: float ∈ [0,1]`
- **`SceneDetectionResult`** — `video_id`, `duration_ms`, `boundaries: tuple[SceneBoundary, ...]`, `detector: str`, `detector_revision: str`
- **`DeduplicatedFrameSet`** — `kept: tuple[FrameSample, ...]`, `dropped_count: int`, `hash_algorithm: str`, `hamming_threshold: int`
- **`OCRProviderSpec`** — `provider: str`, `provider_revision: str` (follows `TranscriptionProviderSpec` pattern)
- **`OCRBlock`** — one text region: `text: str`, `start_ms`, `end_ms`, `confidence: float | None ∈ [0,1]`, `confidence_method: str | None`, `source_frame_sequence: int`, `provider_spec: OCRProviderSpec`
- **`OCRExtractionRequest`** — `video_id`, `frame: FrameSample`, `job_id: UUID`
- **`OCRExtractionResult`** — `request: OCRExtractionRequest`, `blocks: tuple[OCRBlock, ...]`, `provider_spec: OCRProviderSpec`, `extracted_at: datetime`

**Ports (protocols):**
- `SceneDetector` — `async def detect(self, video_path: Path, duration_ms: int) -> SceneDetectionResult`
- `OCRProvider` — `spec: OCRProviderSpec` attribute + `async def extract(self, request: OCRExtractionRequest) -> OCRExtractionResult`

### Non-goals for P4.1

No adapters, no `pyproject.toml` changes, no config fields, no DB migrations, no ingestion pipeline wiring, no API endpoints.

### Required running services

**None.** Pure unit tests only.

### Required API keys / software

None for this packet.

### Acceptance checks

- All model invariants tested: blank fields, invalid intervals (`end_ms ≤ start_ms`), out-of-range confidence, invalid hash format, timezone-naïve datetimes.
- Protocol structural compatibility tested via `isinstance` (matching `@runtime_checkable` pattern from `AudioAcquirer`/`TranscriptionProvider`).
- `bun run test:backend` passes at 100% coverage.
- `bun run check` (ruff + pyright) passes.

### Risks

None material — this is pure domain modelling with no external dependencies.
