# P4.2 Pre-Implementation Report: Scene Detection and Periodic Frame Sampling

## Scope

1. Add `opencv-python-headless` and `scenedetect` to `backend/pyproject.toml` runtime dependencies.
2. Implement `PySceneDetectAdapter` fulfilling the `SceneDetector` protocol from P4.1.
   - Detect visual scene boundaries using PySceneDetect's `AdaptiveDetector`.
   - Extract one representative JPEG frame per scene boundary using OpenCV.
   - Insert additional periodic JPEG frames inside long static scenes (default: every 10 seconds).
   - Both operations are run in `asyncio.to_thread` to avoid blocking the event loop.
3. Write unit tests maintaining 100% statement and branch coverage.

## Decision: Coupled Extraction (Single Pass)

`PySceneDetectAdapter.detect()` detects boundaries **and** extracts frames in one call so the caller receives a `(SceneDetectionResult, tuple[FrameSample, ...])` pair without a second round-trip. This avoids decoding the video twice and matches the expected pipeline shape in P4.5+.

## Default Periodic Sampling Interval

**10 000 ms (10 seconds)** — configurable via `periodic_interval_ms` constructor argument.

## Affected Files

| File | Change |
|---|---|
| `backend/pyproject.toml` | + `opencv-python-headless>=4.11,<5` + `scenedetect>=0.6.5,<1` |
| `backend/src/galaxy_frog/domain/vision/models.py` | + runtime `isinstance` guards in `FrameSample`, `OCRBlock`, `OCRExtractionRequest`, `OCRExtractionResult` |
| `backend/src/galaxy_frog/adapters/vision/__init__.py` | New package |
| `backend/src/galaxy_frog/adapters/vision/scene_detect.py` | New adapter |
| `backend/tests/unit/vision/test_scene_detect.py` | New test file (98 tests) |

## Required Services / API Keys

- Local execution only. No cloud services or API keys needed.
- No machine-level tool installations; `uv` manages `opencv-python-headless` and `scenedetect`.

## Acceptance Checks

- [x] `ruff check` passes with no errors.
- [x] `pyright` strict mode passes with 0 errors.
- [x] 98 unit tests pass (`test_vision_models.py` + `test_scene_detect.py`).
- [x] 100% statement and branch coverage for `galaxy_frog.domain.vision` and `galaxy_frog.adapters.vision`.
- [x] `PySceneDetectAdapter` satisfies `isinstance(adapter, SceneDetector)` protocol check.
- [x] All `FrameSample` paths are absolute; `sequence` numbers are contiguous from 0.
- [x] Boundaries are strict half-open millisecond intervals matching the `SceneBoundary` model.

## Risks

- **OpenCV install size**: `opencv-python-headless` adds ~40 MB to the package. Headless variant avoids GUI toolkit deps on servers.
- **PySceneDetect stubs**: `scenedetect` ships partial type stubs; suppressed via `pyright: ignore[reportMissingTypeStubs]` and targeted ignores.
- **Video decoding twice**: Boundary detection and frame extraction both decode the video. Acceptable for local use; can be optimised later with PySceneDetect's built-in frame-save API if needed.
