import copy
import unittest

from performance.targets.echomem.orchestrator.commit_diagnostic_report import render_commit_diagnostic_html


def result_fixture():
    scene = {"level": 64, "actual_users": 4, "per_session_concurrency": 16,
             "observed_inflight_peak": 64, "elapsed_s": 12, "offered": 8,
             "operations": {"commit": {"offered": 4, "commit_completed": 3, "commit_timed_out": 1,
                 "commit_failed": 0, "commit_unique_archives": 2, "commit_repeated_archive_observations": 2,
                 "p95_ms": 90000},
                 "search": {"offered": 4, "search_quality_ok": 2, "search_degraded": 1, "p95_ms": 1200}},
             "samples": [{"operation": "search", "quality_ok": True, "recall_hit": True, "quality_observed": True},
                         {"operation": "search", "quality_ok": True, "recall_hit": True, "quality_observed": True},
                         {"operation": "search", "quality_ok": False, "recall_hit": True, "degraded": True, "quality_observed": True},
                         {"operation": "search", "quality_ok": False, "recall_hit": False, "quality_observed": True}]}
    return {"method": "<unsafe>", "profiles": [{"concurrency_topology": {"checks": [{"detail": {"matrix": [scene]}}]},
             "objectives": [{"observed": {"parameters": {"models": {"llm": "example"}}}}]}]}


class DashboardTests(unittest.TestCase):
    def test_chart_partitions_and_escaping(self):
        rendered = render_commit_diagnostic_html(result_fixture())
        self.assertEqual(rendered.count('class="tile '), 8)
        self.assertIn("3次", rendered)
        self.assertIn("90秒内未完成1次", rendered)
        self.assertIn("命中且无降级2次", rendered)
        self.assertIn("涉及 2 个独立归档任务", rendered)
        self.assertIn("&lt;unsafe&gt;", rendered)
        self.assertNotIn("<unsafe>", rendered)

    def test_comparison_and_latency_units(self):
        result = result_fixture()
        result["comparison"] = copy.deepcopy(result)
        result["comparison"]["profiles"][0]["concurrency_topology"]["checks"][0]["detail"]["matrix"][0]["level"] = 16
        rendered = render_commit_diagnostic_html(result)
        self.assertIn("从 16 到 64 并发", rendered)
        self.assertIn("90.00<small>秒", rendered)
        self.assertIn("1.20<small>秒", rendered)
        self.assertIn("两轮请求总数不同", rendered)


if __name__ == "__main__":
    unittest.main()
