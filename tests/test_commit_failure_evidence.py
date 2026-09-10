import json
import unittest
from types import SimpleNamespace

from performance.targets.echomem.probes.failure_evidence import failure_evidence
from performance.targets.echomem.probes.concurrency_topology import _commit_call, _summary
from scripts.collect_commit_diagnostics import collect
from performance.targets.echomem.orchestrator.report import _probe_visual


class EvidenceTests(unittest.TestCase):
    def test_provider_code_without_free_text_or_key(self):
        payload = {"error": "Error code: 429 - {'code': 'InsufficientFreeQuota', 'message': 'sk-secret PROMPT'}", "stage": "extraction"}
        result = failure_evidence(payload)
        self.assertIn("PROVIDER_FREE_QUOTA_EXHAUSTED", result["categories"])
        self.assertEqual(result["provider_codes"], ["InsufficientFreeQuota"])
        self.assertEqual(result["upstream_http_statuses"], [429])
        self.assertNotIn("sk-secret", json.dumps(result))
        self.assertNotIn("PROMPT", json.dumps(result))
        self.assertEqual(failure_evidence({"error": "req_429401abcdef"})["upstream_http_statuses"], [])

    def test_commit_terminal_evidence_and_dedup(self):
        class Client:
            def add_message(self, *args):
                return SimpleNamespace(status_code=200, reason_code="", transport_error_type="")
            def commit(self, *args, **kwargs):
                return SimpleNamespace(status_code=202, payload={"archive_id": "a", "commit_id": "c"}, reason_code="", transport_error_type="")
            def commit_status(self, *args):
                return SimpleNamespace(status_code=200, payload={"status": "failed", "error": "insufficient quota", "stage": "extraction"})
        row = _commit_call(Client(), "tenant", "session", "text", 1)()
        self.assertEqual(row["terminal_state"], "failed")
        self.assertEqual(row["terminal_evidence"]["categories"], ["PROVIDER_QUOTA"])
        result = _summary([row, row], 1)
        self.assertEqual(result["commit_failed"], 2)
        self.assertEqual(result["commit_unique_failed_archives"], 1)
        self.assertEqual(result["commit_repeated_archive_observations"], 1)

    def test_log_export_is_whitelisted(self):
        result = collect([json.dumps({"event": "commit_failed", "level": "ERROR", "error": "insufficient balance sk-secret", "content": "PRIVATE_PROMPT"})])
        self.assertEqual(result["events"]["commit_failed"], 1)
        self.assertNotIn("sk-secret", json.dumps(result))
        self.assertNotIn("PRIVATE_PROMPT", json.dumps(result))

    def test_report_labels_log_evidence_separately(self):
        rendered = _probe_visual("commit_diagnostic", {"checks": [{"detail": {"rows": [{
            "terminal_state": "failed", "terminal_evidence": {"error_present": False},
            "service_failure_evidence": {"error_type": "ExtractorError"},
        }]}}]})
        self.assertIn("ExtractorError", rendered)
        self.assertIn("服务日志（任务标识匹配）", rendered)


if __name__ == "__main__":
    unittest.main()
