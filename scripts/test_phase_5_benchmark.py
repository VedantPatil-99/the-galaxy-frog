"""Frozen labels, capture boundaries, and metric semantics; no inference or network calls."""

import hashlib
import json
import runpy
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
metrics = runpy.run_path(str(ROOT / "scripts/phase_5_benchmark.py"))["metrics"]


class VisualBenchmarkTests(unittest.TestCase):
    def test_metrics_distinguish_rank_failure_and_empty_windows(self):
        self.assertEqual(metrics([8, 3], [3])["reciprocal_rank"], 0.5)
        self.assertFalse(metrics([8], [3])["hit_at_3"])
        self.assertEqual(metrics([], [3])["reciprocal_rank"], 0)
        self.assertTrue(metrics([], [])["empty_correct"])
        self.assertFalse(metrics([3], [])["empty_correct"])

    def test_frozen_ground_truth_respects_capture_and_original_intervals(self):
        frozen = json.loads((ROOT / "docs/phase-5/visual-v1.json").read_text())
        frames = {f["sequence"]: f for f in frozen["frames"]}
        self.assertEqual(len(frames), 8)
        for frame in frames.values():
            self.assertLessEqual(frame["start_ms"], frame["capture_ms"])
            self.assertLess(frame["capture_ms"], frame["end_ms"])
            self.assertEqual(len(frame["content_sha256"]), 64)
        for item in frozen["queries"]:
            self.assertTrue(set(item["relevant"]).issubset(frames))
            if "window" in item:
                available = {
                    seq
                    for seq, f in frames.items()
                    if item["window"][0] <= f["capture_ms"] < item["window"][1]
                }
                self.assertEqual(set(item["relevant"]), available)
        # Same scene, different capture times: the periodic frame must not enter [26s,30s).
        self.assertEqual(frames[5]["start_ms"], frames[6]["start_ms"])
        self.assertNotEqual(frames[5]["capture_ms"], frames[6]["capture_ms"])

    def test_saved_measurements_match_the_frozen_dataset(self):
        digest = hashlib.sha256((ROOT / "docs/phase-5/visual-v1.json").read_bytes()).hexdigest()
        baseline = json.loads((ROOT / "docs/phase-5/visual-v1-local-result.json").read_text())
        experiment = json.loads(
            (ROOT / "docs/phase-5/visual-v1-rerank-local-result.json").read_text()
        )
        self.assertEqual(baseline["dataset_sha256"], digest)
        self.assertEqual(experiment["dataset_sha256"], digest)
        self.assertTrue(baseline["complete"])
        self.assertEqual(len(baseline["queries"]), 8)
        self.assertFalse(baseline["answer_generation_evaluated"])
        self.assertFalse(experiment["experiment_is_default"])
        self.assertEqual(sum("failure_code" in q for q in experiment["queries"]), 2)


if __name__ == "__main__":
    unittest.main()
