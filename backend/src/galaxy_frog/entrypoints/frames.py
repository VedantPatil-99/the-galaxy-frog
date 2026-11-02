"""Bounded child entry point connecting the existing scene/hash/deduplication pipeline."""

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

from galaxy_frog.adapters.vision.deduplication import OpenCVDHashDeduplicator, compute_dhash_sync
from galaxy_frog.adapters.vision.scene_detect import PySceneDetectAdapter


async def execute(request_path: Path) -> None:
    payload = json.loads(await asyncio.to_thread(request_path.read_text, encoding="utf-8"))
    detector = PySceneDetectAdapter(
        workspace_dir=Path(payload["workspace"]),
        periodic_interval_ms=10000,
        max_frames=payload["max_frames"],
    )
    scenes, frames = await detector.detect(
        Path(payload["source"]), UUID(payload["video_id"]), payload["duration_ms"]
    )
    deduplication = await OpenCVDHashDeduplicator().deduplicate(frames)
    kept = {frame.sequence for frame in deduplication.kept}
    result = {
        "scenes": [asdict(boundary) for boundary in scenes.boundaries],
        "detector": scenes.detector,
        "revision": scenes.detector_revision,
        "frames": [
            {
                "sequence": frame.sequence,
                "start_ms": frame.start_ms,
                "end_ms": frame.end_ms,
                "source": frame.source,
                "kept": frame.sequence in kept,
                "dhash": compute_dhash_sync(frame.path).hex_digest,
            }
            for frame in frames
        ],
    }
    await asyncio.to_thread(
        Path(payload["result"]).write_text, json.dumps(result), encoding="utf-8"
    )


def main() -> None:
    try:
        asyncio.run(execute(Path(sys.argv[1])))
    except Exception:
        # Native errors and local paths never enter durable provider details.
        raise SystemExit(1) from None


if __name__ == "__main__":  # pragma: no cover
    main()
