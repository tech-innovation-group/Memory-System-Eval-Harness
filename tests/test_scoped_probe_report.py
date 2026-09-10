import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import json
import tempfile
from pathlib import Path

from performance.targets.echomem.orchestrator.report import render_objective_suite_html


class ScopedProbeReportTest(unittest.TestCase):
    def test_extended_report_does_not_claim_missing_cases_complete(self):
        from scripts.build_extended_boundary_report import build
        with tempfile.TemporaryDirectory() as tmp:
            result = build(Path(tmp))
        self.assertTrue(all(row[1] == 'NOT_RUN' for row in result['overview']['rows']))
        self.assertEqual(len(result['overview']['rows']), 5)
        self.assertIn('不是M1-M3完整验收', result['scope'])

    def test_mcp_uses_echomem_user_message_argument(self):
        from performance.targets.echomem.probes.payload_boundary import _mcp_add_memory
        with patch("plugins.echomem_mcp.mcp_client.McpClient") as client:
            client.return_value.call_tool.return_value = "session-1"
            result = _mcp_add_memory({"mcp_base_url": "http://test"}, SimpleNamespace(auth_key="test"), "long body")
        args = client.return_value.call_tool.call_args.args[1]
        self.assertEqual(args["user_message"], "long body")
        self.assertNotIn("content", args)
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertEqual(result["reason_code"], "HISTORY_VERIFIER_NOT_CONFIGURED")

    def test_mcp_full_content_readback_required(self):
        from performance.targets.echomem.probes.payload_boundary import _mcp_add_memory
        for actual, expected in [("long body", "PASS"), ("long", "FAIL")]:
            response = SimpleNamespace(status_code=200, elapsed_s=.1, reason_code="",
                                       transport_error_type="", payload={"messages": [{"role":"user", "content":actual}]})
            verifier = SimpleNamespace(get_history=lambda *_args: response)
            with patch("plugins.echomem_mcp.mcp_client.McpClient") as client:
                client.return_value.call_tool.return_value = "stored in session test"
                result = _mcp_add_memory({"mcp_base_url":"http://test"}, SimpleNamespace(auth_key="test"), "long body", verifier)
            self.assertEqual(result["status"], expected)
            self.assertNotIn("long body", json.dumps(result))

    def test_boundary_setup_failure_keeps_planned_cases_and_runs_mcp(self):
        from performance.targets.echomem.probes import payload_boundary as probe
        tenant = SimpleNamespace(auth_key="test", tenant_id="a", user_id="u", account_id="a", agent_id="g")
        ctx = SimpleNamespace(params={"sizes_bytes": [0, 1], "commit_content_chars": 1,
                                     "mcp_add_memory_chars": 1}, base_url="http://test", check=Mock())
        with patch.object(probe, "load_tenant_specs", return_value=[tenant]), \
             patch.object(probe, "EchoMemHTTP") as client, \
             patch.object(probe, "_mcp_add_memory", return_value={"status": "BLOCKED"}) as mcp:
            client.return_value.open_session.side_effect = TimeoutError()
            probe.run(ctx)
        detail = json.loads(ctx.check.call_args.kwargs["detail"])
        self.assertEqual(detail["cases_total"], 12)
        self.assertEqual(detail["cases_dispatched"], 0)
        self.assertEqual(detail["long_commit"]["accepted_chars"], 0)
        mcp.assert_called_once()

    def test_explicit_auto_commit_threshold_does_not_mutate_source(self):
        from performance.targets.echomem.prepare_concurrency_configs import configure
        source = {"model": {"embedding": {"model": "qwen3.7-text-embedding-flash"}},
                  "session": {"auto_commit_threshold": 1000}}
        result = configure(source, 16, auto_commit_threshold=4194304)
        self.assertEqual(result["session"]["auto_commit_threshold"], 4194304)
        self.assertEqual(source["session"]["auto_commit_threshold"], 1000)
        self.assertEqual(configure(source, 16)["session"]["auto_commit_threshold"], 1000)
        with self.assertRaises(ValueError):
            configure(source, 16, auto_commit_threshold=0)

    def test_scope_and_strict_results_are_rendered(self):
        report = render_objective_suite_html({
            "title": "Concurrency <audit>", "scope": "Only topology and boundary",
            "profiles": [{"name": "16", "concurrency_topology": {
                "status": "INCONCLUSIVE", "checks": [{"detail": {"matrix": [{
                    "level": 16, "topology": "one-session", "p95_ms": 31,
                    "operations": {"commit": {"offered": 4, "commit_completed": 2,
                        "commit_completion_throughput_jain": 0.8,
                        "boundary_reasons": {"timeout": 2}}},
                }]}}]}}],
        })
        self.assertIn("Concurrency &lt;audit&gt;", report)
        self.assertIn("Only topology and boundary", report)
        self.assertIn("timeout", report)
        self.assertIn("<td>0.8</td>", report)
        self.assertNotIn("<h2>内存泄漏诊断</h2>", report)


if __name__ == "__main__":
    unittest.main()
