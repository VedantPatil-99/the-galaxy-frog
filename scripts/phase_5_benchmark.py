"""Frozen closed-pool visual quality, separate from answers and route behavior.

Reads existing source frames/vectors; never downloads, indexes, or uploads. Strict model failure
is recorded and returns nonzero. The optional local pointwise VLM experiment is not a default.
"""

import argparse
import asyncio
import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from galaxy_frog.adapters.vision.experimental_reranking import ReasoningMultimodalReranker
from galaxy_frog.adapters.vision.reasoning import OllamaQwenVisualReasoner
from galaxy_frog.adapters.vision.siglip import SiglipEmbeddingProvider
from galaxy_frog.config import Settings
from galaxy_frog.db.engine import create_database_engine
from galaxy_frog.db.models import EmbeddingCollectionRow, VideoFrameRow, VideoRow
from galaxy_frog.db.visual_models import VisualEmbeddingRow
from galaxy_frog.db.visual_search import _frame
from galaxy_frog.domain.vision.reranking import MultimodalRerankRequest
from galaxy_frog.domain.vision.visual import VisualHit, VisualProviderError

ROOT = Path(__file__).resolve().parents[1]


def dataset_fingerprint(data):
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def metrics(sequences, relevant):
    if not relevant:
        return {"empty_correct": not sequences}
    positives = set(relevant)
    rank = next((i for i, seq in enumerate(sequences, 1) if seq in positives), None)
    return {
        "hit_at_1": bool(sequences and sequences[0] in positives),
        "hit_at_3": any(seq in positives for seq in sequences[:3]),
        "reciprocal_rank": 1 / rank if rank else 0,
    }


async def run(args):
    dataset_bytes = await asyncio.to_thread(args.dataset.read_bytes)
    dataset = json.loads(dataset_bytes)
    settings = Settings()
    provider = SiglipEmbeddingProvider(device="cuda", python=args.python)
    engine = create_database_engine(settings)
    output = {
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": dataset_fingerprint(dataset_bytes),
        "scope": dataset["scope"],
        "embedding": asdict(provider.identity),
        "answer_generation_evaluated": False,
        "rerank_enabled": args.rerank,
        "experiment_is_default": False,
        "baseline": "Visual-only projection of reciprocal-rank fusion; cosine ordering is unchanged",
        "queries": [],
    }
    failed = False
    try:
        async with AsyncSession(engine) as db:
            video = await db.scalar(
                select(VideoRow.id).where(VideoRow.canonical_url == dataset["source"])
            )
            if video is None:
                raise ValueError("frozen source has not been ingested")
            collection = await db.scalar(
                select(EmbeddingCollectionRow.id).filter_by(**asdict(provider.collection))
            )
            rows = (
                await db.execute(
                    select(VideoFrameRow, VisualEmbeddingRow.embedding)
                    .join(VisualEmbeddingRow, VisualEmbeddingRow.frame_id == VideoFrameRow.id)
                    .where(
                        VideoFrameRow.video_id == video,
                        VisualEmbeddingRow.collection_id == collection,
                        VideoFrameRow.sequence.in_([f["sequence"] for f in dataset["frames"]]),
                    )
                )
            ).all()
            if len(rows) != len(dataset["frames"]):
                raise ValueError("frozen frame vectors are incomplete")
            annotations = {f["sequence"]: f for f in dataset["frames"]}
            for row, _ in rows:
                frozen = annotations[row.sequence]
                actual = hashlib.sha256(
                    await asyncio.to_thread(Path(row.image_path).read_bytes)
                ).hexdigest()
                if (
                    any(
                        getattr(row, key) != frozen[key]
                        for key in ("start_ms", "end_ms", "capture_ms", "content_sha256")
                    )
                    or actual != frozen["content_sha256"]
                ):
                    raise ValueError("frozen source pixels or provenance changed")
        reranker = ReasoningMultimodalReranker(
            OllamaQwenVisualReasoner(base_url=settings.ollama_base_url)
        )
        for item in dataset["queries"]:
            started = perf_counter()
            record = {"id": item["id"], "category": item["category"]}
            eligible = [
                (row, values)
                for row, values in rows
                if "window" not in item or item["window"][0] <= row.capture_ms < item["window"][1]
            ]
            try:
                vector = await provider.embed_query(item["query"]) if eligible else None
                ranked = (
                    sorted(
                        [
                            (row, sum(float(a) * b for a, b in zip(values, vector, strict=True)))
                            for row, values in eligible
                        ],
                        key=lambda pair: (-pair[1], pair[0].sequence),
                    )
                    if vector is not None
                    else []
                )
                hits = tuple(
                    VisualHit(
                        _frame(row),
                        provider.collection,
                        max(-1, min(1, score)),
                        i,
                        collection_id=collection,
                    )
                    for i, (row, score) in enumerate(ranked, 1)
                )
                record.update(
                    baseline_sequences=[h.frame.sequence for h in hits],
                    baseline_metrics=metrics([h.frame.sequence for h in hits], item["relevant"]),
                    baseline_ms=(perf_counter() - started) * 1000,
                )
                if args.rerank and item["category"] in {"visual-only", "mixed"}:
                    result = await reranker.rerank(MultimodalRerankRequest(item["query"], hits[:4]))
                    record.update(
                        reranked_sequences=[h.frame.sequence for h in result.ranked],
                        rerank_metrics=metrics(
                            [h.frame.sequence for h in result.ranked], item["relevant"]
                        ),
                        rerank_grades=list(result.grades),
                        rerank_ms=result.elapsed_ms,
                        rerank_provider=asdict(result.provider),
                        prompt_version=result.prompt_version,
                    )
            except VisualProviderError as exc:
                failed = True
                record["failure_code"] = exc.code.value
            output["queries"].append(record)
            args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(record), flush=True)
        output["complete"] = True
        output["baseline_passed"] = all(
            record.get("baseline_metrics", {}).get("empty_correct", False)
            or record.get("baseline_metrics", {}).get("hit_at_3", False)
            for record in output["queries"]
        )
        output["strict_experiment_failed"] = failed
        args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    finally:
        await engine.dispose()
    return int(failed or not output["baseline_passed"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=ROOT / "docs/phase-5/visual-v1.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--rerank", action="store_true")
    raise SystemExit(asyncio.run(run(parser.parse_args())))
