from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from memory.tasking import task_progress


def test_echomemory_import_progress_prefers_summary_when_tail_log_loses_commit_lines(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    log_path = run_dir / "run.log"
    summary_path = run_dir / "echomemory_import_summary.json"

    filler = "[LLM] call_site=embed INFO latency=12.3ms tokens=10\n"
    log_path.write_text(filler * 5000, encoding="utf-8")
    summary_path.write_text(
        json.dumps(
            {
                "status": "ECHOMEMORY_IMPORT_RUNNING",
                "expected_messages": 254,
                "submitted_messages": 254,
                "records": [
                    {
                        "sample_id": "conv-30",
                        "session_count": 13,
                        "original_session_count": 19,
                        "progress_sessions_done": 13,
                        "progress_sessions_total": 19,
                        "session_records": [
                            {"session_key": "session_13", "integrity": "pending_async_memory"},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    task = SimpleNamespace(
        kind="echomemory_import",
        log_file=str(log_path),
        output_file=str(summary_path),
        status="running",
        started_at=1.0,
        created_at=1.0,
        meta={},
        command=[],
    )
    task.config = {"dataset_format": "locomo", "sample": "conv-30"}

    progress = task_progress(task)

    assert progress is not None
    assert progress["unit"] == "sessions"
    assert progress["current"] == 13
    assert progress["total"] == 19
    assert "session_13" in progress["detail"]


def test_locomo_qa_progress_exposes_current_question_preview_from_log(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    log_path = run_dir / "run.log"
    csv_path = run_dir / "openviking_memory_qa_results.csv"
    log_path.write_text("[qa] 3/81 conv-30_qa17 What did the user buy during the trip?\n", encoding="utf-8")
    csv_path.write_text("", encoding="utf-8")

    task = SimpleNamespace(
        kind="openviking_qa",
        log_file=str(log_path),
        output_file=str(csv_path),
        status="running",
        started_at=1.0,
        created_at=1.0,
        meta={"config": {"dataset_format": "locomo"}},
        command=[],
    )

    progress = task_progress(task)

    assert progress is not None
    assert progress["unit"] == "questions"
    assert progress["current"] == 3
    assert progress["total"] == 81
    assert progress["qa_preview"]["question_id"] == "conv-30_qa17"
    assert "What did the user buy" in progress["qa_preview"]["question"]
    assert progress["qa_preview"]["answer"] == ""


def test_locomo_qa_progress_uses_csv_answer_only_for_same_question(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    log_path = run_dir / "run.log"
    csv_path = run_dir / "openviking_memory_qa_results.csv"
    log_path.write_text("[qa] 3/81 conv-30_qa17 What did the user buy during the trip?\n", encoding="utf-8")
    csv_path.write_text(
        "question_id,question,response\n"
        "conv-30_qa17,What did the user buy during the trip?,A blue jacket.\n",
        encoding="utf-8",
    )

    task = SimpleNamespace(
        kind="openviking_qa",
        log_file=str(log_path),
        output_file=str(csv_path),
        status="running",
        started_at=1.0,
        created_at=1.0,
        meta={"config": {"dataset_format": "locomo"}},
        command=[],
    )

    progress = task_progress(task)

    assert progress is not None
    assert progress["qa_preview"]["question_id"] == "conv-30_qa17"
    assert progress["qa_preview"]["answer"] == "A blue jacket."
