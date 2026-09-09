from __future__ import annotations

import csv
import json
from pathlib import Path

from performance.targets.echomem.acceptance.stage_observability import (
    correlate_requests,
    cross_check,
    parse_structured_logs,
    read_stage_events,
    summarize_log_stages,
    summarize_prometheus_histograms,
    trace_ref,
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
