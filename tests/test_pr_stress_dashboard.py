from __future__ import annotations

import unittest
from datetime import datetime, timezone

from scripts import pr_stress_dashboard as dashboard


class StressDashboardTests(unittest.TestCase):
    def test_marks_running_job_stalled_after_fifteen_minutes(self) -> None:
        now = datetime(2026, 8, 28, 2, 30, tzinfo=timezone.utc)
        job = {
            "status": "running",
            "started_at": "2026-08-28T02:00:00+00:00",
            "progress": {
                "updated_at": "2026-08-28T02:14:00+00:00",
                "phase": "qa",
                "label": "正式多租户矩阵压测",
                "percent": 47,
            },
        }

        age_s = dashboard.progress_age_seconds(job, now)

        self.assertEqual(960.0, age_s)
        self.assertEqual("运行中 · 疑似停滞", dashboard.status_label(job, age_s))

    def test_keeps_recent_running_job_as_running(self) -> None:
        now = datetime(2026, 8, 28, 2, 30, tzinfo=timezone.utc)
        job = {
            "status": "running",
            "started_at": "2026-08-28T02:00:00+00:00",
            "progress": {"updated_at": "2026-08-28T02:20:01+00:00"},
        }

        age_s = dashboard.progress_age_seconds(job, now)

        self.assertEqual("running", dashboard.status_label(job, age_s))


if __name__ == "__main__":
    unittest.main()
