import csv
import json
import tempfile
import unittest
from pathlib import Path

from stress.echomem.formal_data_report import render


class FormalDataReportTests(unittest.TestCase):
    def test_report_keeps_numeric_detail_and_missing_server_evidence_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = root / "mixed" / "repeat-01" / "fifo"
            run_dir.mkdir(parents=True)
            summary = {
                "status": "INCONCLUSIVE",
                "base_url": "http://127.0.0.1:8010",
                "parameters": {
                    "commit_delay_threshold_s": 10,
                    "search_delay_threshold_s": 2.5,
                },
                "details": {"identity_mode": "independent_auth_keys"},
            }
            (run_dir / "summary.json").write_text(
                json.dumps(summary), encoding="utf-8"
            )
            with (run_dir / "commit_results.csv").open("w", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "tenant",
                        "session_id",
                        "status",
                        "end_to_end_s",
                        "queue_wait_s",
                        "admission_wait_s",
                        "admission_queue_depth",
                        "request_id",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "tenant": "tenant-a",
                        "session_id": "s1",
                        "status": "completed",
                        "end_to_end_s": "12.5",
                        "queue_wait_s": "3.0",
                        "admission_wait_s": "2.0",
                        "admission_queue_depth": "4",
                        "request_id": "req-1",
                    }
                )
            with (run_dir / "search_results.csv").open("w", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "tenant",
                        "session_id",
                        "status_code",
                        "service_s",
                        "queue_wait_s",
                        "request_id",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "tenant": "tenant-a",
                        "session_id": "s1",
                        "status_code": "200",
                        "service_s": "0.8",
                        "queue_wait_s": "0.1",
                        "request_id": "req-2",
                    }
                )
            manifest = {
                "created_at": "2026-08-26T00:00:00Z",
                "base_url": "http://127.0.0.1:8010",
                "output_root": str(root),
                "repeats": 1,
                "server_observation_mode": True,
                "runs": [
                    {
                        "scenario": "mixed",
                        "scenario_label": "均衡混合负载",
                        "repetition": 1,
                        "policy": "fifo",
                        "status": "INCONCLUSIVE",
                        "output_dir": str(run_dir),
                        "summary": summary,
                    }
                ],
            }
            suite_path = root / "suite.json"
            suite_path.write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            output = root / "suite.html"
            render(suite_path, output)
            document = output.read_text(encoding="utf-8")
            self.assertIn("12.500s", document)
            self.assertIn("延迟事件", document)
            self.assertIn("服务端时序覆盖", document)
            self.assertIn("服务端排队", document)
            self.assertIn("服务端观察模式", document)
            self.assertIn("req-1", document)


if __name__ == "__main__":
    unittest.main()
