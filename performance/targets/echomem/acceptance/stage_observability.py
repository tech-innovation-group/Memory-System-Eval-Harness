"""Collect and summarize real EchoMem stage timing evidence.

Only whitelisted structured fields are persisted. Trace identifiers are hashed
before they leave this module, so reports can correlate requests without
publishing opaque service identifiers or payloads.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from performance.stats import percentile


LOG_EVENTS = frozenset({
    "recall_stage_completed",
    "recall_engine_completed",
    "dashscope_rerank_operation",
    "http_request_completed",
    "memory_extraction_completed",
    "atomic_pipeline_completed",
})

PROMETHEUS_HISTOGRAMS = frozenset({
    "echomem_memrouter_planning_duration_seconds",
    "echomem_memrouter_stage_duration_seconds",
    "echomem_memrouter_stage_queue_wait_seconds",
    "echomem_recall_duration_seconds",
    "echomem_router_embedding_duration_seconds",
    "echomem_engine_model_duration_seconds",
    "echomem_engine_model_ttfb_seconds",
})


def trace_ref(value: object) -> str:
    """Return a stable non-reversible reference for one service trace id."""
    text = str(value or "").strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text else ""


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _json_payload(line: str) -> dict[str, Any] | None:
    start = line.find("{")
    if start < 0:
        return None
    try:
        value = json.loads(line[start:])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _base_event(payload: dict[str, Any], *, module: str) -> dict[str, Any]:
    return {
        "event": str(payload.get("event") or ""),
        "module": module,
        "trace_ref": trace_ref(payload.get("trace_id")),
        "status": str(payload.get("status") or ""),
        "duration_ms": _number(payload.get("duration_ms")),
        "queue_wait_ms": _number(payload.get("queue_wait_ms")),
        "item_count": _number(payload.get("item_count")),
        "source": "structured_log",
    }


def normalize_log_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Project one JSON log payload into safe, stage-level timing rows."""
    event = str(payload.get("event") or "")
    if event not in LOG_EVENTS:
        return []
    if event == "recall_stage_completed":
        return [_base_event(payload, module=f"recall/{payload.get('stage') or 'unknown'}")]
    if event == "recall_engine_completed":
        return [_base_event(payload, module=f"recall_engine/{payload.get('engine_id') or 'unknown'}")]
    if event == "dashscope_rerank_operation":
        return [_base_event(payload, module="provider/dashscope_rerank")]
    if event == "http_request_completed":
        method = str(payload.get("method") or "OTHER")
        route = str(payload.get("route") or "unknown")
        return [_base_event(payload, module=f"http/{method} {route}")]
    if event == "memory_extraction_completed":
        engine = str(payload.get("engine_id") or "unknown")
        return [_base_event(payload, module=f"commit/memory_extraction/{engine}")]

    timings = payload.get("macro_stage_timings_ms")
    if not isinstance(timings, dict):
        return [_base_event(payload, module="commit/atomic_pipeline")]
    rows = []
    for stage, value in timings.items():
        duration = _number(value)
        if duration is None:
            continue
        row = _base_event(payload, module=f"atomic/{stage}")
        row["duration_ms"] = duration
        rows.append(row)
    return rows or [_base_event(payload, module="commit/atomic_pipeline")]


def parse_structured_logs(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        payload = _json_payload(line)
        if payload is not None:
            rows.extend(normalize_log_payload(payload))
    return rows


def read_stage_events(path: Path) -> list[dict[str, Any]]:
    """Read normalized stage rows without loading raw container logs."""
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def collect_container_stage_events(
    container: str,
    *,
    since: str,
    output: Path,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    """Collect a bounded Docker log window and persist normalized events."""
    if not container:
        return {"status": "NOT_CONFIGURED", "reason": "resource_container_missing", "events": []}
    try:
        completed = subprocess.run(
            ["docker", "logs", "--since", since, container],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "ERROR", "reason": type(exc).__name__, "events": []}
    text = completed.stdout + "\n" + completed.stderr
    rows = parse_structured_logs(text)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return {
        "status": "COLLECTED" if completed.returncode == 0 else "PARTIAL",
        "reason": "" if completed.returncode == 0 else "docker_logs_nonzero",
        "events": rows,
        "event_count": len(rows),
        "trace_count": len({row["trace_ref"] for row in rows if row.get("trace_ref")}),
        "path": str(output),
    }


def summarize_log_stages(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in events:
        grouped[str(row.get("module") or "unknown")].append(row)
    result = []
    for module, rows in sorted(grouped.items()):
        durations = [value for row in rows if (value := _number(row.get("duration_ms"))) is not None]
        waits = [value for row in rows if (value := _number(row.get("queue_wait_ms"))) is not None]
        result.append({
            "module": module,
            "source": "structured_log",
            "observations": len(durations),
            "trace_count": len({row.get("trace_ref") for row in rows if row.get("trace_ref")}),
            "p50_ms": percentile(durations, 50),
            "p95_ms": percentile(durations, 95),
            "p99_ms": percentile(durations, 99),
            "queue_wait_observations": len(waits),
            "queue_wait_p50_ms": percentile(waits, 50),
            "queue_wait_p95_ms": percentile(waits, 95),
            "queue_wait_p99_ms": percentile(waits, 99),
        })
    return result


def _labels(text: str) -> dict[str, str]:
    try:
        value = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}
    return {str(key): str(item) for key, item in value.items()} if isinstance(value, dict) else {}


def _bucket_percentile(buckets: dict[float, float], total: float, q: float) -> float | None:
    if total <= 0 or not buckets:
        return None
    target = total * q
    lower_bound = 0.0
    lower_count = 0.0
    for bound, count in sorted(buckets.items()):
        if count >= target:
            if count <= lower_count:
                return bound
            fraction = (target - lower_count) / (count - lower_count)
            return lower_bound + (bound - lower_bound) * fraction
        lower_bound, lower_count = bound, count
    return max(buckets)


def summarize_prometheus_histograms(paths: Iterable[Path]) -> list[dict[str, Any]]:
    """Calculate run-window histogram deltas from metrics CSV files."""
    aggregate: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]] = {}
    for path in paths:
        if not path.is_file():
            continue
        series: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                metric = str(row.get("metric") or "")
                base = next((name for name in PROMETHEUS_HISTOGRAMS if metric.startswith(name + "_")), None)
                if base is None:
                    continue
                try:
                    series[(metric, str(row.get("labels") or "{}"))].append(
                        (float(row.get("ts") or 0), float(row.get("value") or 0))
                    )
                except (TypeError, ValueError):
                    continue
        for (metric, labels_text), points in series.items():
            points.sort()
            delta = max(0.0, points[-1][1] - points[0][1]) if len(points) > 1 else 0.0
            base = next(name for name in PROMETHEUS_HISTOGRAMS if metric.startswith(name + "_"))
            labels = _labels(labels_text)
            le = labels.pop("le", None)
            key = (base, tuple(sorted(labels.items())))
            target = aggregate.setdefault(key, {"buckets": defaultdict(float), "count": 0.0, "sum": 0.0})
            if metric.endswith("_bucket") and le not in (None, "+Inf"):
                try:
                    target["buckets"][float(le)] += delta
                except ValueError:
                    pass
            elif metric.endswith("_count"):
                target["count"] += delta
            elif metric.endswith("_sum"):
                target["sum"] += delta
    rows = []
    for (metric, label_items), values in sorted(aggregate.items()):
        count = float(values["count"])
        if count <= 0:
            continue
        labels = dict(label_items)
        suffix = ", ".join(f"{key}={value}" for key, value in label_items)
        rows.append({
            "module": metric.removeprefix("echomem_") + (f" [{suffix}]" if suffix else ""),
            "metric": metric,
            "labels": labels,
            "source": "prometheus_histogram_delta",
            "observations": int(count),
            "mean_ms": round(values["sum"] / count * 1000.0, 3),
            "p50_ms": round((_bucket_percentile(values["buckets"], count, 0.50) or 0.0) * 1000.0, 3),
            "p95_ms": round((_bucket_percentile(values["buckets"], count, 0.95) or 0.0) * 1000.0, 3),
            "p99_ms": round((_bucket_percentile(values["buckets"], count, 0.99) or 0.0) * 1000.0, 3),
        })
    return rows


def correlate_requests(
    records: Iterable[dict[str, Any]], events: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    stage_refs = {str(row.get("trace_ref")) for row in events if row.get("trace_ref")}
    eligible = [row for row in records if row.get("op") in {"read", "commit_done"}]
    traced = [row for row in eligible if row.get("trace_ref")]
    linked = [row for row in traced if str(row.get("trace_ref")) in stage_refs]
    return {
        "eligible_requests": len(eligible),
        "requests_with_trace": len(traced),
        "requests_linked_to_internal_stage": len(linked),
        "requests_missing_trace": len(eligible) - len(traced),
        "traced_without_stage_log": len(traced) - len(linked),
        "status": "CORRELATED" if eligible and len(linked) == len(eligible) else "PARTIAL" if linked else "MISSING",
    }


def cross_check(log_rows: Iterable[dict[str, Any]], metric_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Report independent log/metric presence; never derive one from another."""
    logs = list(log_rows)
    metrics = list(metric_rows)
    pairs = [
        ("recall_total", lambda row: row.get("module") == "recall/recall_total",
         "echomem_recall_duration_seconds", lambda row: row.get("metric") == "echomem_recall_duration_seconds"),
        ("query_embedding", lambda row: row.get("module") == "recall/query_embedding",
         "echomem_router_embedding_duration_seconds", lambda row: row.get("metric") == "echomem_router_embedding_duration_seconds"),
        ("memrouter_stage", lambda row: str(row.get("module", "")).startswith("recall/"),
         "echomem_memrouter_stage_duration_seconds", lambda row: row.get("metric") == "echomem_memrouter_stage_duration_seconds"),
        ("stage_queue_wait", lambda row: str(row.get("module", "")).startswith("recall/") and row.get("queue_wait_observations", 0),
         "echomem_memrouter_stage_queue_wait_seconds", lambda row: row.get("metric") == "echomem_memrouter_stage_queue_wait_seconds"),
    ]
    result = []
    for name, log_match, metric_name, metric_match in pairs:
        log_count = sum(int(row.get("observations") or 0) for row in logs if log_match(row))
        metric_count = sum(int(row.get("observations") or 0) for row in metrics if metric_match(row))
        result.append({
            "check": name,
            "log_observations": log_count,
            "prometheus_metric": metric_name,
            "prometheus_observations": metric_count,
            "status": "BOTH_PRESENT" if log_count and metric_count else "LOG_ONLY" if log_count else "METRIC_ONLY" if metric_count else "MISSING",
            "note": "两路独立统计用于覆盖交叉校验；Histogram 分桶近似值不与日志逐样本相减。",
        })
    return result
