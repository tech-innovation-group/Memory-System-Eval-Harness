from __future__ import annotations

import csv
import json
import math
from pathlib import Path

from performance.targets.echomem.acceptance.stage_observability import (
    correlate_requests,
    cross_check,
    parse_structured_logs,
    read_stage_events,
    summarize_log_stages,
    summarize_prometheus_histograms,
    trace_ref,
    response_trace_ref,
    _bucket_percentile,
    _counter_delta,
)


def test_structured_log_parser_expands_atomic_stages_and_hashes_trace() -> None:
    payloads = [
        {"event": "recall_stage_completed", "trace_id": "private-recall",
         "stage": "query_embedding", "status": "completed",
         "duration_ms": 12.5, "queue_wait_ms": 2.0},
        {"event": "atomic_pipeline_completed", "trace_id": "private-commit",
         "status": "completed", "macro_stage_timings_ms": {
             "extraction": 80.0, "atom_persistence": 11.0}},
        {"event": "unrelated", "sensitive": "do-not-copy"},
    ]
    text = "\n".join("2026-09-09T00:00:00Z " + json.dumps(row) for row in payloads)
    rows = parse_structured_logs(text)
    assert {row["module"] for row in rows} == {
        "recall/query_embedding", "atomic/extraction", "atomic/atom_persistence"}
    assert rows[0]["trace_ref"] == trace_ref("private-recall")
    assert "private-recall" not in json.dumps(rows)
    assert "sensitive" not in json.dumps(rows)
    stats = {row["module"]: row for row in summarize_log_stages(rows)}
    assert stats["recall/query_embedding"]["queue_wait_p95_ms"] == 2.0
    assert stats["atomic/extraction"]["p50_ms"] == 80.0


def _write_metrics(path: Path) -> None:
    fields = ["ts", "metric", "labels", "value"]
    rows = []
    for ts, values in ((1, (10, 20, 20, 2.0)), (2, (12, 25, 25, 2.8))):
        low, high, count, total = values
        rows.extend([
            [ts, "echomem_memrouter_stage_duration_seconds_bucket",
             json.dumps({"stage": "query_embedding", "le": "0.1"}), low],
            [ts, "echomem_memrouter_stage_duration_seconds_bucket",
             json.dumps({"stage": "query_embedding", "le": "1.0"}), high],
            [ts, "echomem_memrouter_stage_duration_seconds_count",
             json.dumps({"stage": "query_embedding"}), count],
            [ts, "echomem_memrouter_stage_duration_seconds_sum",
             json.dumps({"stage": "query_embedding"}), total],
        ])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        writer.writerows(rows)


def test_prometheus_summary_uses_window_delta_not_process_lifetime(tmp_path: Path) -> None:
    path = tmp_path / "metrics_samples.csv"
    _write_metrics(path)
    rows = summarize_prometheus_histograms([path])
    assert len(rows) == 1
    assert rows[0]["observations"] == 5
    assert rows[0]["mean_ms"] == 160.0
    assert rows[0]["labels"] == {"stage": "query_embedding"}


def test_trace_correlation_and_cross_check_keep_missing_visible() -> None:
    ref = trace_ref("trace-1")
    events = [{"module": "recall/query_embedding", "trace_ref": ref,
               "duration_ms": 10, "queue_wait_ms": 1}]
    correlation = correlate_requests([
        {"op": "read", "trace_ref": ref},
        {"op": "commit_done", "trace_ref": ""},
    ], events)
    assert correlation["requests_linked_to_internal_stage"] == 1
    assert correlation["requests_missing_trace"] == 1
    logs = summarize_log_stages(events)
    checks = cross_check(logs, [])
    query_embedding = next(row for row in checks if row["check"] == "query_embedding")
    assert query_embedding["status"] == "LOG_ONLY"


def test_read_stage_events_skips_invalid_rows(tmp_path: Path) -> None:
    path = tmp_path / "stages.jsonl"
    path.write_text('{"module":"recall/rule","duration_ms":1}\ninvalid\n')
    assert read_stage_events(path) == [{"module": "recall/rule", "duration_ms": 1}]


def test_prometheus_only_stage_is_observable(tmp_path: Path) -> None:
    from performance.targets.echomem.acceptance.observation import summarize_timing_evidence
    _write_metrics(tmp_path / "metrics_samples.csv")
    summary = summarize_timing_evidence({"runs": [{"output_dir": str(tmp_path)}]}, [])
    assert "recall/query_embedding" not in summary["unobservable_modules"]
    assert "atomic/extraction" in summary["unobservable_modules"]


def test_missing_log_duration_and_wait_are_not_zero() -> None:
    row = summarize_log_stages([{"module": "atomic/extraction"}])[0]
    assert row["observations"] == 0
    assert row["p95_ms"] is None
    assert row["queue_wait_p95_ms"] is None


def test_response_trace_reference_preserves_supported_envelopes_only() -> None:
    expected = trace_ref("private-trace")
    for payload in ({"trace_id": "private-trace"},
                    {"result": {"trace_id": "private-trace"}},
                    {"status": {"trace_id": "private-trace"}}):
        assert response_trace_ref(payload) == expected
    assert response_trace_ref({"request_id": "different-http-id"}) == ""
    assert response_trace_ref({"trace_id": {"not": "a trace"}}) == ""


def test_m1_trace_correlation_uses_measurements_and_excludes_unsent(tmp_path: Path) -> None:
    from performance.targets.echomem.acceptance.observation import summarize_timing_evidence
    ref = trace_ref("private-trace")
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({"levels": [{"measurement_file": "level-1.json"}]}))
    (tmp_path / "level-1.json").write_text(json.dumps({"rows": [
        {"op": "read", "sent": True, "trace_ref": ref},
        {"op": "read", "sent": False},
        {"op": "commit_done", "trace_ref": ref},
    ]}))
    suite = {"m1": {"reports": [{"topology": "cross-tenant", "path": str(report_path)}]},
             "stage_observability": {"events": [{"module": "recall/recall_total",
                                                   "duration_ms": 12, "trace_ref": ref}]}}
    correlation = summarize_timing_evidence(suite, [])["trace_correlation"]
    assert correlation["eligible_requests"] == 2
    assert correlation["requests_linked_to_internal_stage"] == 2
    assert correlation["status"] == "CORRELATED"
    (tmp_path / "level-1.json").unlink()
    correlation = summarize_timing_evidence(suite, [])["trace_correlation"]
    assert correlation["status"] == "PARTIAL"
    assert correlation["missing_m1_evidence_files"] == ["cross-tenant:level-1.json"]


def test_capacity_load_persists_hashed_response_trace(monkeypatch) -> None:
    from types import SimpleNamespace
    from performance.targets.echomem.acceptance import capacity_load
    from performance.targets.echomem.acceptance.semantic_corpus import build_corpus
    from performance.targets.echomem.probes._client import HttpResult
    monkeypatch.setattr(capacity_load, "arrival_plan", lambda *args, **kwargs: [(0, "read", 0, 0)])
    actor = SimpleNamespace(
        tenant_index=0, user_index=0, corpus=build_corpus("trace-unit"),
        client=SimpleNamespace(agent_id="unit", request=lambda *args, **kwargs: HttpResult(
            "POST", "/api/retrieval/search", 200, 0.01,
            payload={"result": {"trace_id": "private-capacity-trace"}})),
    )
    result = capacity_load.measure([actor], duration_s=1)
    row = next(row for row in result["rows"] if row["op"] == "read")
    assert row["trace_ref"] == trace_ref("private-capacity-trace")
    assert "private-capacity-trace" not in json.dumps(result)


def test_histogram_tail_cannot_be_reported_as_finite_maximum() -> None:
    assert _bucket_percentile({0.1: 3, 1.0: 8}, 10, 0.95) is None
    assert _bucket_percentile({}, 10, 0.50) is None
    assert math.isclose(_bucket_percentile({0.1: 3, 1.0: 8}, 10, 0.50), 0.46)


def test_counter_delta_preserves_observed_increments_after_reset() -> None:
    assert _counter_delta([(1, 100), (2, 110), (3, 2), (4, 5)]) == (15, 1)
    assert _counter_delta([(1, 100)]) == (0, 0)


def test_histogram_missing_buckets_and_reset_are_explicit(tmp_path: Path) -> None:
    path = tmp_path / "metrics.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ts", "metric", "labels", "value"])
        for ts, count, total in [(1, 100, 20), (2, 110, 22), (3, 2, 0.4), (4, 5, 1)]:
            for suffix, value in [("count", count), ("sum", total)]:
                writer.writerow([ts, "echomem_recall_duration_seconds_" + suffix, "{}", value])
    row = summarize_prometheus_histograms([path, path])[0]
    assert row["observations"] == 15
    assert row["counter_resets"] == 1
    assert row["count_is_lower_bound"] is True
    assert row["mean_ms"] == 200
    assert row["p50_ms"] is None
    assert row["p95_ms"] is None
    assert row["quantile_missing_reason"] == "finite_buckets_missing"
def test_container_log_collection_preserves_explicit_end_window(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from performance.targets.echomem.acceptance import stage_observability as module
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(module.subprocess, 'run', run)
    result = module.collect_container_stage_events(
        'dedicated-test', since='2026-09-09T09:00:00Z',
        until='2026-09-09T10:00:00Z', output=tmp_path / 'events.jsonl')
    assert calls == [['docker', 'logs', '--since', '2026-09-09T09:00:00Z',
                      '--until', '2026-09-09T10:00:00Z', 'dedicated-test']]
    assert result['until'] == '2026-09-09T10:00:00Z'
def test_http_ledger_counts_observed_status_without_assuming_business_success():
    from performance.targets.echomem.acceptance.stage_observability import normalize_log_payload, summarize_http_calls
    events = []
    for code in (200, 503, True):
        events.extend(normalize_log_payload({"event": "http_request_completed", "method": "GET",
                                             "route": "/api/v1/system/ready", "status_code": code}))
    rows = summarize_http_calls(events)
    assert len(rows) == 1
    assert rows[0]["observed_completions"] == 3
    assert rows[0]["status_counts"] == {"200": 1, "503": 1, "unknown": 1}
    assert rows[0]["business_success"] is None
