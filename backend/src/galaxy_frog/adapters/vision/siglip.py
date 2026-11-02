"""Bounded offline SigLIP subprocess. Native packages and weights are user provisioned."""

import asyncio
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from galaxy_frog.adapters.media.process import AsyncSubprocessRunner, CommandRunner, CommandTimedOut
from galaxy_frog.domain.retrieval.models import EmbeddingCollectionSpec
from galaxy_frog.domain.vision.visual import (
    ExecutionLocation,
    VisualEmbedding,
    VisualFailureCode,
    VisualFrame,
    VisualProviderError,
    VisualProviderIdentity,
    validate_vector,
)

SIGLIP_MODEL = "google/siglip-base-patch16-224"
SIGLIP_REVISION = "7fd15f0689c79d79e38b1c2e2e2370a7bf2761ed"


class _Response(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    model: str
    revision: str
    device: str
    vectors: list[list[float]] = Field(min_length=1, max_length=4)


class SiglipEmbeddingProvider:
    def __init__(
        self,
        *,
        device: Literal["cpu", "cuda"],
        python: Path | None = None,
        timeout_seconds: float = 120,
        runner: CommandRunner | None = None,
    ) -> None:
        if device not in {"cpu", "cuda"} or not 0 < timeout_seconds <= 600:
            raise ValueError("SigLIP requires explicit device and bounded deadline")
        self.identity = VisualProviderIdentity(
            "transformers-siglip", SIGLIP_MODEL, SIGLIP_REVISION, ExecutionLocation.LOCAL
        )
        self.collection = EmbeddingCollectionSpec(
            self.identity.provider, SIGLIP_MODEL, SIGLIP_REVISION, 768, "l2"
        )
        self._device = device
        self._python = python or Path(sys.executable)
        self._deadline = timeout_seconds
        self._runner = runner or AsyncSubprocessRunner()
        self._lock = asyncio.Lock()

    async def embed_query(self, query: str) -> tuple[float, ...]:
        if not query.strip() or len(query) > 2000:
            raise ValueError("visual query must contain one to 2000 characters")
        return (await self._execute({"query": query}, expected=1))[0]

    async def embed_frames(self, frames: tuple[VisualFrame, ...]) -> tuple[VisualEmbedding, ...]:
        if not 1 <= len(frames) <= 4 or len({f.frame_id for f in frames}) != len(frames):
            raise ValueError("embedding batch requires one to four distinct frames")
        vectors = await self._execute(
            {"paths": [str(frame.path) for frame in frames]}, expected=len(frames)
        )
        return tuple(
            VisualEmbedding(frame, self.collection, vector)
            for frame, vector in zip(frames, vectors, strict=True)
        )

    async def _execute(
        self, payload: dict[str, object], *, expected: int
    ) -> tuple[tuple[float, ...], ...]:
        if self._lock.locked():
            raise VisualProviderError(VisualFailureCode.BUSY)
        async with self._lock:
            try:
                with TemporaryDirectory(prefix="galaxy-frog-visual-") as directory:
                    request = Path(directory) / "request.json"
                    request.write_text(
                        json.dumps(
                            {
                                **payload,
                                "model": SIGLIP_MODEL,
                                "revision": SIGLIP_REVISION,
                                "device": self._device,
                            }
                        ),
                        encoding="utf-8",
                    )
                    result = await self._runner.run(
                        (
                            str(self._python),
                            str(Path(__file__).with_name("siglip_runtime.py")),
                            str(request),
                        ),
                        cwd=Path(directory),
                        timeout_seconds=self._deadline,
                    )
            except CommandTimedOut as exc:
                raise VisualProviderError(VisualFailureCode.TIMEOUT) from exc
            except OSError as exc:
                raise VisualProviderError(VisualFailureCode.UNAVAILABLE) from exc
            if result.return_code != 0:
                raise VisualProviderError(VisualFailureCode.UNAVAILABLE)
            try:
                response = _Response.model_validate_json(result.stdout)
                if (response.model, response.revision, response.device) != (
                    SIGLIP_MODEL,
                    SIGLIP_REVISION,
                    self._device,
                ) or len(response.vectors) != expected:
                    raise ValueError("unexpected visual provider identity or batch")
                vectors = tuple(tuple(row) for row in response.vectors)
                for vector in vectors:
                    validate_vector(vector, self.collection)
            except ValueError as exc:
                raise VisualProviderError(VisualFailureCode.INVALID_OUTPUT) from exc
            return vectors
