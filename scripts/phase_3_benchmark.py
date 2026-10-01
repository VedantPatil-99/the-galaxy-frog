"""Frozen Phase 3 acceptance benchmark against the real FastAPI service, without fallback.

This is a local exit-gate script, not an evaluation service. It never ingests, downloads models,
changes provider settings, or generates answers. Failed trials remain in the output and exit nonzero.
"""

import argparse
import hashlib
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def fingerprint(transcript):
    evidence = {key: transcript[key] for key in ("cues", "retrieval_units", "transcription")}
    return hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def merge(intervals):
    merged = []
    for start, end in sorted(intervals):
        if start < 0 or end <= start:
            raise ValueError("Intervals must be positive and half-open")
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return merged


def overlap_score(predicted, expected):
    predicted, expected = merge(predicted), merge(expected)
    intersection = sum(max(0, min(b, d) - max(a, c)) for a, b in predicted for c, d in expected)
    union = sum(b - a for a, b in merge(predicted + expected))
    return intersection / union if union else 0.0


def metrics(ranked_groups, gold_units, intervals, gold_intervals):
    """Unit recall in the first five ranked items; reciprocal first relevant item by rank ten.

    Baselines use singleton units. Expanded retrieval uses groups containing original units.
    Temporal IoU uses the union of the first five item intervals, penalizing excessive expansion.
    """
    gold = set(gold_units)
    if not gold:
        raise ValueError("Gold units must not be empty")
    found = {unit for group in ranked_groups[:5] for unit in group}
    reciprocal_rank = next(
        (1 / rank for rank, group in enumerate(ranked_groups[:10], 1) if gold.intersection(group)),
        0.0,
    )
    return {
        "recall_at_5": len(found & gold) / len(gold),
        "mrr_at_10": reciprocal_rank,
        "temporal_iou_at_5": overlap_score(intervals[:5], gold_intervals),
    }


def request(base_url, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=310) as response:
        return json.load(response)


class GpuSampler:
    """Device-wide memory includes Ollama and other applications, not just this request."""

    def __init__(self):
        self.samples = []
        self.error = None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.sample, daemon=True)

    def sample(self):
        while not self.stop.is_set():
            try:
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=5,
                )
                self.samples.append(sum(float(value) for value in result.stdout.splitlines()))
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                self.error = type(exc).__name__
                return
            self.stop.wait(2)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=6)


def trial(base_url, case, mode, query):
    started = time.perf_counter()
    record = {"case_id": case["id"], "mode": mode, "query": query}
    with GpuSampler() as gpu:
        try:
            result = request(
                base_url,
                f"/v1/videos/{case['video_id']}/search",
                {
                    "query": query,
                    "mode": mode,
                    "limit": 8,
                    "allow_fallback": False,
                },
            )
            record["request_ms"] = (time.perf_counter() - started) * 1000
            record["trace_id"] = result["trace_id"]
            if result["degraded"] or result["status"] != "resolved":
                raise ValueError("Strict benchmark requires resolved, non-degraded retrieval")
            trace = request(
                base_url, f"/v1/videos/{case['video_id']}/retrieval-traces/{result['trace_id']}"
            )
            record.update(status="passed", result=result, trace=trace["payload"])
        except urllib.error.HTTPError as exc:
            try:
                error = json.loads(exc.read())
            except (ValueError, UnicodeDecodeError):
                error = {"error": {"code": f"HTTP_{exc.code}"}}
            record.update(status="failed", error=error)
        except (OSError, ValueError, KeyError) as exc:
            record.update(status="failed", error={"error": {"code": type(exc).__name__}})
    record.setdefault("request_ms", (time.perf_counter() - started) * 1000)
    record["device_peak_used_mib"] = max(gpu.samples) if gpu.samples else None
    record["memory_sampling_error"] = gpu.error
    return record


def scored(record, case, transcript, variant, expanded=False):
    output = {key: value for key, value in record.items() if key not in ("result", "trace")}
    output["variant"] = variant
    output["origin"] = case["origin"]
    if record["status"] != "passed":
        return output
    trace = record["trace"]
    retrieval = trace["retrieval"]
    if expanded:
        groups = record["result"]["evidence"]
        rankings = [[unit["unit_id"] for unit in group["units"]] for group in groups]
        intervals = [[group["start_ms"], group["end_ms"]] for group in groups]
    else:
        units = {unit["retrieval_unit_id"]: unit for unit in transcript["retrieval_units"]}
        rankings = [[hit["unit_id"]] for hit in retrieval["candidates"]]
        intervals = [[units[group[0]]["start_ms"], units[group[0]]["end_ms"]] for group in rankings]
    output.update(metrics(rankings, case["gold_unit_ids"], intervals, case["gold_intervals_ms"]))
    reranking = retrieval.get("reranking") or {}
    native = reranking.get("result") or {}
    output.update(
        ranked_unit_groups=rankings,
        predicted_intervals_ms=intervals,
        warnings=record["result"]["warnings"],
        providers=trace["providers"],
        configuration=trace["configuration"],
        stage_timings_ms={stage["stage"]: stage["elapsed_ms"] for stage in retrieval["stages"]},
        reranker={key: value for key, value in native.items() if key != "scores"},
        truncated_pairs=sum(score["truncated"] for score in native.get("scores", [])),
    )
    return output


def summarize(rows):
    summary = {}
    for variant in sorted({row["variant"] for row in rows}):
        trials = [row for row in rows if row["variant"] == variant]
        passed = [row for row in trials if row["status"] == "passed"]
        stats = {
            "attempted": len(trials),
            "passed": len(passed),
            "failed": len(trials) - len(passed),
        }
        for key in ("recall_at_5", "mrr_at_10", "temporal_iou_at_5"):
            stats[key] = mean(row[key] for row in passed) if passed else None
        stats["median_request_ms"] = median(row["request_ms"] for row in passed) if passed else None
        stats["max_device_used_mib"] = max(
            (
                row["device_peak_used_mib"]
                for row in trials
                if row["device_peak_used_mib"] is not None
            ),
            default=None,
        )
        summary[variant] = stats
    return summary


def save_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".pending.json")
    pending.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(pending, path)


def run(args):
    manifest = json.loads(args.dataset.read_text(encoding="utf-8"))
    if manifest["version"] != 1:
        raise ValueError("Unsupported benchmark version")
    transcripts = {}
    for video in manifest["videos"]:
        transcript = request(args.api, f"/v1/videos/{video['video_id']}/transcript")
        if fingerprint(transcript) != video["transcript_sha256"]:
            raise ValueError(
                f"Transcript drift for {video['video_id']}; do not relabel after scoring"
            )
        transcripts[video["video_id"]] = transcript
    report = {
        "version": 1,
        "started_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "git_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "fallback_allowed": False,
        "api": args.api,
        "ollama_models": request(args.ollama, "/api/tags"),
        "notes": [
            "One attempt per trial; failures retained, never silently retried or replaced.",
            "Quality aggregates cover successful trials only; inspect failure counts before comparisons.",
            "Dense/fused/reranked measure original unit rankings before expansion using baseline_query.",
            "Expanded measures up to eight groups with query constraints; MRR@10 sees at most eight groups.",
            "Request latency includes service expansion/tracing even for pre-expansion ranking metrics.",
            "Expanded reuses the reranked request when queries match; its latency is not an extra trial.",
            "Device memory samples include other processes; reranker allocator peaks are separately recorded.",
            "Small transcript-derived acceptance set; no general quality or human relevance claim.",
        ],
        "rows": [],
    }
    save_report(args.output, report)
    for case in manifest["cases"]:
        transcript = transcripts[case["video_id"]]
        for mode, variant in (("dense", "dense"), ("hybrid", "fused"), ("reranked", "reranked")):
            print(f"{case['id']}: {variant}", flush=True)
            result = trial(args.api, case, mode, case["baseline_query"])
            report["rows"].append(scored(result, case, transcript, variant))
            if mode == "reranked":
                expanded = (
                    result
                    if case["query"] == case["baseline_query"]
                    else trial(args.api, case, mode, case["query"])
                )
                report["rows"].append(scored(expanded, case, transcript, "expanded", expanded=True))
            report["summary"] = summarize(report["rows"])
            save_report(args.output, report)
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["status"] = (
        "passed" if all(row["status"] == "passed" for row in report["rows"]) else "failed"
    )
    save_report(args.output, report)
    print(json.dumps(report["summary"], indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    parser.add_argument(
        "--dataset", type=Path, default=REPOSITORY_ROOT / "docs/phase-3/benchmark-v1.json"
    )
    parser.add_argument(
        "--output", type=Path, default=REPOSITORY_ROOT / "tmp/phase-3-benchmark.json"
    )
    raise SystemExit(run(parser.parse_args()))
