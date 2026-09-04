from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from performance.acceptance import (
    FAIL,
    INCONCLUSIVE,
    NOT_IMPLEMENTED,
    PR28_REVIEW_RESOLUTION,
    build_model_analysis_input,
    evaluate_pr421_acceptance,
)
from performance.formal_data_report import render


class AcceptanceTests(unittest.TestCase):
    def test_missing_measurements_are_inconclusive_and_unavailable_are_explicit(self):
        result = evaluate_pr421_acceptance({"runs": []})
        self.assertEqual(INCONCLUSIVE, result["overall"])
        statuses = {item["status"] for item in result["checks"]}
        self.assertIn(INCONCLUSIVE, statuses)
        self.assertIn("INCONCLUSIVE", statuses)

    def test_report6_quality_gate_rejects_empty_marker_results(self):
        manifest = {
            "runs": [{
                "scenario": "A@1",
                "summary": {
                    "metrics": {
                        "search": {
                            "quality_asserted": 10,
                            "quality_failures": 2,
                        }
                    },
                    "details": {
                        "quality_seed": [
                            {"status": "completed"},
                        ]
                    },
                },
            }]
        }
        result = evaluate_pr421_acceptance(manifest)
        quality = next(
            item for item in result["checks"]
            if item["name"] == "report(6) Search quality assertion"
        )
        self.assertEqual("FAIL", quality["status"])
        self.assertEqual(2, quality["observed"]["quality_failures"])

    def test_model_input_is_secret_free_and_preserves_acceptance(self):
        manifest = {
            "base_url": "http://127.0.0.1:8010",
            "scenarios": ["saturation"],
            "repeats": 1,
            "client_admission_enabled": False,
            "server_observation_mode": True,
            "runs": [],
        }
        acceptance = evaluate_pr421_acceptance(manifest)
        payload = build_model_analysis_input(manifest, acceptance)
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertIn("PR421", encoded)
        self.assertNotIn("api_key", encoded.lower())
        self.assertIn("NOT_IMPLEMENTED", encoded)

    def test_html_renders_acceptance_matrix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {
                "base_url": "http://127.0.0.1:8010",
                "repeats": 1,
                "runs": [],
                "acceptance": {
                    "overall": "INCONCLUSIVE",
                    "checks": [
                        {
                            "name": "B7 lane/fan-out metrics",
                            "status": "INCONCLUSIVE",
                            "target": "6 metric families",
                            "observed": {"missing": ["lane_exec"]},
                            "reason": "缺失服务端指标",
                            "evidence": "details.pr421_metric_coverage",
                        }
                    ],
                    "review": {
                        "reasonable_targets": ["分离成功延迟与超时率"],
                        "missing_or_weak_targets": ["需要游标对账"],
                    },
                },
            }
            path = root / "suite.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            output = root / "suite.html"
            render(path, output)
            document = output.read_text(encoding="utf-8")
            self.assertIn("PR421 验收矩阵", document)
            self.assertIn("需要游标对账", document)
            self.assertIn("INCONCLUSIVE", document)

    def test_html_renders_review_resolution_when_present(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            acceptance = evaluate_pr421_acceptance({"runs": []})
            manifest = {"runs": [], "acceptance": acceptance}
            path = root / "suite.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            output = root / "suite.html"
            render(path, output)
            document = output.read_text(encoding="utf-8")
            self.assertIn("PR28 检视意见闭环", document)
            self.assertIn("Commit barrier and tenant distributions", document)
        self.assertIn("PARTIAL", document)

    def test_saturation_without_rejections_does_not_claim_contract_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "saturation" / "repeat-01" / "server-observe"
            run_dir.mkdir(parents=True)
            (run_dir / "search_results.csv").write_text(
                "status_code,end_to_end_s\n200,0.1\n200,0.2\n",
                encoding="utf-8",
            )
            result = evaluate_pr421_acceptance(
                {
                    "runs": [
                        {
                            "scenario": "saturation",
                            "output_dir": str(run_dir),
                            "summary": {},
                        }
                    ]
                }
            )
            check = next(
                item for item in result["checks"]
                if item["name"] == "Saturation rejection rate"
            )
            self.assertEqual(INCONCLUSIVE, check["status"])

    def test_saturation_rejection_requires_reason_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "saturation" / "repeat-01" / "server-observe"
            run_dir.mkdir(parents=True)
            (run_dir / "search_results.csv").write_text(
                "status_code,end_to_end_s,retry_after_s,reason_code\n"
                "503,0.2,1,\n",
                encoding="utf-8",
            )
            result = evaluate_pr421_acceptance(
                {
                    "runs": [{
                        "scenario": "saturation",
                        "output_dir": str(run_dir),
                        "summary": {},
                    }]
                }
            )
            check = next(
                item for item in result["checks"]
                if item["name"] == "Saturation rejection rate"
            )
            self.assertEqual(FAIL, check["status"])

    def test_saturation_counts_explicit_400_admission_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "saturation" / "repeat-01" / "server-observe"
            run_dir.mkdir(parents=True)
            (run_dir / "search_results.csv").write_text(
                "status_code,end_to_end_s,error_class,retry_after,reason_code,error_detail\n"
                "200,0.01,,,,\n"
                "400,0.02,admission_rejected,1,,too many recall requests in flight\n",
                encoding="utf-8",
            )
            result = evaluate_pr421_acceptance(
                {
                    "runs": [{
                        "scenario": "saturation",
                        "output_dir": str(run_dir),
                        "summary": {},
                    }]
                }
            )
            check = next(
                item for item in result["checks"]
                if item["name"] == "Saturation rejection rate"
            )
            self.assertEqual(FAIL, check["status"])
            self.assertEqual(1, check["observed"]["rejected"])
            self.assertEqual({"400": 1}, check["observed"]["status_breakdown"])
            self.assertFalse(check["observed"]["wire_status_complete"])
            self.assertTrue(check["observed"]["retry_after_complete"])

    def test_report4_invalid_baseline_cannot_produce_degradation_pass(self):
        result = evaluate_pr421_acceptance(
            {
                "runs": [
                    {
                        "scenario": "A-c4",
                        "summary": {
                            "metrics": {
                                "search": {
                                    "success_rate": 0.8,
                                    "latency": {"p95_s": 1.0},
                                }
                            }
                        },
                    },
                    {
                        "scenario": "D-c4",
                        "summary": {
                            "metrics": {
                                "search": {
                                    "success_rate": 1.0,
                                    "latency": {"p95_s": 1.1},
                                }
                            }
                        },
                    },
                ]
            }
        )
        check = next(
            item for item in result["checks"]
            if item["name"] == "Search P95 isolation ratio"
        )
        self.assertEqual(INCONCLUSIVE, check["status"])
        self.assertIn("invalid_baselines", check["observed"])

    def test_metric_label_violation_is_not_a_coverage_pass(self):
        result = evaluate_pr421_acceptance(
            {
                "runs": [{
                    "summary": {
                        "details": {
                            "pr421_metric_coverage": {
                                "present": {
                                    "lane_queued": True,
                                    "lane_wait": True,
                                    "lane_exec": True,
                                    "lane_rejected": True,
                                    "engine_exec": True,
                                    "engine_skipped": True,
                                },
                                "missing": [],
                                "bounded_label_violations": [{
                                    "metric": "echomem_lane_queued",
                                    "label": "tenant_id",
                                    "value": "tenant-a",
                                }],
                            }
                        }
                    }
                }]
            }
        )
        check = next(
            item for item in result["checks"]
            if item["name"] == "B7 lane/fan-out metrics"
        )
        self.assertEqual(INCONCLUSIVE, check["status"])

    def test_bounded_lane_and_fanout_evidence_passes(self):
        lanes = (
            "recall_engine",
            "recall_intent_llm",
            "recall_query_embedding",
            "recall_rerank",
            "commit",
        )
        result = evaluate_pr421_acceptance(
            {
                "runs": [{
                    "summary": {
                        "details": {
                            "pr421_metric_coverage": {
                                "present": {
                                    "echomem_lane_queued": True,
                                    "echomem_lane_wait_seconds": True,
                                    "echomem_lane_exec_seconds": True,
                                    "echomem_lane_rejected_total": True,
                                    "echomem_engine_fanout_exec_seconds": True,
                                    "echomem_engine_fanout_skipped_total": True,
                                },
                                "missing": [],
                                "bounded_label_violations": [],
                                "lane_quartets": {
                                    lane: {
                                        "queued": True,
                                        "wait": True,
                                        "exec": True,
                                        "rejected": True,
                                    }
                                    for lane in lanes
                                },
                                "fanout_engines": {
                                    "memory": {"exec": True, "skipped": True},
                                },
                            }
                        }
                    }
                }]
            }
        )
        check = next(
            item for item in result["checks"]
            if item["name"] == "B7 lane/fan-out metrics"
        )
        self.assertEqual("PASS", check["status"])
        self.assertEqual(sorted(lanes), check["observed"]["complete_lanes"])
        self.assertEqual(["memory"], check["observed"]["complete_fanout_engines"])

    def test_bounded_lane_evidence_without_all_lanes_is_inconclusive(self):
        result = evaluate_pr421_acceptance(
            {
                "runs": [{
                    "summary": {
                        "details": {
                            "pr421_metric_coverage": {
                                "present": {},
                                "missing": [],
                                "bounded_label_violations": [],
                                "lane_quartets": {
                                    "commit": {
                                        "queued": True,
                                        "wait": True,
                                        "exec": True,
                                        "rejected": True,
                                    }
                                },
                                "fanout_engines": {
                                    "memory": {"exec": True, "skipped": True},
                                },
                            }
                        }
                    }
                }]
            }
        )
        check = next(
            item for item in result["checks"]
            if item["name"] == "B7 lane/fan-out metrics"
        )
        self.assertEqual(INCONCLUSIVE, check["status"])
        self.assertIn("recall_engine", check["observed"]["missing_lanes"])

    def test_legacy_evidence_with_missing_metric_family_is_inconclusive(self):
        result = evaluate_pr421_acceptance(
            {
                "runs": [{
                    "summary": {
                        "details": {
                            "pr421_metric_coverage": {
                                "present": {},
                                "missing": ["echomem_lane_wait_seconds"],
                                "bounded_label_violations": [],
                                "per_tenant_quartets": {
                                    "a": {
                                        "queued": True,
                                        "wait": True,
                                        "exec": True,
                                        "rejected": True,
                                    },
                                    "b": {
                                        "queued": True,
                                        "wait": True,
                                        "exec": True,
                                        "rejected": True,
                                    },
                                },
                            }
                        }
                    }
                }]
            }
        )
        check = next(
            item for item in result["checks"]
            if item["name"] == "B7 lane/fan-out metrics"
        )
        self.assertEqual(INCONCLUSIVE, check["status"])

    def test_fairness_uses_commit_completion_throughput(self):
        result = evaluate_pr421_acceptance(
            {
                "runs": [{
                    "scenario": "tenant-skew",
                    "summary": {
                        "metrics": {
                            "fairness": {
                                "commit_completed_per_tenant": {
                                    "a": 10, "b": 10, "c": 10, "d": 10,
                                }
                            }
                        }
                    }
                }]
            }
        )
        check = next(
            item for item in result["checks"]
            if item["name"] == "Tenant fairness (Jain)"
        )
        self.assertEqual("PASS", check["status"])
        self.assertEqual(1.0, check["observed"])

    def test_review_resolution_is_explicit_and_model_visible(self):
        acceptance = evaluate_pr421_acceptance({"runs": []})
        statuses = {item["status"] for item in PR28_REVIEW_RESOLUTION}
        self.assertTrue({"RESOLVED", "PARTIAL"} <= statuses)
        self.assertEqual(PR28_REVIEW_RESOLUTION, acceptance["pr28_review_resolution"])
        model_input = build_model_analysis_input(
            {"scenarios": [], "repeats": 0},
            acceptance,
        )
        self.assertEqual(
            PR28_REVIEW_RESOLUTION,
            model_input["acceptance"]["pr28_review_resolution"],
        )


if __name__ == "__main__":
    unittest.main()
