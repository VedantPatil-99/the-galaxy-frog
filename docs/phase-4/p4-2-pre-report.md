# P4.2 Pre-Implementation Report: Scene Detection and Periodic Frame Sampling

### Scope
1. Add `scenedetect` and `opencv-python-headless` to `backend/pyproject.toml`. (Verified with `uv` that these resolve cleanly in the locked Python 3.14.7 environment).
2. Implement a `PySceneDetectAdapter` that fulfills the `SceneDetector` protocol from P4.1 to detect visual boundaries using `scenedetect`'s `AdaptiveDetector` (or `ContentDetector`).
3. Define and implement a mechanism (e.g. a `FrameSampler` port and adapter) to physically extract `FrameSample` images at those boundaries and at periodic intervals to cover long, static scenes.
4. Write unit tests for detection and sampling using dummy/fixture videos.

### Likely Affected Files
- `backend/pyproject.toml` (Dependencies).
- `backend/src/galaxy_frog/domain/vision/ports.py` (Add a `FrameSampler` protocol to keep image extraction decoupled from detection).
- `backend/src/galaxy_frog/providers/local/scene_detect.py` (New adapter).
- `backend/tests/unit/vision/test_scene_detect.py` (New tests).

### Required Services / API Keys
- Local execution only. No cloud services or API keys needed.
- No machine-level tool installations required, as `uv` will manage `opencv-python-headless`.

### Acceptance Checks & Risks
- **Check**: Boundaries are reported as strict half-open millisecond intervals matching the `SceneBoundary` model.
- **Check**: Frame images are saved in a temporary workspace directory, yielding absolute paths with correct `FrameSource` labels.
- **Risk**: Extracting images separately from detection requires decoding the video twice. We can mitigate this by passing an extraction parameter if the chosen library supports it, or simply rely on `FFmpeg`/`PySceneDetect` being fast enough for local use.

### Unresolved Product Decisions (Pending Approval)
1. **Extraction Approach**: `PySceneDetect` can detect boundaries *and* save the scene-start frames in one pass. Should we couple boundary detection and frame extraction into one step for performance, or keep them decoupled so `SceneDetector` only returns boundaries, and a separate `FrameSampler` (using FFmpeg or OpenCV) handles image extraction?
2. **Periodic Sampling Rate**: What should the default interval be for periodic frame sampling? (e.g., 1 frame every 10 seconds for scenes that are long?)
