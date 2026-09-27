"""Opt-in checks of the exact provisioned local CUDA reranker and hard deadline."""

import os

import pytest

from galaxy_frog.adapters.reranking.bge import BgeTranscriptReranker
from galaxy_frog.config import Settings
from galaxy_frog.domain.retrieval.reranking import RerankCandidate, RerankerError, RerankerErrorCode
from galaxy_frog.entrypoints.reranker_probe import probe

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_RERANKER_INTEGRATION") != "1",
    reason="set RUN_RERANKER_INTEGRATION=1 with pinned CUDA packages and model cache provisioned",
)


@pytest.mark.asyncio
async def test_real_reranker_handles_full_candidate_budget_offline() -> None:
    report = await probe(full=True)
    assert report["device"] == "cuda"
    assert report["dtype"] == "float16"
    assert report["batch_size"] == 1
    assert report["max_tokens"] == 512


@pytest.mark.asyncio
async def test_real_child_is_terminated_at_deadline() -> None:
    provider = BgeTranscriptReranker(python=Settings().reranker_python, timeout_seconds=0.001)
    with pytest.raises(RerankerError) as caught:
        await provider.rerank("pandas", (RerankCandidate("a" * 64, "Pandas eat bamboo."),))
    assert caught.value.code == RerankerErrorCode.TIMEOUT
