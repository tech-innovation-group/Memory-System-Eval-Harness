from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.reproduce_memory_profile import build_result
from scripts.retest_memory_profile import _ratio


class ReproduceMemoryProfileTests(unittest.TestCase):
    def test_live_ratio_reads_mean_and_percentile_units(self) -> None:
        low = {"mean_seconds": 4.0, "percentiles_seconds": {"p50": 2.0, "p95": 8.0}}
        high = {"mean_seconds": 2.0, "percentiles_seconds": {"p50": 1.0, "p95": 4.0}}
        self.assertEqual(0.5, _ratio(low, high, "mean_s"))
        self.assertEqual(0.5, _ratio(low, high, "p50_s"))
        self.assertEqual(0.5, _ratio(low, high, "p95_s"))

    def _write_jsonl(self, directory: Path, rows: list[dict]) -> Path:
        path = directory / "echomem.jsonl"
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )
        return path

    def test_groups_labeled_16_and_64_samples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            log = self._write_jsonl(
                directory,
                [
                    {
                        "event": "recall_stage_completed",
                        "stage": "memory_profile",
                        "scenario": "sessions",
                        "concurrency": 16,
                        "trace_id": "trace-16",
                        "duration_ms": 36.0,
                        "queue_wait_ms": 1.0,
                    },
                    {
                        "event": "recall_stage_completed",
                        "stage": "memory_profile",
                        "scenario": "sessions",
                        "concurrency": 64,
                        "trace_id": "trace-64",
                        "duration_ms": 3876.2,
                        "queue_wait_ms": 2.0,
                    },
                ],
            )

            result = build_result(type("Args", (), {"log": log, "metrics": None,
                                                     "before": None, "after": None,
                                                     "reference": None})())

            source = result["sources"][0]
            self.assertTrue(source["comparison_ready"])
            self.assertEqual(36.0, source["by_scenario"]["sessions/C=16"]["timing_ms"]["p95"])
            self.assertEqual(3876.2, source["by_scenario"]["sessions/C=64"]["timing_ms"]["p95"])
            self.assertEqual(
                107.6722,
                source["comparisons"][0]["timing_ratio_64_div_16"]["p95"],
            )
            self.assertTrue(result["reproducible_16_64"])

    def test_unlabeled_samples_cannot_become_16_64_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = self._write_jsonl(
                Path(tmp),
                [{
                    "event": "recall_stage_completed",
                    "stage": "memory_profile",
                    "trace_id": "trace-1",
                    "duration_ms": 0.023,
                    "queue_wait_ms": 0.0,
                    "status": "embedding_failed",
                }],
            )

            result = build_result(type("Args", (), {"log": log, "metrics": None,
                                                     "before": None, "after": None,
                                                     "reference": None})())

            source = result["sources"][0]
            self.assertEqual("MEASURED", source["status"])
            self.assertFalse(source["comparison_ready"])
            self.assertFalse(result["reproducible_16_64"])
            self.assertIn("场景边界", result["conclusion"])

    def test_single_prometheus_snapshot_is_cumulative_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            metrics = Path(tmp) / "metrics.prom"
            metrics.write_text(
                'echomem_memrouter_stage_duration_seconds_count{stage="memory_profile"} 81\n'
                'echomem_memrouter_stage_duration_seconds_sum{stage="memory_profile"} 1.0\n',
                encoding="utf-8",
            )

            result = build_result(type("Args", (), {"log": None, "metrics": metrics,
                                                     "before": None, "after": None,
                                                     "reference": None})())

            source = result["sources"][0]
            self.assertEqual("CUMULATIVE_ONLY", source["status"])
            self.assertFalse(source["comparison_ready"])
            self.assertFalse(result["reproducible_16_64"])


if __name__ == "__main__":
    unittest.main()
