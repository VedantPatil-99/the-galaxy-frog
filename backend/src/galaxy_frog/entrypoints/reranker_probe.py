"""Offline provider probe with real bounds, child lifecycle, and observable runtime metadata."""

import argparse
import asyncio
import json
from dataclasses import asdict
from hashlib import sha256

from galaxy_frog.adapters.reranking.bge import BgeTranscriptReranker
from galaxy_frog.config import Settings
from galaxy_frog.domain.retrieval.reranking import RerankCandidate


def build_reranker(settings: Settings) -> BgeTranscriptReranker:
    return BgeTranscriptReranker(
        python=settings.reranker_python,
        timeout_seconds=settings.reranker_timeout_seconds,
    )


async def probe(*, full: bool = False, settings: Settings | None = None) -> dict[str, object]:
    passages = [
        "Giant pandas eat bamboo.",
        "PostgreSQL stores searchable text.",
        "Giant pandas eat bamboo. " * 600,
    ]
    if full:
        passages += ["Pandas eat bamboo. 熊猫吃竹子。" for _ in range(27)]
    candidates = tuple(
        RerankCandidate(sha256(str(index).encode()).hexdigest(), passage)
        for index, passage in enumerate(passages)
    )
    result = await build_reranker(settings or Settings()).rerank(
        "Which animal eats bamboo?", candidates
    )
    if result.scores[0].score <= result.scores[1].score or not result.scores[2].truncated:
        raise RuntimeError("Reranker ranking/truncation sanity check failed")
    return asdict(result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="Exercise the maximum 30 candidates")
    arguments = parser.parse_args()
    print(json.dumps(asyncio.run(probe(full=arguments.full)), indent=2))


if __name__ == "__main__":  # pragma: no cover - module entry point
    main()
