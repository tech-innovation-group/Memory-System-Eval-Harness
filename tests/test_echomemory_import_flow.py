from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from scripts.echomemory_common import workspace_token_usage_summary
from scripts import echomemory_locomo_import as import_mod


def test_workspace_token_usage_summary_reads_engine_scoped_logs(tmp_path: Path) -> None:
    token_dir = (
        tmp_path
        / "tenants"
        / "acct"
        / "engines"
        / "echo0_plugin"
        / "metrics"
        / "llm_tokens"
    )
    token_dir.mkdir(parents=True)
    rows = [
        {
            "timestamp": "2026-06-23T15:17:39.929047+00:00",
            "call_site": "atom_extraction",
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "latency_ms": 123.4,
        },
        {
            "timestamp": "2026-06-23T15:18:45.888932+00:00",
            "call_site": "overview_generation",
            "input_tokens": 7,
            "output_tokens": 3,
            "total_tokens": 10,
            "latency_ms": 44.0,
        },
    ]
    (token_dir / "2026-06-23.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
        encoding="utf-8",
    )

    summary = workspace_token_usage_summary(tmp_path, "acct")

    assert summary["llm_log_rows"] == 2
    assert summary["llm_input_tokens"] == 17
    assert summary["llm_output_tokens"] == 8
    assert summary["llm_total_tokens"] == 25
    assert summary["llm_call_count"] == 2
    assert summary["call_sites"]["atom_extraction"]["call_count"] == 1
    assert summary["call_sites"]["overview_generation"]["call_count"] == 1


def test_count_memory_artifacts_uses_short_ttl_cache(monkeypatch) -> None:
    calls = {"count": 0}
    timeline = {"now": 100.0}

    def fake_uncached(workspace: str, account: str) -> dict[str, object]:
        calls["count"] += 1
        return {"atoms_count": calls["count"], "memory_root": f"{workspace}:{account}"}

    monkeypatch.setattr(import_mod, "_count_memory_artifacts_uncached", fake_uncached)
    monkeypatch.setattr(import_mod.time, "monotonic", lambda: timeline["now"])
    import_mod._MEMORY_ARTIFACT_CACHE.clear()

    first = import_mod.count_memory_artifacts("/tmp/ws", "acct")
    second = import_mod.count_memory_artifacts("/tmp/ws", "acct")
    timeline["now"] += import_mod.MEMORY_ARTIFACT_CACHE_TTL_S + 0.1
    third = import_mod.count_memory_artifacts("/tmp/ws", "acct")

    assert first["atoms_count"] == 1
    assert second["atoms_count"] == 1
    assert third["atoms_count"] == 2
    assert calls["count"] == 2


def test_cached_workspace_token_usage_summary_can_force_refresh(monkeypatch) -> None:
    calls = {"count": 0}
    timeline = {"now": 100.0}

    def fake_summary(workspace: str, account: str) -> dict[str, object]:
        calls["count"] += 1
        return {"llm_log_rows": calls["count"], "call_sites": {}}

    monkeypatch.setattr(import_mod, "workspace_token_usage_summary", fake_summary)
    monkeypatch.setattr(import_mod.time, "monotonic", lambda: timeline["now"])
    import_mod._WORKSPACE_TOKEN_USAGE_CACHE.clear()

    first = import_mod.cached_workspace_token_usage_summary("/tmp/ws", "acct")
    second = import_mod.cached_workspace_token_usage_summary("/tmp/ws", "acct")
    forced = import_mod.cached_workspace_token_usage_summary("/tmp/ws", "acct", force_refresh=True)

    assert first["llm_log_rows"] == 1
    assert second["llm_log_rows"] == 1
    assert forced["llm_log_rows"] == 2
    assert calls["count"] == 2


def test_finalize_force_wait_stall_seconds_prefers_shorter_staged_full_window(monkeypatch) -> None:
    monkeypatch.delenv("ECHOMEM_FINALIZE_FORCE_WAIT_STALL_S", raising=False)
    args = SimpleNamespace(
        import_wait_mode="full",
        defer_artifact_wait=False,
        skip_session_commit=False,
        flush_call_timeout_s=20,
        commit_wait_s=12,
    )

    stall_s = import_mod.finalize_force_wait_stall_seconds(args)

    assert stall_s == 12.0


def test_collect_commit_artifact_state_skips_memory_scan_until_overview_ready(
    tmp_path: Path,
    monkeypatch,
) -> None:
    session_dir = tmp_path / "tenants" / "acct" / "sessions" / "s1"
    session_dir.mkdir(parents=True)
    (session_dir / "messages.jsonl").write_text('{"message_id":"m0"}\n', encoding="utf-8")
    (session_dir / "meta.json").write_text(json.dumps({"commit_index": 0, "atom_pipeline_index": 0}), encoding="utf-8")

    monkeypatch.setattr(import_mod, "find_session_dir", lambda workspace, account, session_id: session_dir)
    monkeypatch.setattr(import_mod, "find_engine_session_dir", lambda workspace, account, session_id: None)

    calls = {"count": 0}

    def fake_count_memory_artifacts(workspace: str, account: str) -> dict[str, object]:
        calls["count"] += 1
        return {"atoms_count": 1, "vector_items": 1, "vector_index_exists": True}

    monkeypatch.setattr(import_mod, "count_memory_artifacts", fake_count_memory_artifacts)
    args = SimpleNamespace(workspace=str(tmp_path), account="acct", skip_session_commit=False)
    monkeypatch.setattr(import_mod, "abstract_required", lambda _args: True)

    state = import_mod.collect_commit_artifact_state(
        args,
        "s1",
        expected_message_count=1,
        expected_last_message_id="m0",
    )

    assert calls["count"] == 0
    assert state["overview_nonempty"] is False
    assert state["retrieval_ready"] is False
    assert state["complete"] is False


def test_recovered_integrity_keeps_active_extracting_commit_pending_async() -> None:
    integrity = import_mod.recovered_integrity_from_artifacts(
        archive_complete=True,
        atom_memory_complete=False,
        retrieval_ready=False,
        cursor_complete=False,
        session_complete=False,
        fast_import=False,
        commit_status={"status": "running", "stage": "extracting"},
    )

    assert integrity == "pending_async_memory"


def test_finalize_import_records_reprobes_pending_sessions_before_second_reingest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    session_dirs = {}
    for session_id in ("s1", "s2"):
        path = tmp_path / session_id
        path.mkdir()
        session_dirs[session_id] = path

    states = {
        "s1": {"complete": False},
        "s2": {"complete": False},
    }
    flush_calls: list[str] = []

    def fake_find_session_dir(workspace: str, account: str, session_id: str) -> Path | None:
        return session_dirs.get(session_id)

    def fake_session_state_for_repair(session_dir: Path) -> dict[str, object]:
        return {
            "session_id": session_dir.name,
            "session_dir": str(session_dir),
            "title": session_dir.name,
            "message_count": 1,
            "last_message_id": "m0",
            "commit_index": 0,
            "atom_pipeline_index": 0 if states[session_dir.name]["complete"] else -1,
            "expected_index": 0,
            "complete": states[session_dir.name]["complete"],
        }

    def fake_collect_commit_artifact_state(
        args: object,
        session_id: str,
        *,
        expected_message_count: int = 0,
        expected_last_message_id: str = "",
    ) -> dict[str, object]:
        complete = bool(states[session_id]["complete"])
        return {
            "commit_index": 0,
            "expected_commit_index": 0,
            "commit_index_ok": True,
            "atom_pipeline_index": 0 if complete else -1,
            "expected_atom_pipeline_index": 0,
            "atom_pipeline_index_ok": complete,
            "legacy_commit_complete": complete,
            "retrieval_ready": complete,
            "cursor_complete": complete,
            "complete": complete,
        }

    async def fake_flush_atom_pipeline(
        args: object,
        sdk: object,
        session_id: str,
        *,
        expected_message_count: int,
        expected_last_message_id: str,
    ) -> dict[str, object]:
        flush_calls.append(session_id)
        states[session_id]["complete"] = True
        states["s2"]["complete"] = True
        return {"complete": True}

    async def fake_wait_for_commit_artifacts(
        args: object,
        session_id: str,
        *,
        expected_message_count: int = 0,
        expected_last_message_id: str = "",
        label: str = "",
    ) -> dict[str, object]:
        return fake_collect_commit_artifact_state(
            args,
            session_id,
            expected_message_count=expected_message_count,
            expected_last_message_id=expected_last_message_id,
        )

    def fake_summarize_sample_progress(
        args: object,
        sample_index: int,
        sample_id: str,
        original_session_count: int,
        session_batches: list[object],
        estimated_import_tokens: int,
        records: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "sample_index": sample_index,
            "sample_id": sample_id,
            "session_records": records,
            "progress_sessions_done": sum(1 for item in records if item.get("qa_ready_after_commit")),
            "progress_sessions_total": original_session_count,
            "original_session_count": original_session_count,
            "estimated_import_tokens": estimated_import_tokens,
        }

    monkeypatch.setattr(import_mod, "find_session_dir", fake_find_session_dir)
    monkeypatch.setattr(import_mod, "session_state_for_repair", fake_session_state_for_repair)
    monkeypatch.setattr(import_mod, "should_reset_stale_cursor_for_repair", lambda *args, **kwargs: False)
    monkeypatch.setattr(import_mod, "restore_commit_index_if_safe", lambda *args, **kwargs: False)
    monkeypatch.setattr(import_mod, "collect_commit_artifact_state", fake_collect_commit_artifact_state)
    monkeypatch.setattr(import_mod, "flush_atom_pipeline", fake_flush_atom_pipeline)
    monkeypatch.setattr(import_mod, "wait_for_commit_artifacts", fake_wait_for_commit_artifacts)
    monkeypatch.setattr(
        import_mod,
        "recovered_integrity_from_artifacts",
        lambda **kwargs: "complete" if kwargs.get("session_complete") else "pending_async_memory",
    )
    monkeypatch.setattr(import_mod, "summarize_sample_progress", fake_summarize_sample_progress)
    monkeypatch.setattr(import_mod, "build_import_summary", lambda *args, **kwargs: {"status": kwargs.get("status")})
    monkeypatch.setattr(import_mod, "write_json", lambda *args, **kwargs: None)

    args = SimpleNamespace(workspace=str(tmp_path), account="acct")
    records = [
        {
            "sample_index": 1,
            "sample_id": "conv-30",
            "original_session_count": 2,
            "progress_sessions_total": 2,
            "estimated_import_tokens": 0,
            "session_records": [
                {
                    "session_id": "s1",
                    "session_key": "session_1",
                    "atom_flush": {"complete": False},
                    "commit_artifacts": {},
                },
                {
                    "session_id": "s2",
                    "session_key": "session_2",
                    "atom_flush": {"complete": False},
                    "commit_artifacts": {},
                },
            ],
        }
    ]

    result = asyncio.run(
        import_mod.finalize_import_records(
            args,
            sdk=object(),
            records=records,
            out_dir=tmp_path / "out",
            root=tmp_path,
            config_path=tmp_path / "config.json",
        )
    )

    assert flush_calls == ["s1"]
    nested = result[0]["session_records"]
    assert all(item["qa_ready_after_commit"] for item in nested)


def test_finalize_import_records_skips_flush_when_atom_or_cursor_already_ready(
    tmp_path: Path,
    monkeypatch,
) -> None:
    session_dir = tmp_path / "s1"
    session_dir.mkdir()
    flush_calls: list[str] = []
    collect_calls = {"count": 0}

    def fake_find_session_dir(workspace: str, account: str, session_id: str) -> Path | None:
        return session_dir if session_id == "s1" else None

    def fake_session_state_for_repair(path: Path) -> dict[str, object]:
        return {
            "session_id": path.name,
            "session_dir": str(path),
            "title": path.name,
            "message_count": 1,
            "last_message_id": "m0",
            "commit_index": 0,
            "atom_pipeline_index": 0,
            "expected_index": 0,
            "complete": False,
        }

    def fake_collect_commit_artifact_state(
        args: object,
        session_id: str,
        *,
        expected_message_count: int = 0,
        expected_last_message_id: str = "",
    ) -> dict[str, object]:
        collect_calls["count"] += 1
        ready = collect_calls["count"] >= 2
        return {
            "commit_index": 0,
            "expected_commit_index": 0,
            "commit_index_ok": True,
            "atom_pipeline_index": 0 if ready else -1,
            "expected_atom_pipeline_index": 0,
            "atom_pipeline_index_ok": ready,
            "legacy_commit_complete": False,
            "retrieval_ready": ready,
            "cursor_complete": ready,
            "complete": ready,
        }

    async def fake_flush_atom_pipeline(
        args: object,
        sdk: object,
        session_id: str,
        *,
        expected_message_count: int,
        expected_last_message_id: str,
    ) -> dict[str, object]:
        flush_calls.append(session_id)
        return {"complete": True}

    async def fake_wait_for_commit_artifacts(
        args: object,
        session_id: str,
        *,
        expected_message_count: int = 0,
        expected_last_message_id: str = "",
        label: str = "",
    ) -> dict[str, object]:
        return fake_collect_commit_artifact_state(
            args,
            session_id,
            expected_message_count=expected_message_count,
            expected_last_message_id=expected_last_message_id,
        )

    def fake_summarize_sample_progress(
        args: object,
        sample_index: int,
        sample_id: str,
        original_session_count: int,
        session_batches: list[object],
        estimated_import_tokens: int,
        records: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "sample_index": sample_index,
            "sample_id": sample_id,
            "session_records": records,
            "progress_sessions_done": sum(1 for item in records if item.get("qa_ready_after_commit")),
            "progress_sessions_total": original_session_count,
            "original_session_count": original_session_count,
            "estimated_import_tokens": estimated_import_tokens,
        }

    monkeypatch.setattr(import_mod, "find_session_dir", fake_find_session_dir)
    monkeypatch.setattr(import_mod, "session_state_for_repair", fake_session_state_for_repair)
    monkeypatch.setattr(import_mod, "should_reset_stale_cursor_for_repair", lambda *args, **kwargs: False)
    monkeypatch.setattr(import_mod, "restore_commit_index_if_safe", lambda *args, **kwargs: False)
    monkeypatch.setattr(import_mod, "collect_commit_artifact_state", fake_collect_commit_artifact_state)
    monkeypatch.setattr(import_mod, "flush_atom_pipeline", fake_flush_atom_pipeline)
    monkeypatch.setattr(import_mod, "wait_for_commit_artifacts", fake_wait_for_commit_artifacts)
    monkeypatch.setattr(
        import_mod,
        "recovered_integrity_from_artifacts",
        lambda **kwargs: "complete" if kwargs.get("session_complete") else "pending_async_memory",
    )
    monkeypatch.setattr(import_mod, "summarize_sample_progress", fake_summarize_sample_progress)
    monkeypatch.setattr(import_mod, "build_import_summary", lambda *args, **kwargs: {"status": kwargs.get("status")})
    monkeypatch.setattr(import_mod, "write_json", lambda *args, **kwargs: None)

    args = SimpleNamespace(workspace=str(tmp_path), account="acct")
    records = [
        {
            "sample_index": 1,
            "sample_id": "conv-30",
            "original_session_count": 1,
            "progress_sessions_total": 1,
            "estimated_import_tokens": 0,
            "session_records": [
                {
                    "session_id": "s1",
                    "session_key": "session_1",
                    "atom_flush": {"complete": False},
                    "commit_artifacts": {},
                },
            ],
        }
    ]

    result = asyncio.run(
        import_mod.finalize_import_records(
            args,
            sdk=object(),
            records=records,
            out_dir=tmp_path / "out",
            root=tmp_path,
            config_path=tmp_path / "config.json",
        )
    )

    assert flush_calls == []
    nested = result[0]["session_records"]
    assert nested[0]["atom_flush"]["skipped"] is True
    assert nested[0]["atom_flush"]["skip_reason"] in {"complete", "cursor_complete", "atom_pipeline_index_ok"}
    assert nested[0]["qa_ready_after_commit"] is True


def test_finalize_import_records_waits_for_stall_before_force_wait(
    tmp_path: Path,
    monkeypatch,
) -> None:
    session_dir = tmp_path / "s1"
    session_dir.mkdir()
    flush_calls: list[str] = []
    sleep_calls: list[float] = []
    now = {"value": 100.0}

    def fake_find_session_dir(workspace: str, account: str, session_id: str) -> Path | None:
        return session_dir if session_id == "s1" else None

    def fake_session_state_for_repair(path: Path) -> dict[str, object]:
        return {
            "session_id": path.name,
            "session_dir": str(path),
            "title": path.name,
            "message_count": 1,
            "last_message_id": "m0",
            "commit_index": 0,
            "atom_pipeline_index": -1,
            "expected_index": 0,
            "complete": False,
        }

    state = {"complete": False}

    def fake_collect_commit_artifact_state(
        args: object,
        session_id: str,
        *,
        expected_message_count: int = 0,
        expected_last_message_id: str = "",
    ) -> dict[str, object]:
        complete = bool(state["complete"])
        return {
            "commit_index": 0,
            "expected_commit_index": 0,
            "commit_index_ok": True,
            "atom_pipeline_index": 0 if complete else -1,
            "expected_atom_pipeline_index": 0,
            "atom_pipeline_index_ok": complete,
            "legacy_commit_complete": complete,
            "retrieval_ready": complete,
            "cursor_complete": complete,
            "complete": complete,
        }

    async def fake_flush_atom_pipeline(
        args: object,
        sdk: object,
        session_id: str,
        *,
        expected_message_count: int,
        expected_last_message_id: str,
    ) -> dict[str, object]:
        flush_calls.append(session_id)
        state["complete"] = True
        return {"complete": True}

    async def fake_wait_for_commit_artifacts(
        args: object,
        session_id: str,
        *,
        expected_message_count: int = 0,
        expected_last_message_id: str = "",
        label: str = "",
    ) -> dict[str, object]:
        return fake_collect_commit_artifact_state(
            args,
            session_id,
            expected_message_count=expected_message_count,
            expected_last_message_id=expected_last_message_id,
        )

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)
        now["value"] += delay
        state["complete"] = True

    def fake_summarize_sample_progress(
        args: object,
        sample_index: int,
        sample_id: str,
        original_session_count: int,
        session_batches: list[object],
        estimated_import_tokens: int,
        records: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "sample_index": sample_index,
            "sample_id": sample_id,
            "session_records": records,
            "progress_sessions_done": sum(1 for item in records if item.get("qa_ready_after_commit")),
            "progress_sessions_total": original_session_count,
            "original_session_count": original_session_count,
            "estimated_import_tokens": estimated_import_tokens,
        }

    monkeypatch.setattr(import_mod, "find_session_dir", fake_find_session_dir)
    monkeypatch.setattr(import_mod, "session_state_for_repair", fake_session_state_for_repair)
    monkeypatch.setattr(import_mod, "should_reset_stale_cursor_for_repair", lambda *args, **kwargs: False)
    monkeypatch.setattr(import_mod, "restore_commit_index_if_safe", lambda *args, **kwargs: False)
    monkeypatch.setattr(import_mod, "collect_commit_artifact_state", fake_collect_commit_artifact_state)
    monkeypatch.setattr(import_mod, "flush_atom_pipeline", fake_flush_atom_pipeline)
    monkeypatch.setattr(import_mod, "wait_for_commit_artifacts", fake_wait_for_commit_artifacts)
    monkeypatch.setattr(import_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(import_mod.time, "time", lambda: now["value"])
    monkeypatch.setattr(
        import_mod,
        "recovered_integrity_from_artifacts",
        lambda **kwargs: "complete" if kwargs.get("session_complete") else "pending_async_memory",
    )
    monkeypatch.setattr(import_mod, "summarize_sample_progress", fake_summarize_sample_progress)
    monkeypatch.setattr(import_mod, "build_import_summary", lambda *args, **kwargs: {"status": kwargs.get("status")})
    monkeypatch.setattr(import_mod, "write_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(import_mod, "finalize_force_wait_stall_seconds", lambda args: 45.0)

    args = SimpleNamespace(workspace=str(tmp_path), account="acct", flush_call_timeout_s=20, commit_wait_s=12)
    records = [
        {
            "sample_index": 1,
            "sample_id": "conv-30",
            "original_session_count": 1,
            "progress_sessions_total": 1,
            "estimated_import_tokens": 0,
            "session_records": [
                {
                    "session_id": "s1",
                    "session_key": "session_1",
                    "atom_flush": {"complete": False},
                    "commit_artifacts": {},
                },
            ],
        }
    ]

    result = asyncio.run(
        import_mod.finalize_import_records(
            args,
            sdk=object(),
            records=records,
            out_dir=tmp_path / "out",
            root=tmp_path,
            config_path=tmp_path / "config.json",
        )
    )

    assert flush_calls == []
    assert sleep_calls
    assert result[0]["session_records"][0]["qa_ready_after_commit"] is True
