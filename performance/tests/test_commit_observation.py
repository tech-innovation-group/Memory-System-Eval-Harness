"""Local protocol fixtures; not real-model stress evidence."""

import threading
import csv

import pytest

from performance.targets.echomem.protocol import commit_session, poll_commit
from performance.tests.conftest import MockState
from performance.tests.test_ctx import make_ctx
from performance.targets.echomem.orchestrator.runner import _write_commit_results


def test_pending_completed_audit_survives_csv(server):
    _, _, url = server
    ctx, records, _ = make_ctx(url)
    receipt = commit_session(ctx, "s1")
    assert receipt.ok and receipt.http_status == 202
    result = poll_commit(ctx, "s1", receipt.json["archive_id"], interval_s=.01)
    row = result.record.to_csv_row()
    assert row["poll_evidence_version"] == "echomem-poll-v1"
    assert row["poll_count"] == result.polls == 3
    assert row["poll_http_errors"] == 0
    assert row["commit_terminal_state"] == "completed"
    assert receipt.record.accepted_at_ms <= row["last_nonterminal_at_ms"] <= row["terminal_at_ms"]
    assert row["completed_at_ms"] == row["terminal_at_ms"] <= row["observation_ended_at_ms"]
    assert row["stage_ms"] > 0 and len(records) == 2


@pytest.mark.parametrize("state,outcome,terminal", [
    (MockState(always_pending=True), "timeout", ""),
    (MockState(poll_fail_after=1), "failed", "failed"),
    (MockState(poll_http_status=404), "failed", ""),
    (MockState(poll_http_status=503), "timeout", ""),
])
def test_observation_failure_is_not_task_failure(mock_server, state, outcome, terminal):
    _, _, url = mock_server(state)
    ctx, _, _ = make_ctx(url)
    result = poll_commit(ctx, "s", "a", interval_s=.01, timeout_s=.2)
    row = result.record
    assert result.status == row.poll_outcome == outcome
    assert row.commit_terminal_state == terminal
    assert (row.terminal_at_ms is not None) == bool(terminal)
    assert row.completed_at_ms is None
    assert row.observation_ended_at_ms is not None
    assert row.poll_count > 0
    assert bool(row.poll_http_errors) == (state.poll_http_status != 200)


def test_stopped_observation_does_not_mark_receipt_completed(server):
    _, _, url = server
    stop = threading.Event()
    ctx, records, _ = make_ctx(url, stop=stop)
    receipt = commit_session(ctx, "s1")
    stop.set()
    result = poll_commit(ctx, "s1", receipt.json["archive_id"])
    assert result.status == "stopped"
    assert result.record.op == "commit_done"
    assert result.record.poll_outcome == "stopped"
    assert result.record.terminal_at_ms is None
    assert receipt.record.completed_at_ms is None
    assert receipt.record.poll_evidence_version == ""
    assert len(records) == 2


@pytest.mark.parametrize("state", [MockState(commit_status=200), MockState(commit_missing_archive=True)])
def test_invalid_receipt_does_not_start_accepted_interval(mock_server, state):
    _, _, url = mock_server(state)
    ctx, _, _ = make_ctx(url)
    response = commit_session(ctx, "s1")
    assert not response.ok
    assert response.record.error_type == "commit_invalid_receipt"
    assert response.record.accepted_at_ms is None


def test_commit_csv_does_not_join_different_archives_or_mislabel_timeout(mock_server, tmp_path):
    _, _, url = mock_server(MockState(always_pending=True))
    ctx, records, _ = make_ctx(url)
    first = commit_session(ctx, "shared-session")
    poll_commit(ctx, "shared-session", first.json["archive_id"], interval_s=.01, timeout_s=.1)
    second = commit_session(ctx, "shared-session")
    _write_commit_results(tmp_path, records)
    with (tmp_path / "commit_results.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert rows[0]["archive_id"] != rows[1]["archive_id"]
    assert rows[0]["status"] == rows[1]["status"] == "unresolved"
    assert rows[0]["observation_status"] == "timeout"
    assert rows[1]["observation_status"] == "unknown"
    assert rows[0]["done_ms"] == rows[1]["done_ms"] == ""
    assert first.record.completed_at_ms is second.record.completed_at_ms is None


def test_commit_csv_duplicate_observations_are_ambiguous(server, tmp_path):
    _, _, url = server
    ctx, records, _ = make_ctx(url)
    receipt = commit_session(ctx, "s")
    result = poll_commit(ctx, "s", receipt.json["archive_id"], interval_s=.01)
    _write_commit_results(tmp_path, records + [result.record])
    with (tmp_path / "commit_results.csv").open() as handle:
        row = next(csv.DictReader(handle))
    assert row["status"] == "ambiguous"
    assert row["done_ms"] == row["end_to_end_s"] == ""
