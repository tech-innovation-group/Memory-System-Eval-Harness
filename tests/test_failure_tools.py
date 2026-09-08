from __future__ import annotations

import csv
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from performance.targets.echomem.acceptance.scheduler import (
    evaluate as evaluate_scheduler_acceptance,
)
from performance.ctx import Ctx, ConnectionRegistry
from performance.targets.echomem.probes._client import extract_message
from performance.targets.echomem.probes._client import EchoMemHTTP
from performance.targets.echomem.probes.blackbox_contract import (
    request as blackbox_request,
)
from performance.targets.echomem.probes.capability import (
    classify_probe,
    request as capability_request,
    run as run_capability,
)
from performance.targets.echomem.probes.commit_recovery import (
    decode_fs_read_payload,
    recovery_control_ok,
)
from performance.targets.echomem.probes.cursor_reconcile import (
    run as run_reconcile,
    values_from_payload,
)
from performance.targets.echomem.probes.fault_injection import run_control


def _probe_ctx(base_url: str, **params):
    checks = []
    ctx = Ctx(
        scene="failure_tools",
        worker_id=0,
        tenant_idx=0,
        headers={},
        base_url=base_url,
        read_timeout_s=5,
        params=params,
        duration_s=0,
        stop=threading.Event(),
        record_fn=lambda r: None,
        seq_fn=lambda: 0,
        choose_fn=lambda items: None,
        phases=[],
        checks=checks,
        registry=ConnectionRegistry(),
    )
    return ctx, checks


class FailureToolTests(unittest.TestCase):
    def test_recovery_requires_successful_kill_and_restart(self) -> None:
        self.assertFalse(
            recovery_control_ok({"kill_returncode": 1, "start_returncode": 0})
        )
        self.assertFalse(
            recovery_control_ok({"kill_returncode": 0, "start_returncode": 1})
        )
        self.assertTrue(
            recovery_control_ok({"kill_returncode": 0, "start_returncode": 0})
        )

    def test_metrics_probe_preserves_full_prometheus_payload(self) -> None:
        prefix = "# HELP old_metric " + ("x" * 5000)
        raw = prefix + "\nechomem_lane_queued 1\n"

        class Response:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return raw.encode()

        with patch("urllib.request.urlopen", return_value=Response()):
            capability = capability_request(
                "http://example.test", "/metrics", timeout_s=1, preserve_raw=True
            )
            blackbox = blackbox_request(
                "http://example.test", "/metrics", auth_key="",
                auth_header="X-Auth-Key", timeout_s=1, preserve_raw=True
            )

        self.assertIn("echomem_lane_queued", capability["payload"]["raw"])
        self.assertIn("echomem_lane_queued", blackbox["payload"]["raw"])

    def test_capability_probe_classifies_404_as_not_implemented_and_unconfigured_as_inconclusive(self) -> None:
        self.assertEqual(
            "NOT_IMPLEMENTED",
            classify_probe("optional", {"status_code": 404, "payload": {}})["status"],
        )
        ctx, checks = _probe_ctx(
            "http://127.0.0.1:1",
            auth_key="",
            auth_key_env="MISSING_KEY",
            auth_header="X-API-Key",
            health_path="/health",
            metrics_path="/metrics",
            timeout_s=0.01,
        )
        run_capability(ctx)
        self.assertTrue(checks)
        self.assertTrue(all(check.status == "INCONCLUSIVE" for check in checks))
        self.assertGreaterEqual(
            sum(1 for check in checks if check.status == "INCONCLUSIVE"), 6
        )

    def test_cursor_payload_extracts_nested_operation_and_archive(self) -> None:
        messages, archives, operations = values_from_payload({
            "result": {
                "message_set": {
                    "items": [{"message_id": "m1", "archive_id": "a1", "operation_id": "o1"}]
                }
            }
        })
        self.assertEqual({"m1"}, messages)
        self.assertEqual({"a1"}, archives)
        self.assertEqual({"o1"}, operations)

    def test_cursor_payload_extracts_committed_message_ids(self) -> None:
        messages, archives, operations = values_from_payload({
            "committed_message_ids": ["msg_001", "msg_002"],
        })
        self.assertEqual({"msg_001", "msg_002"}, messages)
        self.assertEqual(set(), archives)
        self.assertEqual(set(), operations)

    def test_extract_message_uses_server_assigned_id(self) -> None:
        self.assertEqual(
            {
                "id": "msg_001",
                "role": "user",
                "content": "hello",
                "metadata": {"stress_message_id": "recovery-001"},
            },
            extract_message({
                "message": {
                    "id": "msg_001",
                    "role": "user",
                    "content": "hello",
                    "metadata": {"stress_message_id": "recovery-001"},
                }
            }),
        )

    def test_extract_message_does_not_treat_client_metadata_as_persisted_id(self) -> None:
        self.assertEqual(
            {},
            extract_message({
                "status": "ok",
                "metadata": {"stress_message_id": "recovery-001"},
            }),
        )

    def test_recovery_probe_unwraps_fs_read_cursor_document(self) -> None:
        self.assertEqual(
            {"committed_message_ids": ["msg_001"]},
            decode_fs_read_payload({
                "result": {
                    "text": '{"committed_message_ids": ["msg_001"]}',
                }
            }),
        )

    def test_scheduler_fairness_does_not_mix_different_workloads(self) -> None:
        def run(scenario: str, counts: dict[str, int]) -> dict[str, object]:
            return {
                "scenario": scenario,
                "status": "completed",
                "summary": {
                    "metrics": {
                        "fairness": {
                            "commit_completed_per_tenant": counts,
                        },
                        "per_tenant": {
                            tenant: {
                                "commit": {"completed": count},
                                "search": {"latency": {"p95_s": 1.0}},
                            }
                            for tenant, count in counts.items()
                        },
                    }
                },
            }

        result = evaluate_scheduler_acceptance(
            {
                "instance_profile": "4U8G",
                "runs": [
                    run("capacity-2", {"0": 2, "1": 2}),
                    run("capacity-4", {"0": 3, "1": 2, "2": 1, "3": 2}),
                    run("fairness-bounded", {"0": 4, "1": 4, "2": 4, "3": 4}),
                ],
            }
        )
        fairness = next(
            item for item in result["checks"]
            if item["name"] == "Commit/Search 公平性 Jain"
        )
        self.assertEqual("PASS", fairness["status"])
        self.assertEqual("fairness-bounded", fairness["observed"]["scenario"])
        self.assertEqual(1.0, fairness["observed"]["jain"])

    def test_commit_client_includes_idempotency_key(self) -> None:
        client = EchoMemHTTP("http://example.test")
        with patch.object(client, "request") as request:
            client.commit("session-1", idempotency_key="commit-key-1")
        request.assert_called_once()
        args, kwargs = request.call_args
        self.assertEqual("POST", args[0])
        self.assertEqual("/api/sessions/session-1/commit", args[1])
        self.assertEqual("commit-key-1", args[2]["idempotency_key"])

    def test_fault_control_without_real_control_is_inconclusive(self) -> None:
        result = run_control({
            "command": "",
            "endpoint": "",
            "container": "",
            "action": "",
            "signal": "KILL",
            "timeout_s": 1,
        })
        self.assertEqual("INCONCLUSIVE", result["status"])

    def test_cursor_reconcile_compares_message_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commits = root / "commits.csv"
            with commits.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["status", "session_id", "archive_id", "message_ids"])
                writer.writeheader()
                writer.writerow({
                    "status": "completed",
                    "session_id": "s1",
                    "archive_id": "a1",
                    "message_ids": json.dumps(["m1", "m2"]),
                })

            ctx, checks = _probe_ctx(
                "",
                commit_csv=str(commits),
                cursor_uri_template="echo://sessions/{session}/current/commit_cursor.json",
                auth_key="",
                auth_header="X-API-Key",
                timeout_s=1,
            )
            run_reconcile(ctx)
            self.assertEqual(["reconcile"], [check.name for check in checks])
            self.assertEqual("INCONCLUSIVE", checks[0].status)


if __name__ == "__main__":
    unittest.main()
