from pathlib import Path

from memory.plugins.echomemory.inspector import current_session_snapshot, import_integrity, list_imported_memories


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_list_imported_memories_reports_unique_session_completion(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    output_dir = tmp_path / "runs"
    account = "acct"
    session_id = "echomem-locomo-conv-30-s1-aaaa"

    current_dir = workspace / "tenants" / account / "sessions" / session_id
    engine_dir = workspace / "tenants" / account / "engines" / "echo0_plugin" / "sessions" / session_id
    _write(
        current_dir / "current" / "session.json",
        '{"metadata":{"title":"conv-30/session_1"},"scope":{"session_id":"%s"}}' % session_id,
    )
    _write(
        current_dir / "history" / "archive_001" / "messages.jsonl",
        '{"message_id":"m1","content":"hello"}\n{"message_id":"m2","content":"world"}\n',
    )
    _write(engine_dir / "abstract.md", "abstract")
    _write(engine_dir / "overview.md", "overview")
    _write(
        workspace / "tenants" / account / "engines" / "echo0_plugin" / "commits" / f"{session_id}__archive_001.status.json",
        '{"status":"completed"}',
    )
    _write(
        output_dir / "echomemory_import_test" / "echomemory_import" / "echomemory_import_summary.json",
        (
            '{"workspace":"%s","account":"%s","status":"ECHOMEMORY_IMPORT_DONE","records":'
            '[{"sample_id":"conv-30","session_id":"%s","integrity":"complete","submitted_messages":2,"expected_messages":2}]}'  # noqa: E501
            % (workspace, account, session_id)
        ),
    )

    imported = list_imported_memories(workspace, account, output_dir, sample="conv-30")

    assert imported["session_total_count"] == 1
    assert imported["session_complete_count"] == 1
    assert imported["conv_complete"] is True
    assert len(imported["sessions"]) == 1
    assert imported["sessions"][0]["session_complete"] is True


def test_import_integrity_exposes_conv_completion_markers(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    output_dir = tmp_path / "runs"
    dataset = tmp_path / "locomo10.json"
    dataset.write_text("[]", encoding="utf-8")
    account = "acct"
    session_id = "echomem-locomo-conv-30-s1-aaaa"

    current_dir = workspace / "tenants" / account / "sessions" / session_id
    engine_dir = workspace / "tenants" / account / "engines" / "echo0_plugin" / "sessions" / session_id
    _write(
        current_dir / "current" / "session.json",
        '{"metadata":{"title":"conv-30/session_1"},"scope":{"session_id":"%s"}}' % session_id,
    )
    _write(
        current_dir / "history" / "archive_001" / "messages.jsonl",
        '{"message_id":"m1","content":"hello"}\n',
    )
    _write(engine_dir / "abstract.md", "abstract")
    _write(engine_dir / "overview.md", "overview")
    _write(
        workspace / "tenants" / account / "engines" / "echo0_plugin" / "commits" / f"{session_id}__archive_001.status.json",
        '{"status":"completed"}',
    )
    summary_path = output_dir / "echomemory_import_test" / "echomemory_import" / "echomemory_import_summary.json"
    _write(
        summary_path,
        (
            '{"workspace":"%s","account":"%s","status":"ECHOMEMORY_IMPORT_DONE","records":'
            '[{"sample_id":"conv-30","session_id":"%s","integrity":"complete","submitted_messages":1,"expected_messages":1}]}'  # noqa: E501
            % (workspace, account, session_id)
        ),
    )

    integrity = import_integrity(workspace, account, output_dir, dataset, sample="conv-30", summary_path=summary_path)
    snapshot_rows, _ = current_session_snapshot(workspace, account, "conv-30")

    assert len(snapshot_rows) == 1
    assert snapshot_rows[0]["session_complete"] is True
    assert integrity["session_complete_count"] == 1
    assert integrity["session_total_count"] == 1
    assert integrity["conv_complete"] is True
    assert any(check["name"] == "Conv 完成标志" and check["ok"] for check in integrity["checks"])
