from __future__ import annotations

import json
from pathlib import Path

from memory.runs import run_record


def test_run_record_marks_stale_manifest_queued_as_canceled(tmp_path: Path) -> None:
    run_dir = tmp_path / "openviking_import_20260530_180308_fc832d"
    run_dir.mkdir()
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": "openviking_import_20260530_180308_fc832d",
                "kind": "openviking_import",
                "status": "queued",
                "name": "locomo openviking commit import",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    record = run_record(run_dir, active_run_ids=set(), compact=True)

    assert record is not None
    assert record["status"] == "canceled"
    assert record["manifest_status"] == "queued"
    assert record["stale_active"] is True
    assert record["stale_running"] is False
    assert record["status_reason"] == "manifest_queued_without_active_task"


def test_run_record_marks_stale_manifest_running_as_interrupted(tmp_path: Path) -> None:
    run_dir = tmp_path / "echomemory_import_20260627_001159_default-20260626-223941_097af0"
    run_dir.mkdir()
    summary_dir = run_dir / "echomemory_import"
    summary_dir.mkdir()
    summary_path = summary_dir / "echomemory_import_summary.json"
    summary_path.write_text(
        json.dumps({"status": "ECHOMEMORY_IMPORT_RUNNING"}, ensure_ascii=False),
        encoding="utf-8",
    )
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "id": "echomemory_import_20260627_001159_default-20260626-223941_097af0",
                "kind": "echomemory_import",
                "status": "running",
                "name": "locomo echomemory import",
                "output_file": str(summary_path),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    record = run_record(run_dir, active_run_ids=set(), compact=True)

    assert record is not None
    assert record["status"] == "interrupted"
    assert record["manifest_status"] == "running"
    assert record["stale_active"] is True
    assert record["stale_running"] is True
    assert record["status_reason"] == "manifest_running_without_active_task"
