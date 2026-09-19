import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from scripts.build_stress_artifacts import build, derive_anomalies


def write_csv(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = list(rows[0]) if rows else []
    path.write_text(",".join(headers) + "\n" + "\n".join(",".join(str(row[h]) for h in headers) for row in rows) + "\n")


class StressArtifactsTests(unittest.TestCase):
    def test_detects_multi_tenant_empty_recall_and_pending_commit(self):
        facts = {
            "search": [
                {"scenario": "m3-baseline", "planned_or_recorded": 100, "empty_recall": 5, "empty_recall_rate_pct": 5.0, "transport_or_http_errors": 0},
                {"scenario": "m2-fairness-8t", "planned_or_recorded": 100, "empty_recall": 20, "empty_recall_rate_pct": 20.0, "transport_or_http_errors": 0},
            ],
            "commit": [{"scenario": "m3-flood-single-tenant", "rejected": 0, "unresolved_or_timeout": 3}],
            "quality_evidence": {"semantic_quality_fields_present": True},
        }
        ids = {row["id"] for row in derive_anomalies(facts)}
        self.assertIn("multi_tenant_empty_recall_regression", ids)
        self.assertIn("commit_unresolved", ids)

    def test_builds_report_dossier_and_safe_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "summary.json").write_text(json.dumps({"status": "PARTIAL"}))
            (root / "execution-manifest.json").write_text(json.dumps({"execution_status": "PARTIAL"}))
            (root / "report.html").write_text("<html><body><main>old</main></body></html>")
            for scenario in ("m3-baseline", "m2-fairness-8t"):
                (root / scenario).mkdir()
                write_csv(root / scenario / "search_results.csv", [{"status_code": "200", "hit_count": "1"}])
                write_csv(root / scenario / "commit_results.csv", [{"status": "completed"}])
            build(root)
            self.assertTrue((root / "anomaly-dossier.json").is_file())
            self.assertIn("异常诊断与开发者分析", (root / "report.html").read_text())
            with tarfile.open(root / "developer-bundle.tar.gz", "r:gz") as archive:
                names = set(archive.getnames())
            self.assertIn("summary.json", names)
            self.assertNotIn("tenant.env", names)


if __name__ == "__main__":
    unittest.main()
