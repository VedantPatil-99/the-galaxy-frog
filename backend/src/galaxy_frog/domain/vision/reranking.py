"""Experimental multimodal ranking port; every candidate retains its original source objects."""

from dataclasses import dataclass
from math import isfinite
from typing import Protocol

from galaxy_frog.domain.retrieval.models import RetrievedEvidence
from galaxy_frog.domain.vision.models import RetrievedOCREvidence
from galaxy_frog.domain.vision.visual import VisualHit, VisualProviderIdentity


@dataclass(frozen=True, slots=True)
class MultimodalRerankRequest:
    question: str
    frames: tuple[VisualHit, ...]
    transcript: tuple[RetrievedEvidence, ...] = ()
    ocr: tuple[RetrievedOCREvidence, ...] = ()

    def __post_init__(self) -> None:
        if not self.question.strip() or len(self.question) > 1500 or not 1 <= len(self.frames) <= 4:
            raise ValueError("experiment requires a bounded question and one to four frames")
        video = self.frames[0].frame.video_id
        if (
            len({hit.frame.frame_id for hit in self.frames}) != len(self.frames)
            or any(hit.frame.video_id != video for hit in self.frames)
            or any(item.video_id != video for item in (*self.transcript, *self.ocr))
            or len(self.transcript) > 8
            or len(self.ocr) > 8
            or sum(len(item.unit.text) for item in self.transcript)
            + sum(len(item.block.text) for item in self.ocr)
            > 3000
        ):
            raise ValueError("experiment candidates violate evidence scope or budget")


@dataclass(frozen=True, slots=True)
class MultimodalRerankResult:
    original: MultimodalRerankRequest
    # Ordinal relevance grades, not calibrated probabilities or embedding similarity.
    grades: tuple[int, ...]
    provider: VisualProviderIdentity
    elapsed_ms: float
    prompt_version: str = "visual-rerank-experiment-v1"

    def __post_init__(self) -> None:
        if len(self.grades) != len(self.original.frames) or any(
            g not in range(4) for g in self.grades
        ):
            raise ValueError("experiment requires one relevance grade from zero to three per frame")
        if not isfinite(self.elapsed_ms) or self.elapsed_ms < 0:
            raise ValueError("experiment timing must be finite and nonnegative")

    @property
    def ranked(self) -> tuple[VisualHit, ...]:
        # Stable ties retain fusion ordering; source objects and their original ranks never change.
        return tuple(
            self.original.frames[index]
            for index in sorted(range(len(self.grades)), key=lambda i: -self.grades[i])
        )


class MultimodalReranker(Protocol):
    async def rerank(self, request: MultimodalRerankRequest) -> MultimodalRerankResult: ...
