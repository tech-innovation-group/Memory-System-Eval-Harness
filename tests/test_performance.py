"""Unit tests for the performance stress-test module.

Covers: Prometheus text parsing, percentile math, record summarization,
degradation factors, scenario-matrix expansion, loadgen write
transactions against a fake client, error classification, and the
external-deployment (static identity) guard. No real server is touched.

运行:  cd Memory-System-Eval-Harness && python -m unittest tests.test_performance -v
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from backends.memory_types import CommitResult, SearchResult
from performance.loadgen import (
    LoadGenerator,
    RequestRecord,
    classify_error,
    extract_reason_code,
    is_anchor_query,
    mix_token_sequence,
    retry_decision,
    run_write_transaction,
    split_threads,
)
from performance.metrics_calc import (
    burst_summary,
    commit_completion_latency,
    commit_durability,
    consistency_summary,
    degradation_factor,
    degradation_measurements,
    error_type_validation,
    evaluate_features,
    fairness_measurements,
    fault_injection_summary,
    hot_tenant_summary,
    injected_bytes_series,
    isolation_probe_summary,
    isolation_summary,
    percentile,
    percentiles,
    read_records_in_window,
    reconcile_messages,
    retry_summary,
    rss_normalized_series,
    rss_trend_mb_per_min,
    saturation_summary,
    search_quality_summary,
    summarize_records,
    tenant_fairness,
    jain_fairness,
)
from performance.monitor import (
    CPU_SECONDS,
    MetricsFrame,
    MetricsMonitor,
    parse_prometheus_text,
)
from performance.perf_mock_provider import (
    MockProvider,
    probe,
    run_fault_sequence,
)
from performance.perf_preflight import (
    check_env,
    config_digest,
    parse_engine_configs,
    probe_endpoint,
    _retryable_probe_failure,
    run_preflight,
)
from performance.prepare import (
    build_search_query_pool,
    TenantContext,
    TenantPreparer,
    _format_prepare_error,
    _query_fragments,
    load_locomo_seed_batches,
    load_tenant_specs,
    seed_tenant,
    seed_tenant_from_conversations,
)
from performance.report import (
    _cpu_stats_from_csv,
    _estimate_text_width,
    _legend_html,
    build_html,
    chart_series_from_metrics_csv,
    regenerate_report,
)
from performance.run_stress import _resolve_args
from performance.scenarios import (
    SceneRun,
    expand_matrix,
    parse_concurrency_steps,
    parse_mix_ratio,
)

PROMETHEUS_SAMPLE = """\
# HELP a_counter Total counter.
# TYPE a_counter counter
a_counter 3
a_counter 5
# TYPE b_gauge gauge
b_gauge{label="x",other="y"} 1.5
m_bucket{le="0.1"} 2
m_bucket{le="0.5"} 4
m_bucket{le="+Inf"} 4
m_sum 1.2
m_count 4
# EOF
"""


class PrometheusParseTests(unittest.TestCase):
    def test_parse_basic(self) -> None:
        parsed = parse_prometheus_text(PROMETHEUS_SAMPLE)
        self.assertIn("a_counter", parsed)
        # 同 name+labels 的后续样本覆盖前者
        self.assertEqual([value for _, value in parsed["a_counter"]], [5.0])
        self.assertEqual(
            parsed["b_gauge"][0][0],
            {"label": "x", "other": "y"},
        )
        self.assertEqual(parsed["b_gauge"][0][1], 1.5)

    def test_parse_histogram_keeps_buckets_and_aggregates(self) -> None:
        parsed = parse_prometheus_text(PROMETHEUS_SAMPLE)
        buckets = {labels.get("le"): value for labels, value in parsed["m_bucket"]}
        self.assertEqual(buckets, {"0.1": 2.0, "0.5": 4.0, "+Inf": 4.0})
        self.assertEqual(parsed["m_count"][0][1], 4.0)
        self.assertEqual(parsed["m_sum"][0][1], 1.2)


class PercentileTests(unittest.TestCase):
    def test_percentile_linear_interpolation(self) -> None:
        values = sorted([10.0, 20.0, 30.0, 40.0])
        self.assertEqual(percentile(values, 0.5), 25.0)
        self.assertEqual(percentile(values, 0.0), 10.0)
        self.assertEqual(percentile(values, 1.0), 40.0)

    def test_percentile_empty(self) -> None:
        self.assertIsNone(percentile([], 0.5))

    def test_percentiles_labels(self) -> None:
        result = percentiles([1.0, 2.0, 3.0, 4.0], (0.5, 0.95, 0.99))
        self.assertEqual(result["p50"], 2.5)
        self.assertIn("p95", result)
        self.assertIn("p99", result)


class SummarizeTests(unittest.TestCase):
    @staticmethod
    def _record(op, stage, status="ok", error="", scene="A@1", conc=1, tenant=0, ts=0.0):
        return RequestRecord(
            scene_key=scene,
            step_conc=conc,
            tenant_idx=tenant,
            op=op,
            stage_ms=stage,
            status=status,
            error_type=error,
            ts_ms=ts,
        )

    def test_summarize_reads_and_errors(self) -> None:
        records = [
            self._record("read", 10.0),
            self._record("read", 20.0),
            self._record("read", 30.0, "error", "timeout"),
            self._record("open", 5.0, "error", "http_5xx"),
        ]
        summary = summarize_records(records, wall_s=1.0)
        read = summary["A@1"]["read"]
        self.assertEqual(read["count"], 3)
        self.assertEqual(read["qps"], 3.0)
        self.assertEqual(read["p50_ms"], 20.0)
        self.assertEqual(read["errors_total"], 1)
        self.assertEqual(read["error_breakdown"], {"timeout": 1})
        self.assertAlmostEqual(read["error_rate"], 1 / 3, places=5)
        self.assertEqual(summary["A@1"]["open"]["errors_total"], 1)

    def test_degradation_factor(self) -> None:
        baseline = {"p50_ms": 10.0, "p95_ms": 40.0, "p99_ms": 100.0}
        target = {"p50_ms": 20.0, "p95_ms": 80.0, "p99_ms": 300.0}
        factors = degradation_factor(baseline, target)
        self.assertEqual(factors["p50"], 2.0)
        self.assertEqual(factors["p95"], 2.0)
        self.assertEqual(factors["p99"], 3.0)

    def test_degradation_missing_side_is_none(self) -> None:
        factors = degradation_factor(None, {"p50_ms": 1.0})
        self.assertEqual(factors, {"p50": None, "p95": None, "p99": None})

    def test_window_slice(self) -> None:
        records = [
            self._record("read", 1.0, ts=1.0),
            self._record("read", 2.0, ts=2.0),
            self._record("open", 3.0, ts=3.0),
        ]
        in_window = read_records_in_window(records, 0.0, 1.5)
        self.assertEqual(len(in_window), 1)
        self.assertEqual(in_window[0].stage_ms, 1.0)

    def test_consistency_summary(self) -> None:
        records = [
            self._record("consistent_check", 500.0),
            self._record("consistent_check", 1500.0),
            self._record("consistent_check", 30000.0, "error", "consistency_timeout"),
        ]
        summary = consistency_summary(records)
        self.assertEqual(summary["count"], 3)
        self.assertEqual(summary["p50_ms"], 1500.0)
        self.assertEqual(summary["timeouts"], 1)

    def test_burst_summary(self) -> None:
        burst = [self._record("read", 50.0), self._record("read", 90.0)]
        baseline = [self._record("read", 10.0), self._record("read", 30.0)]
        result = burst_summary(burst, baseline)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["degradation"]["p50"], 3.5)  # 70/20

    def test_commit_completion_latency(self) -> None:
        records = [
            self._record("commit_done", 100.0),
            self._record("commit_done", 200.0),
            self._record("commit_done", 300.0),
            self._record("commit_done", 900.0, "error", "commit_timeout"),
            self._record("read", 10.0),
        ]
        stats = commit_completion_latency(records)
        self.assertEqual(stats["count"], 3)
        self.assertEqual(stats["p50_ms"], 200.0)
        self.assertEqual(stats["p95_ms"], 290.0)
        self.assertEqual(stats["max_ms"], 300.0)

    def test_degradation_measurements(self) -> None:
        summary = {
            "scenes": {
                "A@4": {"ops": {"read": {"p50_ms": 20.0, "p95_ms": 40.0, "p99_ms": 100.0}}},
                "D@4": {"ops": {"read": {"p50_ms": 60.0, "p95_ms": 120.0, "p99_ms": 400.0}}},
            },
            "degradation": {"D@4_vs_A@4": {"p50": 3.0, "p95": 3.0, "p99": 4.0}},
        }
        result = degradation_measurements(summary)
        case = result["D@4_vs_A@4"]
        self.assertEqual(case["ratio_p95"], 3.0)
        self.assertEqual(case["baseline_p95_ms"], 40.0)
        self.assertEqual(case["flood_p95_ms"], 120.0)
        self.assertEqual(case["delta_p95_ms"], 80.0)

    def test_fairness_measurements(self) -> None:
        fairness = {
            "A@4": {
                "tenants": [
                    {"tenant_idx": 0, "count": 10, "p95_ms": 20.0, "p99_ms": 30.0},
                    {"tenant_idx": 1, "count": 10, "p95_ms": 200.0, "p99_ms": 400.0},
                ],
                "p95_max_min_ratio": 10.0,
                "p95_cv": 0.8,
                "balanced": False,
            }
        }
        result = fairness_measurements(fairness)
        self.assertEqual(result["worst_scene"], "A@4")
        self.assertEqual(result["slowest_tenant_idx"], 1)
        self.assertEqual(result["slowest_tenant_p95_ms"], 200.0)
        self.assertEqual(result["slowest_tenant_p99_ms"], 400.0)
        self.assertEqual(result["slowest_waits_extra_ms"], 180.0)

    def test_jain_fairness_is_across_tenants(self) -> None:
        self.assertEqual(jain_fairness([10, 10, 10, 10]), 1.0)
        self.assertAlmostEqual(jain_fairness([40, 5, 5, 5]), 0.4515, places=4)
        self.assertIsNone(jain_fairness([10]))


class ScenarioMatrixTests(unittest.TestCase):
    def test_parse_mix_ratio(self) -> None:
        self.assertEqual(parse_mix_ratio("8:1"), (8, 1))
        with self.assertRaises(ValueError):
            parse_mix_ratio("0:0")

    def test_parse_concurrency_steps(self) -> None:
        self.assertEqual(parse_concurrency_steps("1,4,16"), [1, 4, 16])
        with self.assertRaises(ValueError):
            parse_concurrency_steps("1,0")

    def test_expand_matrix_counts(self) -> None:
        runs = expand_matrix(
            scenario_ids=["A", "B"],
            concurrency_steps=[1, 4],
            mix_ratios=[(8, 1)],
            duration_s=60.0,
            burst_commits=32,
            burst_window_s=10.0,
        )
        keys = [run.key for run in runs]
        self.assertEqual(keys, ["A@1", "A@4", "B@1", "B@4"])

    def test_expand_matrix_c_and_d(self) -> None:
        runs = expand_matrix(
            scenario_ids=["C", "D"],
            concurrency_steps=[4],
            mix_ratios=[(8, 1), (4, 1)],
            duration_s=60.0,
            burst_commits=16,
            burst_window_s=5.0,
        )
        self.assertEqual(runs[0].key, "C:8:1@4")
        self.assertEqual(runs[1].key, "C:4:1@4")
        self.assertEqual(runs[2].key, "D@4")
        self.assertEqual(runs[2].burst_commits, 16)

    def test_expand_matrix_unknown_scenario(self) -> None:
        with self.assertRaises(ValueError):
            expand_matrix(
                scenario_ids=["Z"],
                concurrency_steps=[1],
                mix_ratios=[],
                duration_s=10.0,
                burst_commits=1,
                burst_window_s=1.0,
            )

    def test_expand_matrix_single_shot_scenarios(self) -> None:
        # S/H/K/I 为单发场景：各只产出一个 SceneRun，追加在 A/B/C/D 展开之后。
        runs = expand_matrix(
            scenario_ids=["A", "S", "H", "K", "I"],
            concurrency_steps=[1, 4],
            mix_ratios=[(8, 1)],
            duration_s=60.0,
            burst_commits=32,
            burst_window_s=10.0,
            barrier_commits=128,
        )
        keys = [run.key for run in runs]
        self.assertEqual(keys, ["A@1", "A@4", "S@1", "H@1", "K@1", "I@1"])
        s = runs[2]
        self.assertEqual(s.scene_id, "S")
        self.assertEqual(s.burst_commits, 128)  # S 的 burst_commits 用 barrier 参数
        self.assertEqual(s.barrier_commits, 128)
        self.assertEqual(s.barrier_distribution, "uniform")
        h = runs[3]
        self.assertEqual(h.scene_id, "H")
        # 无 explicit counts 时 H 回退到 CLI 传入的 barrier 参数
        self.assertEqual(h.barrier_commits, 128)
        self.assertEqual(h.barrier_distribution, "uniform")

    def test_expand_matrix_single_shot_h_explicit_counts(self) -> None:
        runs = expand_matrix(
            scenario_ids=["H"],
            concurrency_steps=[4],
            mix_ratios=[(8, 1)],
            duration_s=120.0,
            burst_commits=32,
            burst_window_s=10.0,
            barrier_commits=0,
            barrier_tenant_counts=[200, 20, 20, 20],
            barrier_waves=2,
            barrier_cooldown_s=5.0,
        )
        self.assertEqual(len(runs), 1)
        h = runs[0]
        self.assertEqual(h.scene_id, "H")
        self.assertEqual(h.key, "H@4")
        self.assertEqual(h.barrier_commits, 260)  # explicit 计数总和
        self.assertEqual(h.barrier_distribution, "explicit")
        self.assertEqual(h.barrier_tenant_counts, [200, 20, 20, 20])
        self.assertEqual(h.barrier_waves, 2)
        self.assertEqual(h.barrier_cooldown_s, 5.0)


class LoadgenTests(unittest.TestCase):
    def test_mix_token_sequence(self) -> None:
        self.assertEqual(
            mix_token_sequence(2, 1, 7),
            ["read", "read", "write", "read", "read", "write", "read"],
        )

    def test_split_threads(self) -> None:
        self.assertEqual(split_threads(8, (8, 1)), (7, 1))
        self.assertEqual(split_threads(8, (1, 1)), (4, 4))

    def test_classify_error(self) -> None:
        self.assertEqual(classify_error(TimeoutError("slow")), "timeout")
        self.assertEqual(
            classify_error(urllib.error.HTTPError("http://x", 503, "boom", None, None)),
            "http_5xx",
        )
        self.assertEqual(
            classify_error(urllib.error.HTTPError("http://x", 429, "full", None, None)),
            "http_4xx",
        )
        self.assertEqual(classify_error(urllib.error.URLError("refused")), "connection")
        self.assertEqual(classify_error(ValueError("weird")), "other")


class FakeMemClient:
    """Records calls; can be armed to fail at a given step."""

    def __init__(self) -> None:
        self.open_calls = 0
        self.add_calls = 0
        self.commit_calls = 0
        self.delete_calls = 0
        self.messages: list[str] = []
        self.poll_status = "completed"
        self.poll_elapsed_s = 1.5
        self.poll_failures_left = 0
        self.fail_step = ""
        self.account = "fake-tenant"
        self.user_id = "fake-user"
        self.auth_key = "fake-key"
        # -- write retry / reconciliation / quality knobs -------------------
        self.commit_attempts = 0
        self.commit_failures_left = 0  # 抛 503 的次数（之后成功）
        self.commit_429_left = 0  # 抛 429+Retry-After 的次数
        self.commit_400 = False  # 抛 400（不可重试）
        self.commit_reason_code = ""  # 429 响应头附加的 reason_code
        self.archive_status = "completed"
        self.search_short_circuit = False  # 普通查询短路空响应
        self.anchor_short_circuit = False  # 锚词查询短路空响应
        self.search_degraded = False  # 核心标记降级（引擎跳过/饱和）
        self.retry_after_s = "1"

    def delete_current_identity(self) -> None:
        if self.fail_step == "delete":
            raise RuntimeError("delete refused")
        self.delete_calls += 1

    def open_session(self, title: str = ""):
        if self.fail_step == "open":
            raise urllib.error.URLError("refused")
        self.open_calls += 1
        return "session-1"

    def add_message(self, session_id: str, role: str, content: str):
        if self.fail_step == "add":
            raise TimeoutError("slow")
        self.add_calls += 1
        self.messages.append(content)
        return {"message_id": f"msg-{self.add_calls}"}

    def commit_session(self, session_id: str):
        self.commit_attempts += 1
        if self.fail_step == "commit":
            raise urllib.error.HTTPError("http://x", 503, "busy", None, None)
        if self.commit_400:
            raise urllib.error.HTTPError("http://x", 400, "bad request", None, None)
        if self.commit_failures_left > 0:
            self.commit_failures_left -= 1
            raise urllib.error.HTTPError("http://x", 503, "busy", None, None)
        if self.commit_429_left > 0:
            self.commit_429_left -= 1
            exc = urllib.error.HTTPError("http://x", 429, "limited", None, None)
            exc.headers = {"Retry-After": self.retry_after_s}
            if self.commit_reason_code:
                exc.headers["x-reason-code"] = self.commit_reason_code
            raise exc
        self.commit_calls += 1
        return "archive-1"

    def poll_commit(self, session_id: str, archive_id: str, timeout_s=0.0, poll_interval_s=0.0):
        if self.fail_step == "poll":
            raise TimeoutError("poll slow")
        if self.poll_failures_left > 0:
            self.poll_failures_left -= 1
            return CommitResult(session_id, archive_id, "failed", self.poll_elapsed_s, 2)
        return CommitResult(session_id, archive_id, self.poll_status, self.poll_elapsed_s, 2)

    # -- reconciliation / search quality -----------------------------------

    def session_history(self, session_id: str, limit: int = 200):
        if self.fail_step == "history":
            raise RuntimeError("no history endpoint")
        return [
            {"id": f"msg-{i + 1}", "content": content}
            for i, content in enumerate(self.messages)
        ]

    def session_archives(self, session_id: str, limit: int = 50):
        if self.fail_step == "archives":
            raise RuntimeError("no archives endpoint")
        return [{"status": self.archive_status}]

    def commit_memories(self, session_id: str, archive_id: str):
        if self.fail_step == "memories":
            raise RuntimeError("no memories endpoint")
        return {
            "atoms": [
                {"source_turn_ids": [f"msg-{i + 1}" for i in range(len(self.messages))]}
            ]
        }

    def search_with_meta(
        self,
        query: str,
        top_k: int = 10,
        session_id: str = "",
        agent_id: str = "",
        timeout_s: float | None = None,
    ):
        if "PERFANCHOR" in query or "PERFTAIL" in query:
            items = (
                []
                if self.anchor_short_circuit
                else [SearchResult(uri="echo://x", score=1.0, content=query)]
            )
        elif self.search_short_circuit:
            items = []
        else:
            items = [SearchResult(uri="echo://y", score=0.5, content="ordinary hit")]
        meta = {
            "has_explain": True,
            "has_debug": False,
            "hit_count": len(items),
            "status": "degraded" if self.search_degraded else "completed",
            "degraded_reasons": ["engine:atomic_engine"] if self.search_degraded else [],
        }
        return items, meta

    def search(self, query: str, top_k: int = 10, session_id: str = "", agent_id: str = "", timeout_s: float | None = None):
        items, _ = self.search_with_meta(query, top_k=top_k)
        return items


class WriteTransactionTests(unittest.TestCase):
    def _run(self, fail_step: str = ""):
        client = FakeMemClient()
        client.fail_step = fail_step
        result = run_write_transaction(
            client,
            scene_key="B@1",
            step_conc=1,
            tenant_idx=0,
            seq=7,
            messages_per_session=10,
            commit_poll_timeout_s=30.0,
        )
        return client, result

    def test_full_transaction_timing(self) -> None:
        client, result = self._run()
        self.assertTrue(result.ok)
        self.assertEqual(client.open_calls, 1)
        self.assertEqual(client.add_calls, 10)
        self.assertEqual(client.commit_calls, 1)
        ops = [rec.op for rec in result.records]
        self.assertEqual(ops, ["open"] + ["add"] * 10 + ["commit_submit", "commit_done"])
        self.assertEqual(result.anchor, "PERFTAIL-0-7")
        self.assertEqual(result.archive_id, "archive-1")
        self.assertEqual(result.records[-2].archive_id, "archive-1")
        self.assertEqual(result.records[-1].archive_id, "archive-1")
        self.assertTrue(
            result.records[-1].stage_ms >= 1500.0
        )  # poll_commit elapsed 1.5s

    def test_open_failure_stops_transaction(self) -> None:
        _, result = self._run("open")
        self.assertFalse(result.ok)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(result.records[0].op, "open")
        self.assertEqual(result.records[0].error_type, "connection")

    def test_add_failure_classified_timeout(self) -> None:
        _, result = self._run("add")
        self.assertEqual(result.records[0].error_type, "")
        self.assertEqual(result.records[-1].error_type, "timeout")
        self.assertEqual(result.records[-1].op, "add")

    def test_commit_submit_5xx(self) -> None:
        _, result = self._run("commit")
        self.assertEqual(result.records[-1].op, "commit_submit")
        self.assertEqual(result.records[-1].error_type, "http_5xx")

    def test_poll_timeout(self) -> None:
        _, result = self._run("poll")
        self.assertEqual(result.records[-1].op, "commit_done")
        self.assertEqual(result.records[-1].error_type, "timeout")
        self.assertEqual(result.records[-1].archive_id, "archive-1")


class MonitorAnalyticsTests(unittest.TestCase):
    def _monitor_with_frames(self, frames: list[MetricsFrame]) -> MetricsMonitor:
        monitor = MetricsMonitor("http://test", interval_s=1.0)
        monitor.frames = frames
        return monitor

    def _cpu_frame(self, ts: float, user: float, system: float, rss: float) -> MetricsFrame:
        return MetricsFrame(
            ts=ts,
            samples={
                CPU_SECONDS: [
                    ({"mode": "user"}, user),
                    ({"mode": "system"}, system),
                ],
                "echomem_process_resident_memory_bytes": [({}, rss)],
            },
        )

    def test_counter_delta(self) -> None:
        monitor = self._monitor_with_frames(
            [
                self._cpu_frame(1.0, 8.0, 2.0, 100.0),
                self._cpu_frame(2.0, 12.0, 3.0, 130.0),
            ]
        )
        # 帧 t=1 的 cpu = user(8) + system(2) = 10，t=2 的 cpu = 12 + 3 = 15
        self.assertEqual(monitor.counter_delta(CPU_SECONDS, 0.0, 3.0), 5.0)
        self.assertAlmostEqual(monitor.cpu_utilization(0.0, 3.0), 5.0 / 3.0, places=4)
        self.assertEqual(monitor.gauge_max("echomem_process_resident_memory_bytes", 0.0, 3.0), 130.0)

    def test_histogram_percentiles(self) -> None:
        frame = MetricsFrame(
            ts=1.0,
            samples={
                "m_bucket": [
                    ({"le": "0.1", "status": "ok"}, 10.0),
                    ({"le": "0.5", "status": "ok"}, 30.0),
                    ({"le": "1.0", "status": "ok"}, 40.0),
                    ({"le": "+Inf", "status": "ok"}, 40.0),
                ],
                "m_sum": [({"status": "ok"}, 12.0)],
                "m_count": [({"status": "ok"}, 40.0)],
            },
        )
        monitor = self._monitor_with_frames([frame])
        result = monitor.histogram_percentiles("m", 0.0, 2.0)
        self.assertAlmostEqual(result["p50"], 0.3, places=6)
        self.assertAlmostEqual(result["p95"], 0.9, places=6)
        self.assertAlmostEqual(result["p99"], 0.98, places=6)

    def test_histogram_missing_window(self) -> None:
        monitor = self._monitor_with_frames([])
        result = monitor.histogram_percentiles("m", 0.0, 2.0)
        self.assertEqual(result, {"p50": None, "p95": None, "p99": None})


class PrepareTests(unittest.TestCase):
    def test_build_search_query_pool_profiles(self) -> None:
        recall, ordinary = ["r1", "r2"], ["n1", "n2"]
        queries, kinds = build_search_query_pool(
            recall_queries=recall,
            no_recall_queries=ordinary,
            profile="recall-only",
        )
        self.assertEqual(queries, recall)
        self.assertEqual(kinds, ["recall", "recall"])
        queries, kinds = build_search_query_pool(
            recall_queries=recall,
            no_recall_queries=ordinary,
            profile="no-recall-only",
        )
        self.assertEqual(queries, ordinary)
        self.assertEqual(kinds, ["no_recall", "no_recall"])

    def test_build_search_query_pool_mixes_at_requested_ratio(self) -> None:
        queries, kinds = build_search_query_pool(
            recall_queries=["r1"],
            no_recall_queries=["n1"],
            profile="mixed",
            recall_ratio=0.7,
        )
        self.assertEqual(len(queries), 10)
        self.assertEqual(kinds.count("recall"), 7)
        self.assertEqual(kinds.count("no_recall"), 3)

    def test_query_fragments_split_on_chinese_separator(self) -> None:
        fragments = _query_fragments(
            ["本周项目进展顺利，核心模块完成联调。计划下周发布。"]
        )
        self.assertEqual(fragments, ["本周项目进展顺利，核心模块完成联调"])

    def test_query_fragments_split_on_english_separator(self) -> None:
        fragments = _query_fragments(
            ["First message of conv30. With more detail.", "Any updates?"]
        )
        self.assertEqual(fragments, ["First message of conv30", "Any updates"])

    @staticmethod
    def _write_locomo_json(tmp: Path) -> Path:
        samples = [
            {
                "sample_id": "conv-30",
                "conversation": {
                    "speaker_a": "Gina",
                    "speaker_b": "Jon",
                    "session_1_date_time": "10:00 AM on 1 January, 2023",
                    "session_1": [
                        {"speaker": "Gina", "text": "First message of conv30. With detail."},
                        {"speaker": "Jon", "text": "Second message."},
                    ],
                    "session_2_date_time": "11:00 AM on 2 January, 2023",
                    "session_2": [{"speaker": "Gina", "text": "Third message."}],
                },
                "qa": [],
            },
            {
                "sample_id": "conv-41",
                "conversation": {
                    "speaker_a": "Amy",
                    "speaker_b": "Bob",
                    "session_1_date_time": "9:00 AM on 3 January, 2023",
                    "session_1": [{"speaker": "Amy", "text": "Other sample message."}],
                },
                "qa": [],
            },
        ]
        path = tmp / "locomo.json"
        path.write_text(json.dumps(samples, ensure_ascii=False), encoding="utf-8")
        return path

    def test_locomo_seed_batches_single_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_locomo_json(Path(tmp))
            batches = load_locomo_seed_batches(path, "conv-30")
        self.assertEqual(len(batches), 2)  # conv-30 有两个会话
        self.assertEqual(batches[0][0]["role"], "user")
        self.assertEqual(batches[0][0]["content"], "First message of conv30. With detail.")
        self.assertEqual(batches[0][1]["role"], "assistant")

    def test_locomo_seed_batches_all_and_multi(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_locomo_json(Path(tmp))
            all_batches = load_locomo_seed_batches(path, "all")
            multi_batches = load_locomo_seed_batches(path, "conv-30,conv-41")
        self.assertEqual(len(all_batches), 3)
        self.assertEqual(len(multi_batches), 3)

    def test_locomo_seed_batches_unknown_filter_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_locomo_json(Path(tmp))
            with self.assertRaises(ValueError):
                load_locomo_seed_batches(path, "conv-99")
            with self.assertRaises(ValueError):
                load_locomo_seed_batches(path, "")

    def test_seed_tenant_from_conversations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_locomo_json(Path(tmp))
            batches = load_locomo_seed_batches(path, "conv-30")
        client = FakeMemClient()
        tenant = seed_tenant_from_conversations(client, 0, batches, 30.0)
        self.assertEqual(client.open_calls, 2)
        self.assertEqual(client.add_calls, 3)
        self.assertEqual(tenant.seed_sessions, 2)
        self.assertEqual(tenant.seed_messages, 3)
        self.assertEqual(len(tenant.active_session_ids), 2)
        self.assertTrue(all(tenant.active_session_ids))

    def test_empty_seed_can_create_multiple_active_sessions(self) -> None:
        client = FakeMemClient()
        tenant = seed_tenant(
            client,
            0,
            sessions=0,
            messages_per_session=0,
            commit_poll_timeout_s=30.0,
            active_sessions=8,
        )
        self.assertEqual(8, len(tenant.active_session_ids))
        self.assertEqual(0, tenant.seed_messages)
        self.assertEqual(0, tenant.seed_sessions)

    def test_seed_session_flow_retries_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_locomo_json(Path(tmp))
            batches = load_locomo_seed_batches(path, "conv-30")
        client = FakeMemClient()
        client.poll_failures_left = 1  # 第一个会话第一次 commit 失败
        tenant = seed_tenant_from_conversations(client, 0, batches, 30.0)
        # 首个会话重灌一次：open 2 次；两个会话消息共 3 条灌 2 遍 + 1 遍
        self.assertEqual(client.open_calls, 3)
        self.assertEqual(client.add_calls, 5)
        self.assertEqual(tenant.seed_sessions, 2)
        self.assertEqual(tenant.queries, ["First message of conv30", "Third message"])

    def test_seed_session_flow_fails_after_two_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_locomo_json(Path(tmp))
            batches = load_locomo_seed_batches(path, "conv-41")  # 单会话
        client = FakeMemClient()
        client.poll_failures_left = 2  # 两次都失败 -> 中止
        with self.assertRaises(RuntimeError):
            seed_tenant_from_conversations(client, 0, batches, 30.0)
        self.assertEqual(client.open_calls, 2)

    def test_static_mode_rejects_multi_tenant(self) -> None:
        with self.assertRaises(ValueError):
            TenantPreparer("http://127.0.0.1:8010", auth_mode="static", tenants=2)
        # 单租户被接受
        preparer = TenantPreparer(
            "http://127.0.0.1:8010",
            auth_mode="static",
            auth_key="k",
            tenant_id="t",
            user_id="u",
            tenants=1,
        )
        self.assertEqual(preparer.tenants, 1)

    def test_preparer_cleanup_deletes_provisioned(self) -> None:
        preparer = TenantPreparer("http://127.0.0.1:8010", tenants=8)
        # 直接注入"已 provision"记录：seed 中途失败时入口 finally 也会清理它们
        first = FakeMemClient()
        second = FakeMemClient()
        preparer._provisioned = [(0, first), (1, second)]
        preparer.cleanup()
        self.assertEqual(first.delete_calls, 1)
        self.assertEqual(second.delete_calls, 1)

    def test_preparer_cleanup_tolerates_failures(self) -> None:
        preparer = TenantPreparer("http://127.0.0.1:8010", tenants=8)
        broken = FakeMemClient()
        broken.fail_step = "delete"
        healthy = FakeMemClient()
        preparer._provisioned = [(0, broken), (1, healthy)]
        preparer.cleanup()  # 单租户删除失败不阻断其余租户
        self.assertEqual(healthy.delete_calls, 1)


class RunStressArgsTests(unittest.TestCase):
    """run_stress 参数校验：--cleanup-identities 与 auth-mode 的组合约束。"""

    @staticmethod
    def _args(**overrides) -> argparse.Namespace:
        base = dict(
            scenarios="A,D",
            mix_ratios="8:1",
            concurrency_steps="1,4",
            quick=False,
            duration_s=60.0,
            tenants=1,
            seed_concurrency=4,
            barrier_prepare_concurrency=4,
            burst_commits=8,
            mode="max-throughput",
            rps=0.0,
            auth_mode="provision",
            cleanup_identities=False,
            seed_source="synthetic",
            dataset_path="",
            sample_filter="conv-30",
            tenant_config="",
            commit_rpm=0.0,
            commit_barrier=False,
            commit_barrier_count=128,
            commit_tenant_distribution="uniform",
            commit_zipf_exponent=2.0,
            commit_tenant_counts="",
            commit_barrier_waves=1,
            commit_barrier_cooldown_s=0.0,
            isolation_markers_per_tenant=5,
            client_connection_error_abort_threshold=100,
        )
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_static_mode_rejects_cleanup(self) -> None:
        args = self._args(auth_mode="static", cleanup_identities=True)
        with self.assertRaises(ValueError):
            _resolve_args(args)

    def test_provision_accepts_cleanup(self) -> None:
        args = self._args(auth_mode="provision", cleanup_identities=True)
        resolved = _resolve_args(args)
        self.assertEqual(resolved["scenario_ids"], ["A", "D"])

    def test_scenario_f_accepted(self) -> None:
        args = self._args(scenarios="A,B,C,D,F")
        resolved = _resolve_args(args)
        self.assertIn("F", resolved["scenario_ids"])

    def test_unknown_scenario_rejected(self) -> None:
        args = self._args(scenarios="A,X")
        with self.assertRaises(ValueError):
            _resolve_args(args)

    def test_static_without_cleanup_is_ok(self) -> None:
        args = self._args(auth_mode="static", cleanup_identities=False)
        _resolve_args(args)  # 不抛错即可

    def test_fixed_rps_zero_per_tenant_rate_is_not_passed_as_configured(self) -> None:
        args = self._args(mode="fixed-rps", rps=8.0, per_tenant_rps=0.0)
        resolved = _resolve_args(args)
        self.assertEqual(8.0, resolved["rps"])
        self.assertIsNone(resolved["per_tenant_rps"])

    def test_locomo_seed_missing_dataset_raises(self) -> None:
        args = self._args(seed_source="locomo", dataset_path="no-such-file.json")
        with self.assertRaises(ValueError):
            _resolve_args(args)

    def test_locomo_seed_empty_filter_raises(self) -> None:
        args = self._args(seed_source="locomo", sample_filter="")
        with self.assertRaises(ValueError):
            _resolve_args(args)

    def test_locomo_seed_default_dataset_resolves(self) -> None:
        args = self._args(seed_source="locomo")
        resolved = _resolve_args(args)
        path = Path(resolved["seed_dataset_path"])
        self.assertTrue(path.is_file(), f"默认数据集应存在: {path}")
        self.assertEqual(path.name, "locomo10.json")


class FeatureGuaranteeTests(unittest.TestCase):
    """unit tests for the four EchoMem feature guarantees."""

    @staticmethod
    def _rec(op, session_id, status="ok", error="", stage=1.0, idx=0, scene="B@1"):
        return RequestRecord(
            scene_key=scene,
            step_conc=1,
            tenant_idx=idx,
            op=op,
            stage_ms=stage,
            status=status,
            error_type=error,
            ts_ms=0.0,
            session_id=session_id,
            extra="burst" if scene == "D@1" else "",
        )

    def test_commit_durability_full(self) -> None:
        records = [
            self._rec("commit_submit", "s1"),
            self._rec("commit_done", "s1"),
            self._rec("commit_submit", "s2"),
            self._rec("commit_done", "s2", "error", "commit_failed"),
            self._rec("commit_submit", "s3"),
            self._rec("commit_done", "s3", "error", "commit_timeout"),
            self._rec("commit_submit", "s4", "error", "http_5xx"),
        ]
        result = commit_durability(records)
        self.assertEqual(result["submit_ok_total"], 3)
        self.assertEqual(result["submit_rejected_total"], 1)
        self.assertEqual(result["submit_rejected_breakdown"], {"http_5xx": 1})
        self.assertEqual(result["accepted_done_ok"], 1)
        self.assertEqual(result["accepted_done_failed"], 1)
        self.assertEqual(result["accepted_done_poll_timeout"], 1)
        self.assertEqual(result["guarantee_violations"], 1)
        self.assertAlmostEqual(result["commit_success_rate"], 1 / 3, places=5)

    def test_commit_durability_all_ok(self) -> None:
        records = [
            self._rec("commit_submit", "s1"),
            self._rec("commit_done", "s1"),
            self._rec("commit_submit", "s2"),
            self._rec("commit_done", "s2"),
        ]
        result = commit_durability(records)
        self.assertEqual(result["guarantee_violations"], 0)
        self.assertEqual(result["commit_success_rate"], 1.0)

    def test_tenant_fairness_unbalanced(self) -> None:
        records = [
            self._rec("read", f"r-{i}", stage=10.0, idx=0, scene="A@1")
            for i in range(50)
        ] + [
            self._rec("read", f"r2-{i}", stage=100.0, idx=1, scene="A@1")
            for i in range(50)
        ]
        result = tenant_fairness(records)
        fair = result["A@1"]
        self.assertEqual(fair["p95_max_min_ratio"], 10.0)
        self.assertFalse(fair["balanced"])
        self.assertEqual(len(fair["tenants"]), 2)

    def test_tenant_fairness_balanced(self) -> None:
        records = [
            self._rec("read", f"r-{i}", stage=10.0 + i % 3, idx=0, scene="A@1")
            for i in range(30)
        ] + [
            self._rec("read", f"r2-{i}", stage=11.0 + i % 3, idx=1, scene="A@1")
            for i in range(30)
        ]
        result = tenant_fairness(records)
        fair = result["A@1"]
        self.assertLess(fair["p95_max_min_ratio"], 3.0)
        self.assertTrue(fair["balanced"])

    def test_tenant_fairness_separates_commit_and_search_dimensions(self) -> None:
        records = [
            self._rec("read", f"a-{i}", stage=50.0, idx=0, scene="fair")
            for i in range(10)
        ] + [
            self._rec("read", f"b-{i}", stage=100.0, idx=1, scene="fair")
            for i in range(10)
        ] + [
            self._rec("commit_done", f"ca-{i}", stage=10.0, idx=0, scene="fair")
            for i in range(10)
        ] + [
            self._rec("commit_done", f"cb-{i}", stage=10.0, idx=1, scene="fair")
            for i in range(10)
        ]
        fair = tenant_fairness(records, wall_s=10.0)["fair"]
        self.assertEqual(fair["commit_throughput_per_tenant"], {"0": 1.0, "1": 1.0})
        self.assertEqual(fair["commit_throughput_jain"], 1.0)
        self.assertEqual(fair["search_latency_utility_jain"], 0.9)

    def test_rss_trend_slope(self) -> None:
        # 每秒 +1MB：斜率 = 60 MB/min
        series = [
            (float(i), (100 + i) * 1024 * 1024)
            for i in range(0, 40, 10)  # t=0,10,20,30 -> 100..130MB
        ]
        trend = rss_trend_mb_per_min(series)
        self.assertAlmostEqual(trend["slope_mb_per_min"], 60.0, places=1)
        self.assertGreaterEqual(trend["r2"], 0.99)

    def test_rss_trend_flat(self) -> None:
        series = [(float(i), 100.0 * 1024 * 1024) for i in range(10)]
        trend = rss_trend_mb_per_min(series)
        self.assertAlmostEqual(trend["slope_mb_per_min"], 0.0, places=3)

    def test_rss_trend_undecidable_few_samples(self) -> None:
        series = [(0.0, 100.0 * 1024 * 1024), (1.0, 110.0 * 1024 * 1024)]
        trend = rss_trend_mb_per_min(series)
        self.assertIsNone(trend["slope_mb_per_min"])

    def test_rss_trend_short_window_is_not_verdictable(self) -> None:
        series = [
            (float(i), (100 + i) * 1024 * 1024)
            for i in range(0, 50, 10)
        ]
        trend = rss_trend_mb_per_min(series)
        self.assertEqual(trend["window_s"], 40.0)
        self.assertFalse(trend["verdictable"])

    def test_cpu_utilization_series(self) -> None:
        monitor = MetricsMonitor("http://test")
        monitor.frames = [
            MetricsFrame(ts=0.0, samples={CPU_SECONDS: [({}, 0.0)]}),
            MetricsFrame(ts=1.0, samples={CPU_SECONDS: [({}, 1.0)]}),
            MetricsFrame(ts=2.0, samples={CPU_SECONDS: [({}, 1.0)]}),
        ]
        series = monitor.cpu_utilization_series(0.0, 2.0)
        self.assertEqual(series, [(1.0, 100.0), (2.0, 0.0)])


class FeatureVerdictTests(unittest.TestCase):
    """evaluate_features: PASS / FAIL / INCONCLUSIVE for the four guarantees."""

    @staticmethod
    def _base_summary() -> dict:
        return {
            "config": {"degradation_threshold": 2.0, "no_metrics": False},
            "server": {"metrics_available": True},
            "commit_durability": {
                "submit_ok_total": 10,
                "submit_rejected_total": 0,
                "accepted_done_ok": 10,
                "accepted_done_failed": 0,
                "accepted_done_other": 0,
                "guarantee_violations": 0,
                "commit_success_rate": 1.0,
            },
            "degradation": {"D@4_vs_A@4": {"p50": 1.1, "p95": 1.5, "p99": 1.8}},
            "tenant_fairness": {
                "A@4": {
                    "tenants": [{"tenant_idx": 0, "count": 10, "p95_ms": 20.0}, {"tenant_idx": 1, "count": 10, "p95_ms": 22.0}],
                    "p95_max_min_ratio": 1.1,
                    "balanced": True,
                }
            },
            "resources": {
                "rss_trend": {"slope_mb_per_min": 1.2, "r2": 0.8, "samples": 30},
                "rss_unsettled_mb": 5.0,
            },
            "write_retry": {
                "submit_total": 10,
                "retried_total": 0,
                "first_attempt_ok": 10,
                "first_attempt_rate": 1.0,
                "final_ok": 10,
                "final_success_rate": 1.0,
                "retry_exhausted_failures": 0,
            },
            "reconciliation": {
                "sessions": [
                    {"session_id": "s1", "checks": [{"ok": True}], "verdict": "pass"}
                ],
                "verdict": "PASS",
                "reason": "全部会话对账通过",
            },
            "search_quality": {
                "total": 10,
                "anchor_total": 10,
                "anchor_failures": 0,
                "quality_failures": 0,
            },
            "isolation": {"D@4": {"verdict": "PASS", "reason": "同/跨租户劣化均 < 阈值"}},
            "error_type_validation": {"verdict": "PASS", "observed_breakdown": {"http_4xx": 1}},
            "fault_injection": {"verdict": "PASS", "reason": "全部阶段一致", "stages": []},
            "preflight": {"ok": True, "engines_checked": 1},
        }

    def test_all_pass(self) -> None:
        result = evaluate_features(self._base_summary())
        self.assertEqual(result["overall"], "PASS")
        for key, entry in result["features"].items():
            if key in ("tenant_isolation", "saturation_contract", "hot_tenant_fairness"):
                # 新增特性未运行对应场景（S/H/I）时按 not_run 处理，不参与总体 PASS
                self.assertEqual(entry["verdict"], "not_run", key)
                continue
            self.assertEqual(entry["verdict"], "PASS", key)
        # 每个特性都带量化 measurements
        commit_meas = result["features"]["commit_guarantee"]["measurements"]
        self.assertEqual(commit_meas["durability"]["commit_success_rate"], 1.0)
        self.assertIn("cases", commit_meas["retrieval_precedence"])
        fair_meas = result["features"]["tenant_fairness"]["measurements"]
        self.assertEqual(fair_meas["slowest_tenant_p95_ms"], 22.0)
        self.assertEqual(fair_meas["slowest_waits_extra_ms"], 2.0)
        leak_meas = result["features"]["memory_leak"]["measurements"]
        self.assertEqual(leak_meas["slope_mb_per_min"], 1.2)
        self.assertEqual(leak_meas["projected_growth_mb_per_hour"], 72.0)
        timeline_meas = result["features"]["resource_timeline"]["measurements"]
        self.assertIs(timeline_meas["metrics_available"], True)

    def test_durability_violation_fails(self) -> None:
        summary = self._base_summary()
        summary["commit_durability"]["guarantee_violations"] = 2
        summary["commit_durability"]["accepted_done_failed"] = 2
        summary["commit_durability"]["commit_success_rate"] = 0.8
        result = evaluate_features(summary)
        feature = result["features"]["commit_guarantee"]
        self.assertEqual(feature["verdict"], "FAIL")
        self.assertEqual(feature["sub"]["durability"]["verdict"], "FAIL")
        self.assertEqual(result["overall"], "FAIL")

    def test_retrieval_precedence_fails_on_high_degradation(self) -> None:
        summary = self._base_summary()
        summary["degradation"] = {"D@4_vs_A@4": {"p50": 1.0, "p95": 3.5, "p99": 5.0}}
        result = evaluate_features(summary)
        feature = result["features"]["commit_guarantee"]
        self.assertEqual(feature["sub"]["retrieval_precedence"]["verdict"], "FAIL")
        self.assertEqual(feature["verdict"], "FAIL")

    def test_fairness_fails(self) -> None:
        summary = self._base_summary()
        summary["tenant_fairness"]["A@4"].update({"p95_max_min_ratio": 10.0, "balanced": False})
        result = evaluate_features(summary)
        feature = result["features"]["tenant_fairness"]
        self.assertEqual(feature["verdict"], "FAIL")
        self.assertEqual(result["overall"], "FAIL")
        # 量化：最慢租户 P95=22ms，比最快(20ms)多等 2ms
        meas = feature["measurements"]
        self.assertEqual(meas["slowest_tenant_p95_ms"], 22.0)
        self.assertEqual(meas["slowest_waits_extra_ms"], 2.0)
        self.assertIn("多等", feature["reason"])

    def test_memory_leak_fails(self) -> None:
        summary = self._base_summary()
        summary["resources"]["rss_trend"]["slope_mb_per_min"] = 12.0
        result = evaluate_features(summary)
        feature = result["features"]["memory_leak"]
        self.assertEqual(feature["verdict"], "FAIL")
        # 量化：12 MB/min → 预计每小时 720 MB
        meas = feature["measurements"]
        self.assertEqual(meas["projected_growth_mb_per_hour"], 720.0)
        self.assertIn("720.0", feature["reason"])

    def test_memory_leak_short_window_is_inconclusive(self) -> None:
        summary = self._base_summary()
        summary["resources"]["rss_trend"] = {
            "slope_mb_per_min": 12.0,
            "r2": 0.99,
            "samples": 5,
            "window_s": 5.0,
        }
        result = evaluate_features(summary)
        feature = result["features"]["memory_leak"]
        self.assertEqual(feature["verdict"], "INCONCLUSIVE")
        self.assertIn("最小判定窗口", feature["reason"])

    def test_tenant_isolation_passes_for_independent_provisioned_tenants(self) -> None:
        summary = self._base_summary()
        summary["config"].update({"auth_mode": "provision", "tenants": 4})
        summary["data_scale"] = {"tenants": 4}
        summary["isolation_probe"] = {
            "verdict": "PASS",
            "probe_count": 16,
            "same_tenant_hit_rate": 1.0,
            "cross_tenant_false_positive_rate": 0.0,
        }
        result = evaluate_features(summary)
        self.assertEqual(result["features"]["tenant_isolation"]["verdict"], "PASS")

    def test_tenant_isolation_static_identity_stays_inconclusive(self) -> None:
        summary = self._base_summary()
        summary["config"].update({"auth_mode": "static", "tenants": 1})
        summary["isolation_probe"] = {
            "verdict": "PASS",
            "probe_count": 16,
            "same_tenant_hit_rate": 1.0,
            "cross_tenant_false_positive_rate": 0.0,
        }
        result = evaluate_features(summary)
        self.assertEqual(
            result["features"]["tenant_isolation"]["verdict"], "INCONCLUSIVE"
        )

    def test_inconclusive_cases(self) -> None:
        # 未跑写场景
        summary = self._base_summary()
        summary["commit_durability"] = {"submit_ok_total": 0, "submit_rejected_total": 0}
        result = evaluate_features(summary)
        self.assertEqual(result["features"]["commit_guarantee"]["sub"]["durability"]["verdict"], "INCONCLUSIVE")
        # 无 D 场景
        summary["degradation"] = {"C:8:1@4_vs_A@4": {"p95": 1.2}}
        result = evaluate_features(summary)
        self.assertEqual(result["features"]["commit_guarantee"]["sub"]["retrieval_precedence"]["verdict"], "INCONCLUSIVE")
        # 单租户公平性不可判
        summary["tenant_fairness"] = {
            "A@4": {"tenants": [{"tenant_idx": 0, "count": 10, "p95_ms": 20.0}], "p95_max_min_ratio": None, "balanced": True}
        }
        result = evaluate_features(summary)
        self.assertEqual(result["features"]["tenant_fairness"]["verdict"], "INCONCLUSIVE")
        # RSS 采样不足
        summary["resources"]["rss_trend"] = {"slope_mb_per_min": None, "r2": None, "samples": 2}
        result = evaluate_features(summary)
        self.assertEqual(result["features"]["memory_leak"]["verdict"], "INCONCLUSIVE")
        self.assertEqual(result["overall"], "INCONCLUSIVE")

    def test_no_metrics_timeline_inconclusive(self) -> None:
        summary = self._base_summary()
        summary["config"]["no_metrics"] = True
        result = evaluate_features(summary)
        self.assertEqual(result["features"]["resource_timeline"]["verdict"], "INCONCLUSIVE")


class SerializationTests(unittest.TestCase):
    """Record serialization used by requests.csv."""

    def test_loadgen_records_serialize(self) -> None:
        record = RequestRecord(
            scene_key="D@4",
            step_conc=4,
            tenant_idx=1,
            op="read",
            stage_ms=12.5,
            status="ok",
            error_type="",
            ts_ms=123.0,
            session_id="s",
        )
        row = record.to_csv_row()
        self.assertEqual(row["op"], "read")
        self.assertEqual(row["scene"], "D@4")
        self.assertIn("http_status", row)


class ReportTests(unittest.TestCase):
    """report.py: 报告区块完整性 + 从制品再生成时间线 / 报告。"""

    @staticmethod
    def _summary() -> dict:
        return {
            "generator": "test",
            "status": "completed",
            "started_at": "2026-01-01T00:00:00+0800",
            "finished_at": "2026-01-01T00:01:00+0800",
            "config": {
                "echomem_url": "http://127.0.0.1:8010",
                "timeout_s": 10,
                "top_k": 5,
                "tenants": 2,
                "concurrency_steps": [1, 4],
                "scenario_ids": ["A", "B"],
                "mix_ratios": ["8:1"],
                "duration_s": 60,
                "burst_commits": 32,
                "burst_window_s": 10,
                "seed_source": "synthetic",
                "seed_sessions_per_tenant": 5,
                "messages_per_session": 10,
                "metrics_interval_s": 2,
                "degradation_threshold": 2.0,
                "commit_poll_timeout_s": 120,
                "auth_mode": "provision",
                "cleanup_identities": True,
                "mode": "max-throughput",
                "no_metrics": False,
            },
            "data_scale": {"tenants": 2, "sessions_per_tenant": 5, "messages_per_session": 10},
            "server": {"base_url": "http://127.0.0.1:8010", "metrics_available": True},
            "scenes": {
                "A@1": {
                    "scene_id": "A",
                    "per_tenant_conc": 1,
                    "duration_s": 60.0,
                    "mix": None,
                    "burst_commits": 0,
                    "burst_window_s": 0.0,
                    "resource": {
                        "threads_max": 33,
                        "python_threads_max": 24,
                        "http_inflight_max": 3,
                        "commit_queue_depth_max": 0,
                        "recall_duration": {"p50": 1.8, "p95": 3.2},
                        "http_duration": {"p50": 0.007},
                        "commit_duration": {"p50": 46.6},
                    },
                    "ops": {
                        "A@1": {
                            "read": {
                                "count": 57,
                                "avg_ms": 2140.9,
                                "p50_ms": 2084.0,
                                "p95_ms": 2653.2,
                                "p99_ms": 3063.1,
                                "max_ms": 3113.3,
                                "min_ms": 1834.0,
                                "qps": 0.922,
                                "errors_total": 0,
                                "error_rate": 0.0,
                                "error_breakdown": {},
                            }
                        }
                    },
                },
                "B@1": {
                    "scene_id": "B",
                    "per_tenant_conc": 1,
                    "duration_s": 60.0,
                    "mix": None,
                    "burst_commits": 0,
                    "burst_window_s": 0.0,
                    "resource": {
                        "threads_max": 40,
                        "python_threads_max": 30,
                        "http_inflight_max": 5,
                        "commit_queue_depth_max": 2,
                        "recall_duration": {"p50": 2.0, "p95": 3.0},
                        "http_duration": {"p50": 0.01},
                        "commit_duration": {"p50": 50.0},
                    },
                    "ops": {
                        "B@1": {
                            "open": {"count": 3, "p50_ms": 200.0, "p95_ms": 250.0, "p99_ms": 260.0},
                            "add": {"count": 30, "p50_ms": 210.0, "p95_ms": 300.0, "p99_ms": 320.0},
                            "commit_submit": {"count": 3, "p50_ms": 100.0, "p95_ms": 120.0, "p99_ms": 130.0},
                            "commit_done": {"count": 3, "p50_ms": 40000.0, "p95_ms": 45000.0, "p99_ms": 46000.0},
                            "read": {"count": 1, "p50_ms": 5.0, "p95_ms": 6.0, "p99_ms": 7.0, "qps": 0.02, "error_rate": 0.0},
                        }
                    },
                },
            },
            "degradation": {"B@1_vs_A@1": {"p50": 1.1, "p95": 1.2, "p99": 1.3}},
            "signals": {"signals_found": ["B@1: 测试信号"]},
            "consistency": {"count": 3, "p50_ms": 2175.5, "p95_ms": 29996.5, "timeouts": 1},
            "resources": {
                "cpu_util_mean_percent": 12.3,
                "cpu_util_max_percent": 40.0,
                "rss_baseline_mb": 745.17,
                "rss_peak_mb": 993.96,
                "rss_settled_mb": 518.04,
                "rss_unsettled_mb": -227.13,
                "threads_max": 331,
                "commit_queue_max": 32,
                "http_inflight_max": 235,
                "rss_trend": {"slope_mb_per_min": -13.9, "r2": 0.72, "samples": 100},
            },
            "commit_durability": {
                "submit_ok_total": 211,
                "submit_rejected_total": 0,
                "submit_rejected_breakdown": {},
                "accepted_done_ok": 181,
                "accepted_done_failed": 0,
                "accepted_done_poll_timeout": 30,
                "accepted_done_other": 0,
                "commit_success_rate": 0.85782,
                "guarantee_violations": 0,
            },
            "tenant_fairness": {
                "A@1": {
                    "tenants": [
                        {"tenant_idx": 0, "count": 10, "p50_ms": 2000.0, "p95_ms": 2100.0, "p99_ms": 2300.0},
                        {"tenant_idx": 1, "count": 10, "p50_ms": 2200.0, "p95_ms": 2400.0, "p99_ms": 2600.0},
                    ],
                    "p95_max_min_ratio": 1.14,
                    "p95_cv": 0.07,
                    "balanced": True,
                }
            },
            "commit_latency": {"count": 181, "p50_ms": 47391.6, "p95_ms": 116351.6, "p99_ms": 118316.1},
            "feature_verdicts": {},
        }

    def test_build_html_contains_sections(self) -> None:
        html_text = build_html(self._summary(), {})
        for section in ("测试方法", "测试场景", "指标字典", "压测结果", "写事务四段延迟", "支撑事实"):
            self.assertIn(section, html_text, section)
        # 支撑事实可见：实测值被渲染进报告（A@1 读 QPS=0.922）
        self.assertIn("0.922", html_text)
        # 每场景服务端资源快照表头与矩阵顺序
        self.assertIn("每场景服务端资源快照", html_text)
        self.assertIn("A@1", html_text)
        self.assertIn("B@1", html_text)

    def test_chart_series_from_metrics_csv(self) -> None:
        rows = [
            ["ts", "metric", "labels", "value"],
            ["100.0", "echomem_process_resident_memory_bytes", "{}", "104857600.0"],
            ["101.0", "echomem_process_resident_memory_bytes", "{}", "209715200.0"],
            ["100.0", "echomem_process_threads", "{}", "33.0"],
            ["101.0", "echomem_process_threads", "{}", "34.0"],
            ["100.0", "echomem_session_commit_queue_depth", "{}", "0.0"],
            ["100.0", "echomem_http_requests_inflight", "{}", "3.0"],
            ["100.0", "echomem_http_requests_inflight", "{route=\"/x\"}", "2.0"],
            ["100.0", "echomem_process_cpu_seconds_total", "{}", "10.0"],
            ["101.0", "echomem_process_cpu_seconds_total", "{}", "12.0"],
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics_samples.csv"
            path.write_text("\n".join(",".join(r) for r in rows), encoding="utf-8")
            series = chart_series_from_metrics_csv(path)
        self.assertEqual(series["rss_mb"], [(100.0, 100.0), (101.0, 200.0)])
        self.assertEqual(series["threads"], [(100.0, 33.0), (101.0, 34.0)])
        self.assertEqual(series["commit_queue"], [(100.0, 0.0)])
        # 同一 ts 跨标签集求和（对齐 monitor._value）：3 + 2 = 5
        self.assertEqual(series["inflight"], [(100.0, 5.0)])
        # CPU 帧差：12 - 10 = 2 秒计数 / 1s 墙钟 = 200%
        self.assertEqual(series["cpu_percent"], [(101.0, 200.0)])

    def test_chart_series_from_metrics_csv_missing_file(self) -> None:
        self.assertEqual(chart_series_from_metrics_csv(Path("no_such_file.csv")), {})

    def test_regenerate_report(self) -> None:
        summary = self._summary()
        summary["scenes"]["A@1"]["window_s"] = [100.0, 102.0]
        summary["scenes"]["B@1"]["window_s"] = [103.0, 105.0]
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            (out_dir / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False), encoding="utf-8"
            )
            (out_dir / "metrics_samples.csv").write_text(
                "ts,metric,labels,value\n"
                "100.0,echomem_process_resident_memory_bytes,{},104857600.0\n"
                "101.0,echomem_process_resident_memory_bytes,{},209715200.0\n"
                "100.0,echomem_process_threads,{},33.0\n"
                "100.0,echomem_process_cpu_seconds_total,{\"mode\": \"user\"},8.0\n"
                "100.0,echomem_process_cpu_seconds_total,{\"mode\": \"system\"},2.0\n"
                "101.0,echomem_process_cpu_seconds_total,{\"mode\": \"user\"},12.0\n"
                "101.0,echomem_process_cpu_seconds_total,{\"mode\": \"system\"},3.0\n",
                encoding="utf-8",
            )
            path = regenerate_report(out_dir)
            self.assertTrue(path.name == "report.html")
            html_text = path.read_text(encoding="utf-8")
            self.assertIn("测试方法", html_text)
            self.assertIn("指标字典", html_text)
            # CPU 统计从 CSV 回填：窗口 [100,105] 内 user+system 10->15，均值 100.0%
            self.assertIn("CPU 均值 (%)</td><td>100.0", html_text)

    def test_cpu_stats_from_csv(self) -> None:
        rows = [
            ["ts", "metric", "labels", "value"],
            ["100.0", "echomem_process_cpu_seconds_total", '{"mode": "user"}', "8.0"],
            ["100.0", "echomem_process_cpu_seconds_total", '{"mode": "system"}', "2.0"],
            ["102.0", "echomem_process_cpu_seconds_total", '{"mode": "user"}', "12.0"],
            ["102.0", "echomem_process_cpu_seconds_total", '{"mode": "system"}', "3.0"],
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.csv"
            path.write_text("\n".join(",".join(r) for r in rows), encoding="utf-8")
            mean, maxp = _cpu_stats_from_csv(path, 100.0, 102.0)
        # 窗口内：user+system = 10 -> 15，delta=5s，wall=2s -> 均值 250%，峰值 250%
        self.assertEqual(mean, 250.0)
        self.assertEqual(maxp, 250.0)

    def test_cpu_stats_from_csv_missing(self) -> None:
        self.assertEqual(_cpu_stats_from_csv(Path("no_such_file.csv"), 0.0, 1.0), (None, None))

    def test_legend_no_overlap(self) -> None:
        """图例自适应排布：长标签不顶到下一项色块，且不与右侧单位重叠。"""
        labels = ["open", "add", "commit_submit", "commit_done"]
        legend = _legend_html([(label, "#000") for label in labels], unit="ms")
        rects = re.findall(r'<rect x="([\d.]+)" y="2"', legend)
        texts = re.findall(r'<text x="([\d.]+)" y="11"[^>]*>([^<]+)</text>', legend)
        self.assertEqual(len(rects), len(labels))
        self.assertEqual(len(texts), len(labels))
        for i in range(len(labels) - 1):
            text_end = float(texts[i][0]) + _estimate_text_width(texts[i][1])
            self.assertLessEqual(text_end, float(rects[i + 1]), labels[i])
        # 单位在最右上角，位于图例整体右侧
        self.assertIn('<text x="632" y="12"', legend)


if __name__ == "__main__":
    unittest.main()

class _FakeTenant:
    """Minimal tenant stub for reconciliation tests (idx + client + queries)."""

    def __init__(self, idx: int, client: FakeMemClient) -> None:
        self.idx = idx
        self.client = client
        self.queries: list[str] = []


class WriteRetryTests(unittest.TestCase):
    """写事务重试：429+Retry-After / 5xx 退避重试、不可重试 4xx、上限、原始/重试后值。"""

    def _run(self, client: FakeMemClient, retry_max: int = 3, messages: int = 3):
        return run_write_transaction(
            client,
            scene_key="B@1",
            step_conc=1,
            tenant_idx=0,
            seq=7,
            messages_per_session=messages,
            commit_poll_timeout_s=30.0,
            commit_retry_max=retry_max,
            commit_retry_backoff_s=0.01,
        )

    def _submit(self, result):
        return [rec for rec in result.records if rec.op == "commit_submit"][0]

    def test_429_retry_after_backs_off_and_succeeds(self) -> None:
        client = FakeMemClient()
        client.commit_429_left = 2
        client.retry_after_s = "0"
        result = self._run(client)
        self.assertTrue(result.ok)
        self.assertEqual(client.commit_attempts, 3)  # 首次 + 2 次重试
        submit = self._submit(result)
        self.assertTrue(submit.retried)
        self.assertEqual(submit.retry_count, 2)
        self.assertTrue(submit.final_success)

    def test_5xx_backs_off_and_succeeds(self) -> None:
        client = FakeMemClient()
        client.commit_failures_left = 2
        result = self._run(client)
        self.assertTrue(result.ok)
        self.assertEqual(client.commit_attempts, 3)
        submit = self._submit(result)
        self.assertTrue(submit.retried)
        self.assertEqual(submit.retry_count, 2)
        self.assertTrue(submit.final_success)

    def test_non_retryable_4xx_fails_immediately(self) -> None:
        client = FakeMemClient()
        client.commit_400 = True
        result = self._run(client, retry_max=3)
        self.assertFalse(result.ok)
        self.assertEqual(client.commit_attempts, 1)  # 业务 4xx 不重试
        submit = self._submit(result)
        self.assertEqual(submit.error_type, "http_4xx")
        self.assertFalse(submit.retried)

    def test_retry_exhausted_fails(self) -> None:
        client = FakeMemClient()
        client.commit_failures_left = 10
        result = self._run(client, retry_max=2)
        self.assertFalse(result.ok)
        self.assertEqual(client.commit_attempts, 3)  # 首次 + 2 次重试后耗尽
        submit = self._submit(result)
        self.assertTrue(submit.retried)
        self.assertEqual(submit.retry_count, 2)
        self.assertFalse(submit.final_success)

    def test_no_retry_when_max_zero(self) -> None:
        client = FakeMemClient()
        client.commit_failures_left = 2
        result = self._run(client, retry_max=0)
        self.assertFalse(result.ok)
        self.assertEqual(client.commit_attempts, 1)
        submit = self._submit(result)
        self.assertEqual(submit.error_type, "http_5xx")
        self.assertFalse(submit.retried)

    def test_retry_decision_classification(self) -> None:
        e429 = urllib.error.HTTPError("http://x", 429, "x", None, None)
        e429.headers = {"Retry-After": "2"}
        retryable, wait = retry_decision(e429, max_retries=3, attempt=1, backoff_s=0.5)
        self.assertTrue(retryable)
        self.assertEqual(wait, 2.0)  # 429 优先用 Retry-After
        e500 = urllib.error.HTTPError("http://x", 500, "x", None, None)
        retryable, wait = retry_decision(e500, max_retries=3, attempt=2, backoff_s=0.5)
        self.assertTrue(retryable)
        self.assertEqual(wait, 1.0)  # 退避 = backoff * attempt
        e400 = urllib.error.HTTPError("http://x", 400, "x", None, None)
        self.assertFalse(retry_decision(e400, max_retries=3, attempt=1, backoff_s=0.5)[0])
        self.assertTrue(retry_decision(TimeoutError("slow"), max_retries=3, attempt=1, backoff_s=0.5)[0])

    def test_retry_summary_raw_vs_retried(self) -> None:
        client_a = FakeMemClient()
        client_a.commit_429_left = 1
        client_a.retry_after_s = "0"
        client_b = FakeMemClient()
        result_a = self._run(client_a)
        result_b = self._run(client_b)
        summary = retry_summary(result_a.records + result_b.records)
        self.assertEqual(summary["submit_total"], 2)
        self.assertEqual(summary["retried_total"], 1)
        self.assertEqual(summary["first_attempt_ok"], 1)  # 原始值
        self.assertEqual(summary["final_ok"], 2)  # 重试后值
        self.assertEqual(summary["retry_exhausted_failures"], 0)
        self.assertEqual(summary["first_attempt_rate"], 0.5)
        self.assertEqual(summary["final_success_rate"], 1.0)


class MessageReconciliationTests(unittest.TestCase):
    """消息级对账去重：全集⊆cursor/atom、无重复、archive 终态。"""

    def _entry(self, **overrides) -> dict:
        base = {
            "tenant_idx": 0,
            "session_id": "s1",
            "client_ids": ["m1", "m2"],
            "client_hashes": ["h1", "h2"],
            "archive_id": "a1",
            "server_ids": ["m1", "m2"],
            "server_hashes": ["h1", "h2"],
            "archive_status": "completed",
            "atom_source_turn_ids": ["m1", "m2"],
            "history_available": True,
            "archive_available": True,
            "atoms_available": True,
        }
        base.update(overrides)
        return base

    def test_reconcile_all_pass(self) -> None:
        result = reconcile_messages([self._entry()])
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["sessions"][0]["verdict"], "pass")

    def test_reconcile_missing_message_fails(self) -> None:
        result = reconcile_messages([self._entry(server_hashes=["h1"])])
        self.assertEqual(result["verdict"], "FAIL")

    def test_reconcile_server_duplicate_fails(self) -> None:
        result = reconcile_messages([self._entry(server_hashes=["h1", "h1", "h2"])])
        self.assertEqual(result["verdict"], "FAIL")

    def test_reconcile_archive_not_completed_fails(self) -> None:
        result = reconcile_messages([self._entry(archive_status="failed")])
        self.assertEqual(result["verdict"], "FAIL")

    def test_reconcile_atom_duplicate_fails(self) -> None:
        result = reconcile_messages([self._entry(atom_source_turn_ids=["m1", "m1"])])
        self.assertEqual(result["verdict"], "FAIL")

    def test_reconcile_sources_unavailable_inconclusive(self) -> None:
        result = reconcile_messages(
            [self._entry(history_available=False, archive_available=False, atoms_available=False)]
        )
        self.assertEqual(result["verdict"], "INCONCLUSIVE")
        self.assertEqual(result["sessions"][0]["verdict"], "not_available")

    def test_run_reconciliation_collects_data(self) -> None:
        client = FakeMemClient()
        result = run_write_transaction(
            client,
            scene_key="B@1", step_conc=1, tenant_idx=0, seq=1,
            messages_per_session=3, commit_poll_timeout_s=30.0,
        )
        gen = LoadGenerator()
        gen._reconciliation_candidates.append(
            (0, result.session_id, result.message_ids, result.content_hashes, result.archive_id)
        )
        data = gen.run_reconciliation([_FakeTenant(0, client)])
        self.assertEqual(len(data), 1)
        self.assertTrue(data[0]["history_available"])
        self.assertTrue(data[0]["archive_available"])
        self.assertTrue(data[0]["atoms_available"])
        self.assertEqual(len(data[0]["server_ids"]), 3)

    def test_run_reconciliation_tolerates_missing_endpoints(self) -> None:
        client = FakeMemClient()
        client.fail_step = "history"
        result = run_write_transaction(
            client,
            scene_key="B@1", step_conc=1, tenant_idx=0, seq=1,
            messages_per_session=2, commit_poll_timeout_s=30.0,
        )
        gen = LoadGenerator()
        gen._reconciliation_candidates.append(
            (0, result.session_id, result.message_ids, result.content_hashes, result.archive_id)
        )
        data = gen.run_reconciliation([_FakeTenant(0, client)])
        self.assertFalse(data[0]["history_available"])


class SearchQualityAssertionTests(unittest.TestCase):
    """search 质量断言：锚词可召回、普通查询真实召回证据、假通过识别。"""

    def test_is_anchor_query(self) -> None:
        self.assertTrue(is_anchor_query("PERFANCHOR-0-1-2"))
        self.assertTrue(is_anchor_query("压测写入会话消息 PERFTAIL-0-1"))
        self.assertFalse(is_anchor_query("本周项目进展顺利"))

    def test_query_kind_is_recorded_on_search(self) -> None:
        client = FakeMemClient()
        gen = LoadGenerator(timeout_s=2.0)
        record = gen._read_once(
            client,
            "PERFANCHOR-0-1-2",
            scene_key="A@1",
            step_conc=1,
            tenant_idx=0,
            query_kind="recall",
        )
        self.assertEqual("recall", record.query_kind)

    def test_search_quality_summary_groups_query_kinds(self) -> None:
        records = [
            self._mk_read("PERFANCHOR-0", 1, True, True),
            self._mk_read("今天下午帮我整理一下会议纪要", 0, True, query_kind="no_recall"),
        ]
        records[0].query_kind = "recall"
        summary = search_quality_summary(records)
        self.assertEqual(summary["query_kind_counts"], {"no_recall": 1, "recall": 1})
        self.assertEqual(summary["query_kind_stats"]["recall"]["count"], 1)
        self.assertEqual(summary["query_kind_stats"]["no_recall"]["p95_ms"], 5.0)

    def test_read_quality_anchor_failure(self) -> None:
        client = FakeMemClient()
        gen = LoadGenerator(timeout_s=2.0)
        rec = gen._read_once(client, "PERFANCHOR-0-1-2", scene_key="A@1", step_conc=1, tenant_idx=0)
        self.assertTrue(rec.quality_ok)
        self.assertEqual(rec.hit_count, 1)
        client.anchor_short_circuit = True
        rec2 = gen._read_once(client, "PERFANCHOR-0-1-2", scene_key="A@1", step_conc=1, tenant_idx=0)
        self.assertFalse(rec2.quality_ok)
        self.assertEqual(rec2.hit_count, 0)
        self.assertFalse(rec2.degraded)

    def test_read_quality_semantic_recall_failure(self) -> None:
        """LoCoMo 语义 query 不是锚词，也必须按 recall 口径判定。"""
        client = FakeMemClient()
        client.search_short_circuit = True
        gen = LoadGenerator(timeout_s=2.0)
        rec = gen._read_once(
            client,
            "上周评审的新接口设计有什么结论",
            scene_key="A@1",
            step_conc=1,
            tenant_idx=0,
            query_kind="recall",
        )
        self.assertFalse(rec.quality_ok)
        self.assertEqual(rec.hit_count, 0)

    def test_read_quality_anchor_degraded_empty_is_not_failure(self) -> None:
        # 核心标记 degraded（引擎跳过/饱和）时空结果不是召回缺陷：不计质量失败。
        client = FakeMemClient()
        client.anchor_short_circuit = True
        client.search_degraded = True
        gen = LoadGenerator(timeout_s=2.0)
        rec = gen._read_once(client, "PERFANCHOR-0-1-2", scene_key="A@1", step_conc=1, tenant_idx=0)
        self.assertEqual(rec.hit_count, 0)
        self.assertTrue(rec.degraded)
        self.assertTrue(rec.quality_ok)

    def test_quality_summary_degraded_breakdown(self) -> None:
        records = [
            self._mk_read("PERFANCHOR-0", 0, True, degraded=True),  # 降级空结果
            self._mk_read("PERFANCHOR-1", 0, False),  # 干净空结果 = 失败
            self._mk_read("PERFANCHOR-2", 2, True, True, degraded=True),  # 降级但有命中
            self._mk_read("普通查询", 1, True, True),
        ]
        summary = search_quality_summary(records)
        self.assertEqual(summary["anchor_failures"], 1)  # 仅干净空结果
        self.assertEqual(summary["degraded_total"], 2)
        self.assertEqual(summary["anchor_degraded"], 2)
        # gated 延迟只覆盖实际召回（hit_count≥1）的 read：降级空结果不入列
        self.assertEqual(summary["gated_read_stats"]["count"], 2)

    def _mk_read(
        self,
        query: str,
        hit: int,
        quality_ok: bool,
        real: bool = False,
        degraded: bool = False,
        query_kind: str = "",
    ) -> RequestRecord:
        return RequestRecord(
            scene_key="A@1", step_conc=1, tenant_idx=0, op="read",
            stage_ms=5.0, status="ok", error_type="", ts_ms=0.0,
            query=query, hit_count=hit, real_recall=real, quality_ok=quality_ok,
            degraded=degraded,
            query_kind=query_kind,
        )

    def test_quality_summary_counts(self) -> None:
        records = [
            self._mk_read("PERFANCHOR-0", 1, True, True),
            self._mk_read("PERFANCHOR-0", 0, False),  # 锚词未召回 = 失败
            self._mk_read("普通查询", 0, True),  # 无证据 → undetermined
            self._mk_read("普通查询", 2, True, True),
        ]
        summary = search_quality_summary(records)
        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["anchor_failures"], 1)
        self.assertEqual(summary["undetermined_real_recall"], 1)
        self.assertEqual(summary["quality_failures"], 1)
        self.assertEqual(summary["degraded_total"], 0)
        self.assertEqual(summary["anchor_degraded"], 0)
        # hit 分布只覆盖实际召回（hit_count≥1）的 read：无证据的 0 命中不再计入
        self.assertEqual(summary["hit_count_p95"], 1.95)

    def test_quality_summary_uses_query_kind_for_semantic_recall(self) -> None:
        records = [
            self._mk_read(
                "上周评审的新接口设计有什么结论",
                0,
                False,
                query_kind="recall",
            ),
            self._mk_read(
                "查今天工单",
                0,
                True,
                query_kind="no_recall",
            ),
        ]
        summary = search_quality_summary(records)
        self.assertEqual(summary["recall_total"], 1)
        self.assertEqual(summary["recall_failures"], 1)
        self.assertEqual(summary["no_recall_total"], 1)
        self.assertEqual(summary["anchor_total"], 0)

    def test_quality_burst_window_excluded(self) -> None:
        # D 场景洪峰窗口（burst）内是刻意过载场景，读降级不计入质量失败；
        # 窗口外的锚词查询仍严格判定。
        def rec(ts: float, query: str, hit: int) -> RequestRecord:
            return RequestRecord(
                scene_key="D@4", step_conc=4, tenant_idx=1, op="read",
                stage_ms=5.0, status="ok", error_type="", ts_ms=ts,
                query=query, hit_count=hit,
                real_recall=hit > 0, quality_ok=hit >= 1,
            )

        records = [
            rec(4500.0, "PERFANCHOR-0", 0),  # burst 窗口 [4000,6000] 内：降级，不计失败
            rec(5000.0, "PERFANCHOR-1", 1),  # burst 窗口内：正常命中，也不计入
            rec(7000.0, "PERFANCHOR-2", 0),  # 窗口外：锚词未召回 = 失败
            rec(8000.0, "PERFANCHOR-3", 2),  # 窗口外：命中
        ]
        summary = search_quality_summary(records, burst_windows=[(4000.0, 6000.0)])
        self.assertEqual(summary["total"], 2)  # 窗口内两条被排除
        self.assertEqual(summary["anchor_total"], 2)
        self.assertEqual(summary["anchor_failures"], 1)  # 仅窗口外未召回计失败

    def test_quality_no_burst_window_keeps_all(self) -> None:
        records = [
            self._mk_read("PERFANCHOR-0", 0, False),
            self._mk_read("PERFANCHOR-1", 1, True, True),
        ]
        summary = search_quality_summary(records)
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["anchor_failures"], 1)


class IsolationGranularityTests(unittest.TestCase):
    """读写隔离细粒度：同/跨租户分组与劣化判定。"""

    def _mk_read(self, tenant: int, ts: float, ms: float) -> RequestRecord:
        return RequestRecord(
            scene_key="D@4", step_conc=4, tenant_idx=tenant, op="read",
            stage_ms=ms, status="ok", error_type="", ts_ms=ts,
        )

    def test_isolation_pass(self) -> None:
        records = [
            self._mk_read(0, 1000, 110),
            self._mk_read(0, 1000, 115),
            self._mk_read(1, 1000, 105),
            self._mk_read(1, 1000, 108),
        ]
        result = isolation_summary(
            records, t0_ms=0, t1_ms=2000, burst_tenant_idx=0,
            baseline_p95=100.0, degradation_threshold=2.0,
        )
        self.assertEqual(result["verdict"], "PASS")
        self.assertLessEqual(result["cross_tenant_degradation"], result["same_tenant_degradation"])

    def test_isolation_cross_worse_fails(self) -> None:
        records = [
            self._mk_read(0, 1000, 110),
            self._mk_read(0, 1000, 115),
            self._mk_read(1, 1000, 200),
            self._mk_read(1, 1000, 210),
        ]
        result = isolation_summary(
            records, t0_ms=0, t1_ms=2000, burst_tenant_idx=0,
            baseline_p95=100.0, degradation_threshold=2.0,
        )
        self.assertEqual(result["verdict"], "FAIL")
        self.assertGreater(result["cross_tenant_degradation"], result["same_tenant_degradation"])

    def test_isolation_inconclusive(self) -> None:
        result = isolation_summary(
            [], t0_ms=0, t1_ms=2000, burst_tenant_idx=0,
            baseline_p95=100.0, degradation_threshold=2.0,
        )
        self.assertEqual(result["verdict"], "INCONCLUSIVE")
        no_baseline = isolation_summary(
            [self._mk_read(0, 1000, 110)], t0_ms=0, t1_ms=2000,
            burst_tenant_idx=0, baseline_p95=None, degradation_threshold=2.0,
        )
        self.assertEqual(no_baseline["verdict"], "INCONCLUSIVE")

    def test_isolation_cross_slightly_worse_within_threshold_passes(self) -> None:
        # 真实模型路径：embedding 全局共享，burst 抽取导致所有租户公平退化，
        # cross 略高于 same（比值 < 串扰容差 1.25）且均 < 阈值 → PASS。
        records = [
            self._mk_read(0, 1000, 100),
            self._mk_read(0, 1000, 110),
            self._mk_read(0, 1000, 115),
            self._mk_read(0, 1000, 118),
            self._mk_read(1, 1000, 105),
            self._mk_read(1, 1000, 115),
            self._mk_read(1, 1000, 120),
            self._mk_read(1, 1000, 125),
        ]
        result = isolation_summary(
            records, t0_ms=0, t1_ms=2000, burst_tenant_idx=0,
            baseline_p95=100.0, degradation_threshold=2.0,
        )
        self.assertEqual(result["verdict"], "PASS")
        self.assertGreater(result["cross_tenant_degradation"], result["same_tenant_degradation"])

    def test_isolation_cross_significantly_worse_fails(self) -> None:
        # 跨租户显著高于同租户（比值 > 串扰容差 1.25）且均 < 阈值 → 隔离失效 FAIL。
        records = [
            self._mk_read(0, 1000, 100),
            self._mk_read(0, 1000, 105),
            self._mk_read(0, 1000, 108),
            self._mk_read(0, 1000, 110),
            self._mk_read(1, 1000, 130),
            self._mk_read(1, 1000, 135),
            self._mk_read(1, 1000, 140),
            self._mk_read(1, 1000, 145),
        ]
        result = isolation_summary(
            records, t0_ms=0, t1_ms=2000, burst_tenant_idx=0,
            baseline_p95=100.0, degradation_threshold=2.0,
        )
        self.assertEqual(result["verdict"], "FAIL")
        self.assertIn("串扰", result["reason"])


class RSSNormalizedTrendTests(unittest.TestCase):
    """RSS 归一校正：净 RSS = 原始 − 累计注入字节，斜率判定用净序列。"""

    def test_rss_normalized(self) -> None:
        net = rss_normalized_series([(0.0, 1000.0), (10.0, 1200.0)], [(0.0, 100.0), (10.0, 200.0)])
        self.assertEqual(net, [(0.0, 900.0), (10.0, 1000.0)])

    def test_rss_normalized_aligns_cumulative(self) -> None:
        # raw 采样点在注入序列中间 → 取该时刻前累计注入
        net = rss_normalized_series([(5.0, 1100.0)], [(0.0, 100.0), (10.0, 300.0)])
        self.assertEqual(net, [(5.0, 1000.0)])

    def test_rss_normalized_empty_inputs(self) -> None:
        self.assertEqual(rss_normalized_series([], [(0.0, 1.0)]), [])
        self.assertEqual(rss_normalized_series([(0.0, 1.0)], []), [])

    def test_injected_bytes_series(self) -> None:
        records = [
            RequestRecord(scene_key="B@1", step_conc=1, tenant_idx=0, op="add",
                          stage_ms=1.0, status="ok", error_type="", ts_ms=1000.0, content_bytes=10),
            RequestRecord(scene_key="B@1", step_conc=1, tenant_idx=0, op="add",
                          stage_ms=1.0, status="ok", error_type="", ts_ms=2000.0, content_bytes=20),
            RequestRecord(scene_key="B@1", step_conc=1, tenant_idx=0, op="add",
                          stage_ms=1.0, status="error", error_type="timeout", ts_ms=3000.0, content_bytes=5),
        ]
        self.assertEqual(injected_bytes_series(records), [(1.0, 10.0), (2.0, 30.0)])


class MockProviderTests(unittest.TestCase):
    """故障注入 mock：500/挂起/429/恢复 行为与错误类型分类。"""

    def setUp(self) -> None:
        self.provider = MockProvider(port=0, hang_s=5.0, retry_after_s=1)

    def test_ok_behavior(self) -> None:
        self.provider.start()
        try:
            result = probe(self.provider.url, timeout_s=2.0)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["error_type"], "")
        finally:
            self.provider.stop()

    def test_error500_classified_5xx(self) -> None:
        self.provider.start()
        try:
            self.provider.set_behavior("error500")
            result = probe(self.provider.url, timeout_s=2.0)
            self.assertEqual(result["error_type"], "http_5xx")
            self.assertEqual(result["code"], 500)
        finally:
            self.provider.stop()

    def test_rate_limit_retry_after(self) -> None:
        self.provider.start()
        try:
            self.provider.set_behavior("rate_limit")
            result = probe(self.provider.url, timeout_s=2.0)
            self.assertEqual(result["error_type"], "http_4xx")
            self.assertEqual(result["retry_after"], "1")
        finally:
            self.provider.stop()

    def test_hang_times_out(self) -> None:
        self.provider.start()
        try:
            self.provider.set_behavior("hang")
            result = probe(self.provider.url, timeout_s=0.3)
            self.assertEqual(result["error_type"], "timeout")
        finally:
            self.provider.stop()

    def test_restore_recovers(self) -> None:
        self.provider.start()
        try:
            self.provider.set_behavior("restore")
            result = probe(self.provider.url, timeout_s=2.0)
            self.assertEqual(result["status"], "ok")
        finally:
            self.provider.stop()

    def test_run_fault_sequence_summary(self) -> None:
        self.provider.start()
        try:
            stages = [
                {"stage": "baseline", "behavior": "ok", "expected_error_type": "", "requests": 2},
                {"stage": "e500", "behavior": "error500", "expected_error_type": "http_5xx", "requests": 2},
                {"stage": "recover", "behavior": "restore", "expected_error_type": "", "requests": 2, "recovered": True},
            ]
            sequence = run_fault_sequence(self.provider, stages=stages, timeout_s=2.0)
            summary = fault_injection_summary(sequence)
            self.assertEqual(summary["verdict"], "PASS")
            self.assertEqual(len(summary["stages"]), 3)
        finally:
            self.provider.stop()


class PreflightTests(unittest.TestCase):
    """模型/配置预检门禁：解析、env 检查、最小真实请求、失败即停。"""

    def _write_config(self, payload) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(payload, f)
            return f.name

    def test_parse_engine_configs_dict_and_list(self) -> None:
        path = self._write_config(
            {"engines": [{"id": "e1", "kind": "llm", "api_key_env": "K", "api_base": "http://x/", "model": "m"}]}
        )
        engines = parse_engine_configs(path)
        os.unlink(path)
        self.assertEqual(engines[0]["id"], "e1")
        self.assertEqual(engines[0]["api_base"], "http://x")  # rstrip("/")

    def test_parse_requires_fields(self) -> None:
        path = self._write_config({"engines": [{"id": "e1"}]})
        try:
            with self.assertRaises(ValueError):
                parse_engine_configs(path)
        finally:
            os.unlink(path)

    def test_parse_native_echomem_nested_config(self) -> None:
        path = self._write_config(
            {
                "model": {
                    "llm": {
                        "provider": "openai_compatible",
                        "api_base": "https://llm.example/v1",
                        "api_key_env": "LLM_KEY",
                        "model": "real-llm",
                    },
                    "embedding": {
                        "provider": "openai_compatible",
                        "api_base": "https://embed.example/v1",
                        "api_key_env": "EMBED_KEY",
                        "model": "real-embed",
                    },
                    "vlm": {
                        "provider": "fake",
                        "api_base": "",
                        "model": "fake-vlm",
                    },
                },
                "engine": {
                    "configs": {
                        "atomic_engine": {
                            "model": {
                                "llm": {
                                    "provider": "openai_compatible",
                                    "api_base": "https://llm.example/v1",
                                    "api_key_env": "LLM_KEY",
                                    "model": "real-llm",
                                }
                            }
                        }
                    }
                },
            }
        )
        try:
            engines = parse_engine_configs(path)
        finally:
            os.unlink(path)
        self.assertEqual(len(engines), 3)
        self.assertEqual({item["kind"] for item in engines}, {"llm", "embedding"})
        self.assertNotIn("fake-vlm", {item["model"] for item in engines})

    def test_parse_native_config_skips_disabled_optional_model_branch(self) -> None:
        path = self._write_config(
            {
                "model": {
                    "llm": {
                        "provider": "openai_compatible",
                        "api_base": "https://llm.example/v1",
                        "api_key_env": "LLM_KEY",
                        "model": "real-llm",
                    }
                },
                "recall": {
                    "model": {
                        "rerank": {
                            "enabled": False,
                            "provider": "dashscope",
                            "api_base": "https://rerank.example/v1",
                            "api_key_env": "RERANK_KEY",
                            "model": "rerank-model",
                        }
                    }
                },
            }
        )
        try:
            engines = parse_engine_configs(path)
        finally:
            os.unlink(path)
        self.assertEqual([item["id"] for item in engines], ["model.llm"])

    def test_parse_native_config_skips_inactive_intent_llm(self) -> None:
        path = self._write_config(
            {
                "recall": {
                    "search": {"intent": {"backend": "rule"}},
                    "model": {
                        "intent_llm": {
                            "provider": "openai_compatible",
                            "api_base": "https://intent.example/v1",
                            "api_key_env": "INTENT_KEY",
                            "model": "unsupported-model",
                        }
                    },
                },
                "model": {
                    "llm": {
                        "provider": "openai_compatible",
                        "api_base": "https://llm.example/v1",
                        "api_key_env": "LLM_KEY",
                        "model": "supported-model",
                    }
                },
            }
        )
        try:
            engines = parse_engine_configs(path)
        finally:
            os.unlink(path)
        self.assertEqual([item["id"] for item in engines], ["model.llm"])

    def test_parse_native_config_skips_intent_llm_from_engine_search_config(self) -> None:
        path = self._write_config(
            {
                "engine": {
                    "configs": {
                        "atomic_engine": {
                            "search": {"intent": {"backend": "rule"}}
                        }
                    }
                },
                "recall": {
                    "model": {
                        "intent_llm": {
                            "provider": "openai_compatible",
                            "api_base": "https://intent.example/v1",
                            "api_key_env": "INTENT_KEY",
                            "model": "unsupported-model",
                        }
                    }
                },
                "model": {
                    "llm": {
                        "provider": "openai_compatible",
                        "api_base": "https://llm.example/v1",
                        "api_key_env": "LLM_KEY",
                        "model": "supported-model",
                    }
                },
            }
        )
        try:
            engines = parse_engine_configs(path)
        finally:
            os.unlink(path)
        self.assertEqual([item["id"] for item in engines], ["model.llm"])

    def test_config_digest_stable(self) -> None:
        self.assertEqual(config_digest([{"a": 1}]), config_digest([{"a": 1}]))
        self.assertNotEqual(config_digest([{"a": 1}]), config_digest([{"a": 2}]))

    def test_check_env(self) -> None:
        with mock.patch.dict(os.environ, {"PERF_MOCK_KEY": "v"}, clear=False):
            self.assertEqual(check_env([{"id": "e", "api_key_env": "PERF_MOCK_KEY"}]), [])
            errors = check_env([{"id": "e", "api_key_env": "PERF_MISSING_KEY"}])
            self.assertEqual(len(errors), 1)
            self.assertIn("PERF_MISSING_KEY", errors[0])

    def test_run_preflight_config_error(self) -> None:
        result = run_preflight("/nonexistent/preflight.json")
        self.assertFalse(result["ok"])
        self.assertIn("配置读取失败", result["error"])

    def test_preflight_retries_only_transport_failures(self) -> None:
        self.assertTrue(
            _retryable_probe_failure(
                {"code": None, "error": "endpoint 不可达/超时: <urlopen error [Errno 8] nodename nor servname provided, or not known>"}
            )
        )
        self.assertFalse(
            _retryable_probe_failure(
                {"code": 401, "error": "HTTP 401（模型不可用）"}
            )
        )

    def test_probe_endpoint_against_mock(self) -> None:
        provider = MockProvider(port=0)
        provider.start()
        try:
            engine = {"id": "e", "kind": "llm", "api_key_env": "", "api_base": provider.url, "model": "m"}
            result = probe_endpoint(engine, timeout_s=2.0)
            self.assertTrue(result["model_supported"])
            self.assertEqual(result["status"], "ok")
        finally:
            provider.stop()


class ErrorTypeTests(unittest.TestCase):
    """服务端错误类型正确性 / 故障注入判定 / 判定分层状态。"""

    def test_error_type_validation_pass(self) -> None:
        records = [RequestRecord(
            scene_key="B@1", step_conc=1, tenant_idx=0, op="commit_submit",
            stage_ms=1.0, status="error", error_type="http_5xx", ts_ms=0.0,
        )]
        fault = {"stages": [{"stage": "s", "expected_error_type": "http_5xx",
                             "observed_error_type": "http_5xx", "ok": True}]}
        result = error_type_validation(records, fault)
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["observed_breakdown"], {"http_5xx": 1})

    def test_error_type_validation_mismatch_fails(self) -> None:
        fault = {"stages": [{"stage": "s", "expected_error_type": "timeout",
                             "observed_error_type": "http_5xx", "ok": False}]}
        result = error_type_validation([], fault)
        self.assertEqual(result["verdict"], "FAIL")

    def test_error_type_validation_not_run(self) -> None:
        self.assertEqual(error_type_validation([])["verdict"], "not_run")

    def test_fault_injection_summary_pass(self) -> None:
        sequence = [
            {"stage": "a", "behavior": "ok", "expected_error_type": "", "observed_error_type": "",
             "requests": 2, "hang": False, "recovered": True},
            {"stage": "b", "behavior": "error500", "expected_error_type": "http_5xx",
             "observed_error_type": "http_5xx", "requests": 2, "hang": False, "recovered": False},
        ]
        summary = fault_injection_summary(sequence)
        self.assertEqual(summary["verdict"], "PASS")
        self.assertTrue(all(stage["ok"] for stage in summary["stages"]))

    def test_fault_injection_summary_fail(self) -> None:
        sequence = [
            {"stage": "a", "behavior": "error500", "expected_error_type": "http_5xx",
             "observed_error_type": "timeout", "requests": 2, "hang": False, "recovered": False},
        ]
        self.assertEqual(fault_injection_summary(sequence)["verdict"], "FAIL")

    def test_fault_injection_not_run(self) -> None:
        self.assertEqual(fault_injection_summary([])["verdict"], "not_run")

    def test_verdict_layers_and_slo(self) -> None:
        summary = {
            "config": {"degradation_threshold": 2.0, "no_metrics": False},
            "server": {"metrics_available": True},
            "commit_durability": {"submit_ok_total": 1, "submit_rejected_total": 0,
                                  "accepted_done_ok": 1, "accepted_done_failed": 0,
                                  "accepted_done_other": 0, "guarantee_violations": 0,
                                  "commit_success_rate": 1.0},
            "degradation": {},
            "tenant_fairness": {},
            "resources": {"rss_trend": {"slope_mb_per_min": 0.5, "r2": 0.9, "samples": 10},
                          "rss_unsettled_mb": 1.0},
            "fault_injection": {"stages": [], "verdict": "PASS", "reason": "全部一致"},
            "isolation": {"D@4": {"verdict": "PASS", "reason": "同/跨租户隔离判定通过",
                                  "same_tenant": {}, "cross_tenant": {}}},
        }
        result = evaluate_features(summary)
        self.assertIn("verdict_layers", result)
        self.assertIn("slo_accounting", result)
        self.assertEqual(result["verdict_layers"]["mock"]["fault_injection"], "PASS")
        self.assertIn("write_submit_success_raw", result["slo_accounting"])
        self.assertEqual(result["features"]["isolation_granularity"]["verdict"], "PASS")

    def test_isolation_verdict_fail_from_scenes(self) -> None:
        from performance.metrics_calc import evaluate_features

        summary = {
            "config": {"degradation_threshold": 2.0, "no_metrics": True},
            "server": {"metrics_available": False},
            "commit_durability": {},
            "degradation": {},
            "tenant_fairness": {},
            "resources": {},
            "isolation": {"D@4": {"verdict": "FAIL", "reason": "跨租户劣化超阈值"}},
        }
        result = evaluate_features(summary)
        self.assertEqual(result["features"]["isolation_granularity"]["verdict"], "FAIL")
        self.assertEqual(result["overall"], "FAIL")


class RetryContractTests(unittest.TestCase):
    """逐请求 retry 契约：reason_code 提取 / commit_submit 携带 / retry_summary 扩展。"""

    @staticmethod
    def _http_error(code: int = 429, headers=None, body: str | None = None):
        fp = io.BytesIO(body.encode("utf-8")) if body is not None else None
        exc = urllib.error.HTTPError("http://x", code, "msg", None, fp)
        if headers:
            exc.headers = headers
        return exc

    def test_extract_reason_code_from_header(self) -> None:
        exc = self._http_error(headers={"X-Reason-Code": "rate_limited"})
        self.assertEqual(extract_reason_code(exc), "rate_limited")

    def test_extract_reason_code_from_body_top_level(self) -> None:
        exc = self._http_error(body='{"reason_code": "quota_exceeded"}')
        self.assertEqual(extract_reason_code(exc), "quota_exceeded")

    def test_extract_reason_code_from_error_nested(self) -> None:
        exc = self._http_error(body='{"error": {"errorCode": "rate_limited"}}')
        self.assertEqual(extract_reason_code(exc), "rate_limited")

    def test_extract_reason_code_from_meta_nested(self) -> None:
        exc = self._http_error(body='{"meta": {"reasonCode": "quota"}}')
        self.assertEqual(extract_reason_code(exc), "quota")

    def test_extract_reason_code_not_found(self) -> None:
        exc = self._http_error(body='{"message": "just a message"}')
        self.assertEqual(extract_reason_code(exc), "")
        self.assertEqual(extract_reason_code(RuntimeError("boom")), "")

    def test_commit_submit_failure_carries_retry_contract(self) -> None:
        client = FakeMemClient()
        client.commit_429_left = 1
        client.retry_after_s = "2"
        client.commit_reason_code = "rate_limited"
        result = run_write_transaction(
            client,
            scene_key="B@1", step_conc=1, tenant_idx=0, seq=1,
            messages_per_session=3, commit_poll_timeout_s=30.0,
            commit_retry_max=2, commit_retry_backoff_s=0.01,
        )
        submit = [rec for rec in result.records if rec.op == "commit_submit"][0]
        self.assertTrue(result.ok)
        self.assertEqual(submit.retry_after_s, 2.0)
        self.assertEqual(submit.reason_code, "rate_limited")

    def test_commit_submit_success_carries_last_retry_contract(self) -> None:
        # 重试后成功的提交同样记录最后一次失败尝试的 retry 契约字段
        client = FakeMemClient()
        client.commit_429_left = 1
        client.retry_after_s = "1"
        client.commit_reason_code = "rate_limited"
        result = run_write_transaction(
            client,
            scene_key="B@1", step_conc=1, tenant_idx=0, seq=1,
            messages_per_session=2, commit_poll_timeout_s=30.0,
            commit_retry_max=2, commit_retry_backoff_s=0.01,
        )
        submit = [rec for rec in result.records if rec.op == "commit_submit"][0]
        self.assertTrue(submit.final_success)
        self.assertEqual(submit.retry_after_s, 1.0)
        self.assertEqual(submit.reason_code, "rate_limited")

    def test_retry_summary_includes_retry_after_and_reason_codes(self) -> None:
        client = FakeMemClient()
        client.commit_429_left = 1
        client.retry_after_s = "2"
        client.commit_reason_code = "rate_limited"
        result = run_write_transaction(
            client,
            scene_key="B@1", step_conc=1, tenant_idx=0, seq=1,
            messages_per_session=3, commit_poll_timeout_s=30.0,
            commit_retry_max=2, commit_retry_backoff_s=0.01,
        )
        summary = retry_summary(result.records)
        self.assertEqual(summary["retry_after_s"]["max"], 2.0)
        self.assertEqual(summary["reason_codes"], {"rate_limited": 1})


class BarrierTests(unittest.TestCase):
    """commit barrier：租户分布（uniform/zipf/explicit）与并发 commit 记录。"""

    @staticmethod
    def _tenant(idx: int) -> TenantContext:
        client = FakeMemClient()
        return TenantContext(
            idx=idx, tenant_id=f"t{idx}", user_id=f"u{idx}", auth_key="k", client=client
        )

    def _run(self, scene: SceneRun, tenant_count: int):
        gen = LoadGenerator()
        tenants = [self._tenant(i) for i in range(tenant_count)]
        records = gen.run_commit_barrier(scene, tenants, messages_per_session=2)
        return records

    def _submit_counts(self, records):
        counts: dict[int, int] = {}
        for rec in records:
            if rec.op == "commit_submit":
                counts[rec.tenant_idx] = counts.get(rec.tenant_idx, 0) + 1
        return counts

    def test_uniform_distribution(self) -> None:
        scene = SceneRun("S", 1, 60.0, barrier_commits=10, barrier_distribution="uniform")
        counts = self._submit_counts(self._run(scene, 2))
        self.assertEqual(counts, {0: 5, 1: 5})

    def test_uniform_remainder_to_first(self) -> None:
        scene = SceneRun("S", 1, 60.0, barrier_commits=10, barrier_distribution="uniform")
        counts = self._submit_counts(self._run(scene, 3))
        self.assertEqual(counts, {0: 4, 1: 3, 2: 3})

    def test_zipf_distribution(self) -> None:
        scene = SceneRun(
            "S", 1, 60.0, barrier_commits=10,
            barrier_distribution="zipf", barrier_zipf_exponent=2.0,
        )
        counts = self._submit_counts(self._run(scene, 2))
        self.assertEqual(counts, {0: 8, 1: 2})

    def test_explicit_distribution(self) -> None:
        scene = SceneRun(
            "H", 1, 60.0, barrier_commits=10,
            barrier_distribution="explicit", barrier_tenant_counts=[7, 3],
        )
        counts = self._submit_counts(self._run(scene, 2))
        self.assertEqual(counts, {0: 7, 1: 3})

    def test_explicit_distribution_is_preserved_for_s_scene(self) -> None:
        runs = expand_matrix(
            scenario_ids=["S"],
            concurrency_steps=[1],
            mix_ratios=[(1, 1)],
            duration_s=1.0,
            burst_commits=0,
            burst_window_s=0.0,
            barrier_commits=10,
            barrier_distribution="explicit",
            barrier_tenant_counts=[7, 3],
        )
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].scene_id, "S")
        self.assertEqual(runs[0].barrier_distribution, "explicit")
        self.assertEqual(runs[0].barrier_tenant_counts, [7, 3])
        self.assertEqual(runs[0].barrier_commits, 10)

    def test_explicit_count_length_mismatch_raises(self) -> None:
        scene = SceneRun(
            "H", 1, 60.0, barrier_commits=10,
            barrier_distribution="explicit", barrier_tenant_counts=[7],
        )
        with self.assertRaises(ValueError):
            self._run(scene, 2)

    def test_commit_polling_is_concurrent(self) -> None:
        class DelayedPollClient(FakeMemClient):
            def poll_commit(self, session_id, archive_id, **kwargs):
                time.sleep(0.1)
                return super().poll_commit(session_id, archive_id, **kwargs)

        scene = SceneRun(
            "S", 1, 60.0, barrier_commits=4,
            barrier_distribution="uniform",
        )
        gen = LoadGenerator(barrier_wave_size=4)
        tenants = []
        for idx in range(4):
            client = DelayedPollClient()
            tenants.append(
                TenantContext(
                    idx=idx,
                    tenant_id=f"t{idx}",
                    user_id=f"u{idx}",
                    auth_key="k",
                    client=client,
                )
            )

        started = time.perf_counter()
        records = gen.run_commit_barrier(scene, tenants, messages_per_session=1)
        elapsed = time.perf_counter() - started

        self.assertEqual(
            4,
            sum(1 for record in records if record.op == "commit_done" and record.status == "ok"),
        )
        # Four 100 ms polls should remain close to one wave, not four serial waits.
        self.assertLess(elapsed, 0.30)

    def test_explicit_count_sum_mismatch_raises(self) -> None:
        scene = SceneRun(
            "H", 1, 60.0, barrier_commits=10,
            barrier_distribution="explicit", barrier_tenant_counts=[8, 3],
        )
        with self.assertRaises(ValueError):
            self._run(scene, 2)

    def test_non_positive_commits_raises(self) -> None:
        scene = SceneRun("S", 1, 60.0, barrier_commits=0)
        with self.assertRaises(ValueError):
            self._run(scene, 2)

    def test_barrier_records_shape(self) -> None:
        scene = SceneRun("S", 1, 60.0, barrier_commits=4, barrier_distribution="uniform")
        records = self._run(scene, 2)
        ops = [rec.op for rec in records]
        self.assertEqual(ops.count("open"), 4)
        self.assertEqual(ops.count("add"), 4 * 2)  # 每会话 2 条消息
        self.assertEqual(ops.count("commit_submit"), 4)
        self.assertEqual(ops.count("commit_done"), 4)
        submits = [rec for rec in records if rec.op == "commit_submit"]
        self.assertTrue(all(rec.extra == "barrier" for rec in submits))
        self.assertTrue(all(rec.status == "ok" for rec in submits))


class IsolationProbeSummaryTests(unittest.TestCase):
    """isolation_probe_summary：全符合→PASS / 跨租户假阳性→FAIL / 无记录→INCONCLUSIVE。"""

    @staticmethod
    def _probe(writer: int, reader: int, found: bool, expected: bool, status: str = "ok"):
        return RequestRecord(
            scene_key="I@1", step_conc=1, tenant_idx=reader, op="isolation_probe",
            stage_ms=10.0, status=status, error_type="", ts_ms=0.0,
            extra=json.dumps({
                "writer": writer, "reader": reader,
                "same_tenant": writer == reader,
                "marker_found": found, "expected": expected,
                "latency_ms": 10.0,
            }),
        )

    def test_all_matching_passes(self) -> None:
        records = [
            self._probe(0, 0, True, True),    # 同租户命中
            self._probe(0, 1, False, False),  # 跨租户不命中
            self._probe(1, 1, True, True),
            self._probe(1, 0, False, False),
        ]
        summary = isolation_probe_summary(records)
        self.assertEqual(summary["verdict"], "PASS")
        self.assertEqual(summary["same_tenant_hit_rate"], 1.0)
        self.assertEqual(summary["cross_tenant_false_positive_rate"], 0.0)

    def test_cross_false_positive_fails(self) -> None:
        records = [
            self._probe(0, 0, True, True),
            self._probe(0, 1, True, False),  # 跨租户假阳性
        ]
        summary = isolation_probe_summary(records)
        self.assertEqual(summary["verdict"], "FAIL")
        self.assertEqual(summary["invalid_probe_count"], 1)

    def test_no_records_inconclusive(self) -> None:
        summary = isolation_probe_summary([])
        self.assertEqual(summary["verdict"], "INCONCLUSIVE")

    def test_interrupted_probe_inconclusive(self) -> None:
        records = [
            self._probe(0, 0, True, True),
            self._probe(0, 1, False, False, status="error"),
        ]
        summary = isolation_probe_summary(records)
        self.assertEqual(summary["verdict"], "INCONCLUSIVE")


class SaturationSummaryTests(unittest.TestCase):
    """saturation_summary：429/503 拒绝样本的 Retry-After / reason_code 契约。"""

    @staticmethod
    def _rec(
        op,
        status,
        error_type="",
        retry_after=None,
        reason="",
        stage=10.0,
        http_status=None,
    ):
        return RequestRecord(
            scene_key="S@1", step_conc=1, tenant_idx=0, op=op,
            stage_ms=stage, status=status, error_type=error_type, ts_ms=0.0,
            http_status=http_status,
            retry_after_s=retry_after, reason_code=reason,
        )

    def test_pass_when_all_rejections_carry_contract(self) -> None:
        records = [
            self._rec("read", "ok"),
            self._rec("commit_submit", "error", "http_4xx", retry_after=1.0, reason="rate_limited", http_status=429),
            self._rec("commit_submit", "error", "http_4xx", retry_after=2.0, reason="rate_limited", http_status=429),
        ]
        summary = saturation_summary(records)
        self.assertEqual(summary["verdict"], "PASS")
        self.assertEqual(summary["rejected_total"], 2)
        self.assertEqual(summary["retry_after_present"], 2)
        self.assertEqual(summary["reason_code_present"], 2)
        self.assertAlmostEqual(summary["rejection_rate"], 2 / 3, places=5)

    def test_fail_when_reason_code_missing(self) -> None:
        records = [
            self._rec("commit_submit", "error", "http_4xx", retry_after=1.0, http_status=429),
        ]
        summary = saturation_summary(records)
        self.assertEqual(summary["verdict"], "FAIL")

    def test_fail_when_retry_after_missing(self) -> None:
        records = [
            self._rec("commit_submit", "error", "http_4xx", reason="rate_limited", http_status=503),
        ]
        summary = saturation_summary(records)
        self.assertEqual(summary["verdict"], "FAIL")

    def test_inconclusive_without_rejections(self) -> None:
        records = [
            self._rec("read", "ok"),
            self._rec("read", "error", "timeout"),
            self._rec("commit_submit", "error", "http_4xx"),  # 无 retry_after/reason → 非拒绝
        ]
        summary = saturation_summary(records)
        self.assertEqual(summary["verdict"], "INCONCLUSIVE")
        self.assertEqual(summary["rejected_total"], 0)

    def test_rejection_latency_percentiles(self) -> None:
        records = [
            self._rec("read", "ok"),
            self._rec("commit_submit", "error", "http_4xx", retry_after=1.0, reason="x", stage=100.0, http_status=429),
            self._rec("commit_submit", "error", "http_4xx", retry_after=1.0, reason="x", stage=200.0, http_status=429),
            self._rec("commit_submit", "error", "http_4xx", retry_after=1.0, reason="x", stage=300.0, http_status=429),
        ]
        summary = saturation_summary(records)
        self.assertEqual(summary["rejection_p50_ms"], 200.0)
        self.assertEqual(summary["rejection_p95_ms"], 290.0)


class HotTenantSummaryTests(unittest.TestCase):
    """hot_tenant_summary：旁观租户 commit P50 散布（1.50 阈值）。"""

    @staticmethod
    def _commit(tenant: int, stage: float, count: int = 1):
        return [
            RequestRecord(
                scene_key="H@1", step_conc=1, tenant_idx=tenant, op="commit_submit",
                stage_ms=stage, status="ok", error_type="", ts_ms=0.0,
            )
            for _ in range(count)
        ]

    def test_fair_ratio_passes(self) -> None:
        records = (
            self._commit(0, 100.0, count=100) +  # 热租户（提交数 ≥ 总 1/4）
            self._commit(1, 100.0, count=10) +   # 旁观
            self._commit(2, 140.0, count=10)     # 旁观
        )
        summary = hot_tenant_summary(records)
        self.assertEqual(summary["verdict"], "PASS")
        self.assertEqual(summary["bystander_p50_ratio"], 1.4)

    def test_ratio_boundary_1_50_passes(self) -> None:
        records = (
            self._commit(0, 100.0, count=100) +
            self._commit(1, 100.0, count=10) +
            self._commit(2, 150.0, count=10)
        )
        summary = hot_tenant_summary(records)
        self.assertEqual(summary["verdict"], "PASS")
        self.assertEqual(summary["bystander_p50_ratio"], 1.5)

    def test_unfair_ratio_fails(self) -> None:
        records = (
            self._commit(0, 100.0, count=100) +
            self._commit(1, 100.0, count=10) +
            self._commit(2, 200.0, count=10)
        )
        summary = hot_tenant_summary(records)
        self.assertEqual(summary["verdict"], "FAIL")

    def test_single_bystander_passes(self) -> None:
        records = self._commit(0, 100.0, count=100) + self._commit(1, 300.0, count=10)
        summary = hot_tenant_summary(records)
        self.assertEqual(summary["verdict"], "PASS")

    def test_insufficient_data_inconclusive(self) -> None:
        self.assertEqual(hot_tenant_summary([])["verdict"], "INCONCLUSIVE")


class TenantSpecsTests(unittest.TestCase):
    """tenants.json 独立凭据：env 解析 / 缺 env / keys_independent。"""

    def _write(self, tmp: Path, payload) -> Path:
        path = Path(tmp) / "tenants.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_load_tenant_specs_resolves_env_keys(self) -> None:
        payload = {
            "tenants": [
                {"tenant_id": "t1", "user_id": "u1", "auth_key_env": "PERF_KEY_1"},
                {"tenant_id": "t2", "user_id": "u2", "auth_key_env": "PERF_KEY_2"},
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), payload)
            with mock.patch.dict(os.environ, {"PERF_KEY_1": "k1", "PERF_KEY_2": "k2"}, clear=False):
                specs = load_tenant_specs(path)
        self.assertEqual([s["auth_key"] for s in specs], ["k1", "k2"])
        self.assertEqual([s["tenant_id"] for s in specs], ["t1", "t2"])

    def test_load_tenant_specs_inline_key_preferred(self) -> None:
        payload = {
            "tenants": [
                {"tenant_id": "t1", "user_id": "u1", "auth_key": "inline", "auth_key_env": "PERF_MISSING_ENV"},
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), payload)
            specs = load_tenant_specs(path)  # 内联优先，不读缺失的 env
        self.assertEqual(specs[0]["auth_key"], "inline")

    def test_load_tenant_specs_missing_env_raises(self) -> None:
        payload = {
            "tenants": [{"tenant_id": "t1", "user_id": "u1", "auth_key_env": "PERF_NO_SUCH_ENV"}]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), payload)
            with self.assertRaises(ValueError) as ctx:
                load_tenant_specs(path)
        self.assertIn("PERF_NO_SUCH_ENV", str(ctx.exception))

    def test_load_tenant_specs_empty_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), {"tenants": []})
            with self.assertRaises(ValueError):
                load_tenant_specs(path)
            missing = self._write(Path(tmp), {"other": 1})
            with self.assertRaises(ValueError):
                load_tenant_specs(missing)

    def test_keys_independent(self) -> None:
        independent = TenantPreparer(
            "http://x", tenant_specs=[{"auth_key": "a"}, {"auth_key": "b"}]
        )
        self.assertTrue(independent.keys_independent())
        duplicate = TenantPreparer(
            "http://x", tenant_specs=[{"auth_key": "a"}, {"auth_key": "a"}]
        )
        self.assertFalse(duplicate.keys_independent())
        empty = TenantPreparer("http://x", tenant_specs=[{"auth_key": ""}])
        self.assertFalse(empty.keys_independent())
        # 非 config 模式（provision 天然独立）
        self.assertTrue(TenantPreparer("http://x").keys_independent())

    def test_tenant_config_is_reported_as_effective_identity_mode(self) -> None:
        preparer = TenantPreparer(
            "http://x",
            auth_mode="provision",
            tenant_specs=[{"tenant_id": "t1", "auth_key": "key"}],
        )
        self.assertEqual("tenant_config", preparer.identity_mode())
        self.assertEqual("provision", TenantPreparer("http://x").identity_mode())

    def test_prepare_error_keeps_http_status_url_and_bounded_body(self) -> None:
        error = RuntimeError("request failed")
        error.echomem_status = 401
        error.echomem_url = "http://127.0.0.1:8010/api/sessions/open"
        error.echomem_body = "x" * 1000
        detail = _format_prepare_error(error)
        self.assertIn("HTTP 401", detail)
        self.assertIn("/api/sessions/open", detail)
        self.assertEqual(500, len(detail.rsplit("body=", 1)[1]))


class LoadGeneratorSceneTests(unittest.TestCase):
    def test_read_error_records_http_status_and_reason_code(self) -> None:
        generator = LoadGenerator()
        exc = urllib.error.HTTPError(
            "http://x", 429, "rate limited", {"Retry-After": "3"}, io.BytesIO(
                b'{"reason_code":"retrieval_inflight_full"}'
            )
        )
        client = mock.Mock()
        client.search_with_meta.side_effect = exc
        record = generator._read_once(
            client,
            "PERFANCHOR-0-0-0",
            scene_key="K@1",
            step_conc=1,
            tenant_idx=0,
        )
        self.assertEqual(record.error_type, "http_4xx")
        self.assertEqual(record.http_status, 429)
        self.assertEqual(record.reason_code, "retrieval_inflight_full")
        self.assertEqual(record.to_csv_row()["http_status"], 429)

    def test_rate_based_mixed_scene_keeps_read_worker_at_single_tenant(self) -> None:
        generator = LoadGenerator(rps=2.0, commit_rpm=2.0)
        scene = SceneRun("K", 1, 1.0)
        tenant = mock.Mock(idx=0)
        with (
            mock.patch.object(generator, "_read_loop", return_value=[]) as read_loop,
            mock.patch.object(generator, "_write_loop", return_value=[]) as write_loop,
        ):
            generator.run_scene(scene, [tenant], messages_per_session=1)
        self.assertEqual(1, read_loop.call_count)
        self.assertEqual(1, write_loop.call_count)

    def test_rate_based_mixed_scene_keeps_commit_worker_for_per_tenant_rate(self) -> None:
        generator = LoadGenerator(rps=2.0, per_tenant_commit_rpm=2.0)
        scene = SceneRun("K", 1, 1.0)
        tenant = mock.Mock(idx=0)
        with (
            mock.patch.object(generator, "_read_loop", return_value=[]) as read_loop,
            mock.patch.object(generator, "_write_loop", return_value=[]) as write_loop,
        ):
            generator.run_scene(scene, [tenant], messages_per_session=1)
        self.assertEqual(1, read_loop.call_count)
        self.assertEqual(1, write_loop.call_count)

    def test_capacity_scene_with_zero_commit_rate_is_read_only(self) -> None:
        generator = LoadGenerator(rps=8.0, commit_rpm=0.0)
        scene = SceneRun("K", 1, 0.05)
        tenant = mock.Mock(idx=0)
        with (
            mock.patch.object(generator, "_read_loop", return_value=[]) as read_loop,
            mock.patch.object(generator, "_write_loop", return_value=[]) as write_loop,
        ):
            generator.run_scene(scene, [tenant], messages_per_session=1)
        self.assertEqual(1, read_loop.call_count)
        self.assertEqual(0, write_loop.call_count)

    def test_connection_error_threshold_stops_unbounded_read_load(self) -> None:
        generator = LoadGenerator(client_connection_error_abort_threshold=2)
        client = mock.Mock()
        client.search_with_meta.side_effect = urllib.error.URLError("address pool exhausted")
        stop = threading.Event()
        records = generator._read_loop(
            stop,
            mock.Mock(
                idx=0,
                client=client,
                queries=["hello"],
                active_session_ids=[],
            ),
            scene_key="A@1",
            step_conc=1,
        )
        self.assertEqual(2, len(records))
        self.assertTrue(stop.is_set())
        self.assertEqual(
            {
                "connection_errors": 2,
                "abort_threshold": 2,
                "client_resource_exhausted": True,
                "verdict": "CLIENT_RESOURCE_EXHAUSTED",
            },
            generator.client_diagnostics(),
        )


class NewFeatureVerdictTests(unittest.TestCase):
    """evaluate_features 新增特性：tenant_isolation / saturation_contract / hot_tenant_fairness。"""

    @staticmethod
    def _summary(**extra) -> dict:
        base = {
            "config": {"degradation_threshold": 2.0, "no_metrics": True},
            "server": {"metrics_available": False},
            "commit_durability": {},
            "degradation": {},
            "tenant_fairness": {},
            "resources": {},
        }
        base.update(extra)
        return base

    def test_features_not_run_without_data(self) -> None:
        result = evaluate_features(self._summary())
        self.assertEqual(result["features"]["tenant_isolation"]["verdict"], "not_run")
        self.assertEqual(result["features"]["saturation_contract"]["verdict"], "not_run")
        self.assertEqual(result["features"]["hot_tenant_fairness"]["verdict"], "not_run")
        self.assertEqual(result["overall"], "INCONCLUSIVE")  # 其余特性无数据

    def test_tenant_isolation_fail_propagates(self) -> None:
        summary = self._summary()
        summary["isolation_probe"] = {
            "probe_count": 10, "expected_probe_count": 10,
            "invalid_probe_count": 2, "verdict": "FAIL",
            "reason": "2 条隔离探针命中与预期不符",
        }
        result = evaluate_features(summary)
        self.assertEqual(result["features"]["tenant_isolation"]["verdict"], "FAIL")
        self.assertEqual(result["overall"], "FAIL")

    def test_tenant_isolation_pass_becomes_inconclusive(self) -> None:
        summary = self._summary()
        summary["isolation_probe"] = {
            "probe_count": 10, "expected_probe_count": 10,
            "invalid_probe_count": 0, "verdict": "PASS", "reason": "全部符合",
        }
        result = evaluate_features(summary)
        self.assertEqual(result["features"]["tenant_isolation"]["verdict"], "INCONCLUSIVE")

    def test_saturation_contract_maps_verdict(self) -> None:
        summary = self._summary()
        summary["saturation"] = {
            "verdict": "PASS", "reason": "全部带 Retry-After 与 reason_code",
            "rejected_total": 2,
        }
        result = evaluate_features(summary)
        self.assertEqual(result["features"]["saturation_contract"]["verdict"], "PASS")
        summary["saturation"]["verdict"] = "FAIL"
        result = evaluate_features(summary)
        self.assertEqual(result["features"]["saturation_contract"]["verdict"], "FAIL")
        self.assertEqual(result["overall"], "FAIL")

    def test_hot_tenant_fairness_maps_verdict(self) -> None:
        summary = self._summary()
        summary["hot_tenant"] = {
            "verdict": "PASS", "reason": "旁观租户公平", "bystander_p50_ratio": 1.2,
        }
        result = evaluate_features(summary)
        self.assertEqual(result["features"]["hot_tenant_fairness"]["verdict"], "PASS")
        summary["hot_tenant"]["verdict"] = "FAIL"
        result = evaluate_features(summary)
        self.assertEqual(result["features"]["hot_tenant_fairness"]["verdict"], "FAIL")
