import tempfile
import unittest
from pathlib import Path

from performance.targets.echomem.acceptance.plan_report import write_test_plan_report


class PlanReportTest(unittest.TestCase):
    def test_renders_plan_without_interpreting_values_as_measurements(self):
        plan = {
            "title": "方案测试",
            "status": "PLAN_READY_WITH_GAPS",
            "generated_at": "2026-09-14T00:00:00+00:00",
            "overall_conclusion": "只描述计划",
            "scope": "不发请求",
            "profile": {
                "name": "local",
                "base_url": "http://127.0.0.1:8010",
                "resource_container": "test-container",
                "resource_limits": {"cpu": "4 vCPU", "memory": "8 GiB"},
                "models": {"llm": "llm", "embedding": "embedding"},
                "m1_topologies": ["concurrency"],
                "m1_concurrency_levels": [16, 64],
            },
            "execution_state": {"rows": []},
            "current_evidence": {
                "summary": "没有结果",
                "source": "none",
                "comparison": {},
                "caveats": [],
            },
            "concurrency_parameters": {
                "scope": "仅 EchoMem 服务端参数",
                "interpretation": "C=64 是总在途请求",
                "baseline_label": "small 默认",
                "tuning_label": "C=64 调优起点",
                "config_source": "config.json",
                "rows": [
                    {
                        "layer": "Recall",
                        "parameter": "recall.max_inflight",
                        "current": "16",
                        "source": "default",
                        "small_default": "16",
                        "c64_start": "64",
                        "action": "核对",
                        "explanation": "外层闸门",
                    }
                ],
                "topologies": [
                    {"topology": "4T×16", "total_inflight": 64, "per_tenant": 16, "meaning": "等权"}
                ],
                "chart": [{"label": "Recall", "value": 16, "display": "16"}],
                "rules": ["先核对外层闸门"],
                "do_not_change": ["不要缩短 deadline"],
                "config_snippet": '{"recall": {"max_inflight": 64}}',
                "references": [],
            },
            "workflow": [],
            "schedule": [],
            "metrics": [
                {
                    "code": "M1",
                    "name": "容量",
                    "state": "计划",
                    "reflects": "容量含义",
                    "method": "C=1/8/16/64",
                    "boundary": "不等于租户数",
                    "chart": [],
                    "cases": [],
                    "fields": [],
                    "formulas": ["保留分母"],
                    "gaps": ["补基线"],
                    "modules": [],
                    "evidence": [],
                }
            ],
            "contracts": [],
            "commands": [],
            "artifacts": [],
            "delivery_checks": [],
            "improvements": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.html"
            write_test_plan_report(plan, output)
            html = output.read_text(encoding="utf-8")
        self.assertIn("PLAN_READY_WITH_GAPS", html)
        self.assertIn("C=1/8/16/64", html)
        self.assertIn("不发请求", html)
        self.assertIn("C=64 EchoMem 服务端参数审计", html)
        self.assertIn("recall.max_inflight", html)
        self.assertIn("不要缩短 deadline", html)
        self.assertNotIn("sk-", html)


if __name__ == "__main__":
    unittest.main()
