from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from memory.tasking import task_progress


def test_echomemory_import_progress_does_not_treat_summary_session_index_as_completed_sessions(tmp_path: Path) -> None:
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
    assert progress["current"] == 0
    assert progress["total"] == 19
    assert progress["completed_sessions"] == 0
    assert "session_13" in progress["detail"]
    assert "254/254" in progress["detail"]


def test_echomemory_import_progress_finalizing_uses_authoritative_completed_count(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    log_path = run_dir / "run.log"
    summary_path = run_dir / "echomemory_import_summary.json"

    log_path.write_text(
        '[message] {"label":"conv-30/session_19","message_index":14,"message_total":14,"role":"user","content":"done"}\n',
        encoding="utf-8",
    )
    summary_path.write_text(
        json.dumps(
            {
                "status": "ECHOMEMORY_IMPORT_FINALIZING",
                "finalizing_sessions_done": 8,
                "finalizing_sessions_total": 19,
                "current_finalizing_session": "session_16",
                "expected_messages": 369,
                "submitted_messages": 369,
                "records": [
                    {
                        "sample_id": "conv-30",
                        "session_count": 19,
                        "original_session_count": 19,
                        "progress_sessions_done": 19,
                        "progress_sessions_total": 19,
                        "session_records": [
                            {"session_key": "session_19", "integrity": "pending_async_memory"},
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
        meta={
            "_session_progress_cache": {
                "current": 14,
                "total": 19,
                "phase": "commit:finalizing",
                "session_label": "conv-30/session_19",
            }
        },
        command=[],
    )
    task.config = {"dataset_format": "locomo", "sample": "conv-30"}

    progress = task_progress(task)

    assert progress is not None
    assert progress["phase"] == "commit:finalizing"
    assert progress["current"] == 8
    assert progress["completed_sessions"] == 8
    assert progress["submitted_sessions"] == 19
    assert progress["finalizing_sessions_done"] == 8
    assert progress["finalizing_sessions_total"] == 19
    assert progress["session_label"] == "session_16"


def test_echomemory_import_progress_done_reports_full_completion(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    log_path = run_dir / "run.log"
    summary_path = run_dir / "echomemory_import_summary.json"

    log_path.write_text(
        '[message] {"label":"conv-30/session_19","message_index":14,"message_total":14,"role":"user","content":"done"}\n',
        encoding="utf-8",
    )
    summary_path.write_text(
        json.dumps(
            {
                "status": "ECHOMEMORY_IMPORT_DONE",
                "expected_messages": 369,
                "submitted_messages": 369,
                "records": [
                    {
                        "sample_id": "conv-30",
                        "session_count": 19,
                        "original_session_count": 19,
                        "progress_sessions_done": 19,
                        "progress_sessions_total": 19,
                        "session_records": [
                            {"session_key": "session_19", "integrity": "pending_async_memory"},
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
        status="completed",
        started_at=1.0,
        created_at=1.0,
        meta={
            "_session_progress_cache": {
                "current": 18,
                "total": 19,
                "phase": "commit:finalizing",
                "session_label": "conv-30/session_19",
            }
        },
        command=[],
    )
    task.config = {"dataset_format": "locomo", "sample": "conv-30"}

    progress = task_progress(task)

    assert progress is not None
    assert progress["phase"] == "commit:done"
    assert progress["current"] == 19
    assert progress["total"] == 19
    assert progress["completed_sessions"] == 19


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
