from __future__ import annotations

from scripts.echomemory_wait_and_eval import snapshot_ready


def test_snapshot_ready_accepts_document_style_ready_state() -> None:
    snapshot = {
        "session_count": 1,
        "complete_sessions": 1,
        "abstract_count": 1,
        "overview_count": 1,
        "atom_count": 0,
        "graph_count": 0,
        "vector_count": 8,
    }

    assert snapshot_ready(snapshot, expected_sessions_total=0) is True


def test_snapshot_ready_accepts_structured_memory_state() -> None:
    snapshot = {
        "session_count": 1,
        "complete_sessions": 0,
        "abstract_count": 1,
        "overview_count": 1,
        "atom_count": 3,
        "graph_count": 2,
        "vector_count": 8,
    }

    assert snapshot_ready(snapshot, expected_sessions_total=1) is True


def test_snapshot_ready_rejects_missing_summaries() -> None:
    snapshot = {
        "session_count": 1,
        "complete_sessions": 1,
        "abstract_count": 0,
        "overview_count": 1,
        "atom_count": 0,
        "graph_count": 0,
        "vector_count": 8,
    }

    assert snapshot_ready(snapshot, expected_sessions_total=1) is False
