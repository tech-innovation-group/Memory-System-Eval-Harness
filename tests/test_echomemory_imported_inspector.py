from __future__ import annotations

import json
from pathlib import Path

from memory.plugins.echomemory.inspector import list_imported_memories


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_list_imported_memories_prefers_latest_summary_per_sample(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    account = "acct"
    output_dir = tmp_path / "runs"

    older = output_dir / "echomemory_import_older" / "echomemory_import" / "echomemory_import_summary.json"
    newer = output_dir / "echomemory_import_newer" / "echomemory_import" / "echomemory_import_summary.json"

    summary_base = {
        "workspace": str(workspace),
        "account": account,
        "records": [
            {
                "sample_id": "conv-30",
                "session_id": "echomem-locomo-conv-30-s1-old",
                "expected_messages": 28,
                "submitted_messages": 28,
                "integrity": "complete",
            }
        ],
    }
    _write_json(older, summary_base)
    _write_json(
        newer,
        {
            **summary_base,
            "status": "ECHOMEMORY_IMPORT_ASYNC_SETTLING",
            "records": [
                {
                    "sample_id": "conv-30",
                    "session_id": "echomem-locomo-conv-30-s1-new",
                    "expected_messages": 28,
                    "submitted_messages": 28,
                    "integrity": "incomplete",
                    "integrity_stage": "async_memory_pending",
                }
            ],
        },
    )

    imported = list_imported_memories(workspace, account, output_dir, sample="conv-30")

    assert len(imported["summaries"]) == 1
    assert imported["summaries"][0]["session_id"] == "echomem-locomo-conv-30-s1-new"
    assert imported["summaries"][0]["integrity"] == "incomplete"
    assert imported["summaries"][0]["integrity_stage"] == "async_memory_pending"


def test_list_imported_memories_reads_echomem_develop_session_layout(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    account = "acct"
    session_dir = workspace / "tenants" / account / "sessions" / "echomem-locomo-conv-30-s1-1234abcd"

    _write_json(
        session_dir / "current" / "session.json",
        {
            "metadata": {"title": "conv-30/session_1"},
            "scope": {"session_id": "echomem-locomo-conv-30-s1-1234abcd"},
            "status": "open",
        },
    )
    (session_dir / "current" / "messages.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"message_id": "D1:1", "content": "Hello"}),
                json.dumps({"message_id": "D1:2", "content": "Bye"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (session_dir / "history" / "archive_001" / "messages.jsonl").parent.mkdir(parents=True, exist_ok=True)
    (session_dir / "history" / "archive_001" / "messages.jsonl").write_text(
        json.dumps({"message_id": "D1:1", "content": "Hello"}) + "\n",
        encoding="utf-8",
    )

    imported = list_imported_memories(workspace, account, tmp_path / "runs", sample="conv-30")

    assert len(imported["sessions"]) == 1
    session = imported["sessions"][0]
    assert session["session_key"] == "conv-30/session_1"
    assert session["history_files"] == 1
    assert session["stored_messages"] == 1
    assert session["history_path"].endswith("/history/archive_001/messages.jsonl")
    assert session["meta_path"].endswith("/current/session.json")


def test_list_imported_memories_ignores_current_only_sessions_without_archive(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    account = "acct"
    session_dir = workspace / "tenants" / account / "sessions" / "echomem-locomo-conv-30-s1-1234abcd"

    _write_json(
        session_dir / "current" / "session.json",
        {
            "metadata": {"title": "conv-30/session_1"},
            "scope": {"session_id": "echomem-locomo-conv-30-s1-1234abcd"},
            "status": "open",
        },
    )
    (session_dir / "current" / "messages.jsonl").write_text(
        json.dumps({"message_id": "D1:1", "content": "Hello"}) + "\n",
        encoding="utf-8",
    )

    imported = list_imported_memories(workspace, account, tmp_path / "runs", sample="conv-30")

    assert imported["sessions"] == []
