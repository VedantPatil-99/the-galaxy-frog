"""Stable reasoning cache identity and persistence port, without frame payloads or paths."""

import json
from dataclasses import asdict
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from galaxy_frog.domain.vision.visual import (
    VisualFrame,
    VisualProviderIdentity,
    VisualReasoningRequest,
    VisualReasoningResult,
)


def reasoning_cache_key(request: VisualReasoningRequest, provider: VisualProviderIdentity) -> str:
    value = {
        "video_id": str(request.video_id),
        "provider": asdict(provider),
        "prompt_version": request.prompt_version,
        "evidence_fingerprint": request.evidence_fingerprint,
        "schema_version": request.schema_version,
        "query_evidence_sha256": sha256(
            json.dumps([request.question, request.evidence], ensure_ascii=False).encode()
        ).hexdigest(),
        "frames": [
            {
                "video_id": str(frame.video_id),
                "frame_id": str(frame.frame_id),
                "scene_id": str(frame.scene_id),
                "sequence": frame.sequence,
                "start_ms": frame.start_ms,
                "end_ms": frame.end_ms,
                "capture_ms": frame.capture_ms,
                "source": frame.source,
                "perceptual_hash": asdict(frame.perceptual_hash),
                "content_sha256": frame.content_sha256,
            }
            for frame in request.frames
        ],
    }
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class VisualReasoningCache(Protocol):
    async def get(self, video_id: UUID, key: str) -> VisualReasoningResult | None: ...

    async def put(self, video_id: UUID, key: str, result: VisualReasoningResult) -> None: ...


class ReasoningFrameVerifier(Protocol):
    async def verify(self, frames: tuple[VisualFrame, ...]) -> None: ...
