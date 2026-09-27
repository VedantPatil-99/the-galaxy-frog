"""Equal-weight reciprocal-rank fusion without comparing provider score scales."""

from collections import defaultdict
from math import fsum
from uuid import UUID

from galaxy_frog.domain.retrieval.errors import RetrievalIntegrityError
from galaxy_frog.domain.retrieval.pipeline import RankedEvidence, RetrievalStage, StageRank
from galaxy_frog.domain.transcripts.models import RetrievalUnit


def fuse_rankings(
    video_id: UUID, stages: tuple[RetrievalStage, ...], *, constant: int = 60
) -> tuple[RankedEvidence, ...]:
    if constant < 1:
        raise ValueError("RRF constant must be positive")
    units: dict[str, RetrievalUnit] = {}
    ranks: defaultdict[str, list[StageRank]] = defaultdict(list)
    for stage in stages:
        seen: set[str] = set()
        for rank, item in enumerate(stage.results, start=1):
            unit = item.unit
            if item.video_id != video_id:
                raise RetrievalIntegrityError("Retrieved evidence belongs to another video.")
            if unit.unit_id in units and units[unit.unit_id] != unit:
                raise RetrievalIntegrityError("One unit ID has conflicting source evidence.")
            units[unit.unit_id] = unit
            if unit.unit_id in seen:
                continue
            seen.add(unit.unit_id)
            ranks[unit.unit_id].append(StageRank(stage.stage, rank, item.score))
    scores = {
        unit_id: fsum(1 / (constant + rank.rank) for rank in positions)
        for unit_id, positions in ranks.items()
    }
    ordered = sorted(units, key=lambda unit_id: (-scores[unit_id], unit_id))
    return tuple(
        RankedEvidence(video_id, units[unit_id], tuple(ranks[unit_id]), rank, scores[unit_id])
        for rank, unit_id in enumerate(ordered, start=1)
    )
