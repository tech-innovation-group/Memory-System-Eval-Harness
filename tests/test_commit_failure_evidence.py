import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from performance.targets.echomem.probes.failure_evidence import failure_evidence, reference
from performance.targets.echomem.probes.concurrency_topology import _commit_call, _summary
from scripts.collect_commit_diagnostics import collect
from performance.targets.echomem.orchestrator.report import _probe_visual
from scripts.run_commit_diagnostic import diagnose


class EvidenceTests(unittest.TestCase):
    def test_diagnostic_records_configured_length(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out / "diagnostic-options.json").write_text('{"commit_chars":4096}')
            with patch("scripts.run_commit_diagnostic.run_preflight", return_value={"ok":False}):
                diagnose(out, "http://unused.invalid")
            self.assertEqual(json.loads((out / "diagnostic.json").read_text())["commit_chars"], 4096)
            (out / "diagnostic-options.json").write_text('{"commit_chars":0}')
            self.assertRaises(ValueError, diagnose, out, "http://unused.invalid")

    def test_provider_code_without_free_text_or_key(self):
        payload = {"error": "Error code: 429 - {'code': 'InsufficientFreeQuota', 'message': 'sk-secret PROMPT'}", "stage": "extraction"}
        result = failure_evidence(payload)
        self.assertIn("PROVIDER_FREE_QUOTA_EXHAUSTED", result["categories"])
        self.assertEqual(result["provider_codes"], ["InsufficientFreeQuota"])
        self.assertEqual(result["upstream_http_statuses"], [429])
        self.assertNotIn("sk-secret", json.dumps(result))
        self.assertNotIn("PROMPT", json.dumps(result))
        self.assertEqual(failure_evidence({"error": "req_429401abcdef"})["upstream_http_statuses"], [])

    def test_nested_session_status_preserves_error_and_trace(self):
        result = failure_evidence({"status": {"status": "failed",
            "error": "insufficient_quota sk-secret", "error_type": "ExtractorError",
            "stage": "engine_dispatch", "trace_id": "private-trace"}})
        self.assertTrue(result["error_present"])
        self.assertEqual(result["categories"], ["PROVIDER_QUOTA"])
        self.assertEqual(result["error_type"], "ExtractorError")
        self.assertEqual(result["trace_ref"], reference("private-trace"))
        self.assertNotIn("private-trace", json.dumps(result))
        self.assertNotIn("sk-secret", json.dumps(result))

    def test_commit_terminal_evidence_and_dedup(self):
        class Client:
            def add_message(self, *args):
                return SimpleNamespace(status_code=200, reason_code="", transport_error_type="")
            def commit(self, *args, **kwargs):
                return SimpleNamespace(status_code=202, payload={"archive_id": "a", "commit_id": "c"}, reason_code="", transport_error_type="")
            def commit_status(self, *args):
                return SimpleNamespace(status_code=200, payload={"status": {"status": "failed", "error": "insufficient quota", "stage": "extraction"}})
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

    def test_extraction_source_is_classified_without_exporting_message(self):
        result = collect([json.dumps({"event": "log_message", "level": "WARNING",
            "msg": "Atomic extraction LLM call failed: insufficient_quota sk-secret",
            "engine_id": "atomic_engine", "archive_id": "archive-private"})])
        row = result["samples"][0]
        self.assertEqual(row["message_class"], "atomic_extraction_llm")
        self.assertEqual(row["engine_id"], "atomic_engine")
        self.assertTrue(row["archive_id_ref"])
        self.assertNotIn("archive-private", json.dumps(result))
        self.assertNotIn("sk-secret", json.dumps(result))

    def test_report_labels_log_evidence_separately(self):
        rendered = _probe_visual("commit_diagnostic", {"checks": [{"detail": {"rows": [{
            "terminal_state": "failed", "terminal_evidence": {"error_present": False},
            "service_failure_evidence": {"error_type": "ExtractorError"},
        }]}}]})
        self.assertIn("ExtractorError", rendered)
        self.assertIn("服务日志（任务标识匹配）", rendered)


if __name__ == "__main__":
    unittest.main()
