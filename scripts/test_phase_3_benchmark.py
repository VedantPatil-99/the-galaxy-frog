"""Metric checks independent of services and model quality."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from phase_3_benchmark import fingerprint, metrics, overlap_score, scored, summarize, trial


class MetricsTests(unittest.TestCase):
    def test_strict_trial_rejects_fallback_and_unresolved_results(self):
        for degraded, status in ((True, "resolved"), (False, "anchor_selection_required")):
            with (
                patch("phase_3_benchmark.GpuSampler") as gpu,
                patch("phase_3_benchmark.request") as http,
            ):
                gpu.return_value.__enter__.return_value = SimpleNamespace(samples=[], error=None)
                http.return_value = {"trace_id": "trace", "degraded": degraded, "status": status}
                result = trial(
                    "http://localhost", {"id": "case", "video_id": "video"}, "reranked", "query"
                )
                self.assertEqual(result["status"], "failed")
                self.assertFalse(http.call_args.args[2]["allow_fallback"])
                self.assertEqual(http.call_count, 1)

    def test_scoring_uses_trace_order_and_original_transcript_ids(self):
        case = {"origin": "caption", "gold_unit_ids": ["b"], "gold_intervals_ms": [[10, 20]]}
        transcript = {
            "retrieval_units": [
                {"retrieval_unit_id": "a", "start_ms": 0, "end_ms": 10},
                {"retrieval_unit_id": "b", "start_ms": 10, "end_ms": 20},
            ]
        }
        record = {
            "status": "passed",
            "result": {
                "warnings": [],
                "evidence": [
                    {"start_ms": 0, "end_ms": 30, "units": [{"unit_id": "b"}]},
                ],
            },
            "trace": {
                "providers": {},
                "configuration": {},
                "retrieval": {
                    "candidates": [{"unit_id": "b"}, {"unit_id": "a"}],
                    "stages": [],
                },
            },
        }
        self.assertEqual(scored(record, case, transcript, "dense")["mrr_at_10"], 1)
        self.assertEqual(
            scored(record, case, transcript, "expanded", True)["temporal_iou_at_5"], 1 / 3
        )
        failed = scored({"status": "failed"}, case, transcript, "dense")
        self.assertNotIn("mrr_at_10", failed)

    def test_rank_cutoffs_and_partial_recall(self):
        groups = [[str(index)] for index in range(12)]
        result = metrics(groups, ["2", "6"], [[i, i + 1] for i in range(12)], [[2, 3]])
        self.assertEqual(result["recall_at_5"], 0.5)
        self.assertAlmostEqual(result["mrr_at_10"], 1 / 3)
        self.assertEqual(result["temporal_iou_at_5"], 0.2)
        self.assertEqual(metrics(groups, ["10"], [], [[0, 1]])["mrr_at_10"], 0)

    def test_group_deduplication_and_empty_results(self):
        result = metrics([["a", "b"], ["a"]], ["a", "b"], [[0, 10], [5, 15]], [[0, 10]])
        self.assertEqual(result["recall_at_5"], 1)
        self.assertEqual(result["mrr_at_10"], 1)
        self.assertAlmostEqual(result["temporal_iou_at_5"], 2 / 3)
        self.assertEqual(metrics([], ["a"], [], [[0, 1]])["recall_at_5"], 0)
        with self.assertRaises(ValueError):
            metrics([], [], [], [])

    def test_half_open_union_and_expansion_penalty(self):
        self.assertEqual(overlap_score([[0, 10]], [[10, 20]]), 0)
        self.assertEqual(overlap_score([[0, 100]], [[40, 60]]), 0.2)
        self.assertEqual(overlap_score([[0, 10], [5, 15]], [[0, 15]]), 1)
        self.assertEqual(overlap_score([], []), 0)
        with self.assertRaises(ValueError):
            overlap_score([[2, 2]], [])

    def test_fingerprint_rejects_evidence_drift_but_not_video_uuid(self):
        original = {"cues": [{"text": "same"}], "retrieval_units": [], "transcription": None}
        self.assertEqual(fingerprint(original), fingerprint({**original, "video": "new uuid"}))
        self.assertNotEqual(fingerprint(original), fingerprint({**original, "cues": []}))

    def test_failed_trials_are_counted_not_silently_zero_scored(self):
        failed = {"variant": "dense", "status": "failed", "device_peak_used_mib": None}
        result = summarize([failed])["dense"]
        self.assertEqual(result["failed"], 1)
        self.assertIsNone(result["recall_at_5"])
        self.assertIsNone(result["median_request_ms"])
        passed = {
            **failed,
            "status": "passed",
            "recall_at_5": 1,
            "mrr_at_10": 0.5,
            "temporal_iou_at_5": 0.2,
            "request_ms": 100,
            "device_peak_used_mib": 1100,
        }
        result = summarize([failed, passed])["dense"]
        self.assertEqual((result["attempted"], result["passed"], result["failed"]), (2, 1, 1))
        self.assertEqual(result["recall_at_5"], 1)


if __name__ == "__main__":
    unittest.main()
