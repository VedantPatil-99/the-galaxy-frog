# P4.3 Pre-Implementation Report: Perceptual Hashing and Frame Deduplication

## Scope

1. Add perceptual hashing and deduplication to the vision adapter layer (`galaxy_frog.adapters.vision.deduplication` or `hasher.py`).
2. Implement `ImageHashAdapter` fulfilling a `FrameDeduplicator` protocol:
   - Compute perceptual hash (default: `dhash` / difference hash, 64-bit) for each `FrameSample` image.
   - Compare sequential or pairwise frame hashes using Hamming distance.
   - If Hamming distance $\le$ `hamming_threshold` (default: 4 bits out of 64, representing >93% visual similarity), consider the frame a near-duplicate of the preceding kept frame and drop it.
   - Always retain the first frame of each scene boundary to preserve scene context.
   - Return a `DeduplicatedFrameSet` containing the kept `FrameSample` objects, `dropped_count`, `hash_algorithm`, and `hamming_threshold`.
3. Add protocol definition `FrameDeduplicator` to `galaxy_frog.domain.vision.ports`.
4. Provide unit tests maintaining 100% statement and branch coverage.

## Non-Goals for P4.3

- No cloud OCR calls (LlamaParse is P4.4).
- No database persistence or migrations (OCR persistence is P4.5).
- No API endpoint changes or frontend UI changes (P4.6, P4.7).
- No deletion of disk files for dropped frames yet (caller/pipeline lifecycle in P4.5 manages temporary file cleanup).

## Dependency Evaluation

- **Candidate A (`imagehash` + `Pillow`)**: Standard library for perceptual hashing (ahash, phash, dhash, whash). Requires `Pillow`, `scipy`, `pywavelets`. Resolves cleanly in Python 3.14 on Windows.
- **Candidate B (`opencv-python-headless` + custom dhash/phash or OpenCV `img_hash`)**: OpenCV is already installed from P4.2 (`cv2`). Computing 64-bit difference hash (dhash) with OpenCV or numpy takes only ~10 lines of pure code (grayscale -> resize 9x8 -> compare adjacent pixels -> pack 64 bits to hex) without adding scipy/pywavelets.
- **Recommendation**: Support `dhash` natively via OpenCV/Pillow without unnecessary heavy dependencies, or use `imagehash` if advanced algorithms (whash) are required. Since `dhash` is the proven industry standard for video slide deduplication, a lightweight OpenCV/Pillow implementation avoids adding scipy (~50 MB) while fully honoring `bits=64` and `algorithm="dhash"` in `PerceptualHash`.

## Affected Files

| File | Change |
|---|---|
| `backend/src/galaxy_frog/domain/vision/ports.py` | Add `FrameDeduplicator` protocol |
| `backend/src/galaxy_frog/adapters/vision/deduplication.py` | New adapter implementing `FrameDeduplicator` |
| `backend/src/galaxy_frog/adapters/vision/__init__.py` | Export deduplication adapter |
| `backend/tests/unit/vision/test_deduplication.py` | Unit tests for deduplication & hashing (100% coverage) |
| `docs/phase-4/p4-3-pre-report.md` | This pre-report |

## Required Services / API Keys

- Local execution only. Zero external services, zero API keys, zero network access.

## Acceptance Checks

- [ ] `FrameDeduplicator` protocol defined with `@runtime_checkable`.
- [ ] Hasher produces valid 64-bit hexadecimal `PerceptualHash` instances matching `domain.vision.models.PerceptualHash`.
- [ ] Identical images yield Hamming distance 0 and are deduplicated.
- [ ] Completely different images yield high Hamming distance and are both kept.
- [ ] Near-identical slide frames (e.g. speaker minor movement or cursor blink below threshold) are deduplicated, conserving cloud OCR credits.
- [ ] Scene boundary initial frames are preserved according to policy.
- [ ] Contiguous 100% statement and branch coverage across all vision modules and backend test suite.
- [ ] `ruff check` and `pyright` pass with 0 errors.

## Risks & Mitigations

- **Threshold sensitivity**: If Hamming threshold is too high, distinct slides with similar layout might be dropped. If too low, minor compression noise prevents deduplication. Default threshold of 4 (out of 64 bits) is conservative and configurable.
