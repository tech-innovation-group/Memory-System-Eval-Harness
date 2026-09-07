"""Observation-only EchoMem M1-M6 aggregation and HTML publication.

This module never applies a performance threshold. Its four statuses describe
evidence availability and execution health, not whether EchoMem is fast enough.
"""

from __future__ import annotations

import csv
import html
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from performance.stats import percentile

STATUSES = ("MEASURED", "PARTIAL", "BLOCKED", "EXECUTION_ERROR")
METRIC_NAMES = {
    "M1": "4U8G 单实例热用户和 DAU",
    "M2": "单租户故障隔离",
    "M3": "多租户公平性",
    "M4": "Commit 洪泛下 Search 性能",
    "M5": "202 Commit 的 kill-9 恢复",
    "M6": "每层每租户四元组",
}
METRIC_PURPOSES = {
    "M1": "回答一个 4U8G 实例实际承载多少热用户，以及不同业务画像下的流量等价 DAU。",
    "M2": "观察一个租户失败或变慢时，其他租户的 Search 尾延迟和错误是否被拖累。",
    "M3": "检查同档位租户是否获得接近等权的 Commit 吞吐和 Search 响应机会。",
    "M4": "检查 Commit 洪泛期间，交互式 Search 的延迟、质量和可用性是否仍受保护。",
    "M5": "验证已返回 202 的 Commit 在 kill-9 后能否自主恢复，并保持消息、顺序和幂等一致。",
    "M6": "验证每个租户、每个处理层都能观测排队、等待、执行和拒绝四类数据。",
}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _truth(value: Any) -> bool:
    return value is True or str(value).lower() == "true"


def _probe_detail(payload: dict[str, Any], name: str) -> dict[str, Any]:
    for check in payload.get("checks", []):
        if check.get("name") == name:
            try:
                value = json.loads(check.get("detail") or "{}")
            except (TypeError, ValueError):
                value = {}
            return value if isinstance(value, dict) else {}
    return {}


def _records(run: dict[str, Any]) -> list[dict[str, str]]:
    path = Path(str(run.get("output_dir") or "")) / "records.csv"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _request_stats(rows: list[dict[str, Any]], op: str = "read") -> dict[str, Any]:
    selected = [row for row in rows if row.get("op") == op]
    latencies = [value for row in selected if (value := _number(row.get("stage_ms"))) is not None]
    ok = [row for row in selected if row.get("status") == "ok"]
    quality_observed = [row for row in selected if str(row.get("quality_ok", "")) != ""]
    quality_ok = [row for row in quality_observed if _truth(row.get("quality_ok"))]
    return {
        "planned_or_recorded": len(selected),
        "completed": len(selected),
        "ok": len(ok),
        "errors": len(selected) - len(ok),
        "timeouts": sum("timeout" in str(row.get("error_type") or "").lower() for row in selected),
        "p50_ms": percentile(latencies, 50),
        "p95_ms": percentile(latencies, 95),
        "p99_ms": percentile(latencies, 99),
        "quality_observed": len(quality_observed),
        "quality_ok": len(quality_ok),
        "quality_rate": len(quality_ok) / len(quality_observed) if quality_observed else None,
        "empty_recall": sum((_number(row.get("hit_count")) or 0) == 0 for row in selected),
        "http_status": dict(Counter(str(row.get("http_status") or "none") for row in selected)),
        "error_types": dict(Counter(str(row.get("error_type") or "none") for row in selected)),
    }


def jain(values: list[float]) -> float | None:
    """Jain index preserving zero-demand outcomes; all-zero is undefined."""
    if any(not math.isfinite(value) or value < 0 for value in values):
        return None
    denominator = len(values) * sum(value * value for value in values)
    return sum(values) ** 2 / denominator if values and denominator else None


def _status(*, expected: int, observed: int, blocked: bool = False,
            execution_error: bool = False) -> str:
    if execution_error:
        return "EXECUTION_ERROR"
    if blocked and observed == 0:
        return "BLOCKED"
    if expected > 0 and observed >= expected:
        return "MEASURED"
    return "PARTIAL" if observed else "BLOCKED"


def summarize_m1(reports: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    levels = [
        {**level, "topology": report.get("topology")}
        for report in reports for level in report.get("levels", [])
    ]
    measured = [level for level in levels if level.get("status") == "MEASURED"]
    boundary = [
        report.get("operational_boundary") for report in reports
        if report.get("operational_boundary")
    ]
    highest = max((int(level.get("hot_users") or 0) for level in measured), default=None)
    requested = sum(len(report.get("levels_requested", [])) * len(
        ("search", "commit", "mixed", "hotspot")
        if report.get("load_profile") == "all" else (report.get("load_profile"),)
    ) for report in reports)
    scenarios = profile.get("dau_scenarios") or [
        {"name": "read-heavy", "searches_per_user_day": 50, "commits_per_user_day": 5, "peak_to_average_ratio": 3},
        {"name": "balanced", "searches_per_user_day": 20, "commits_per_user_day": 20, "peak_to_average_ratio": 5},
        {"name": "write-heavy", "searches_per_user_day": 5, "commits_per_user_day": 50, "peak_to_average_ratio": 8},
    ]
    search_rps = max((float(level.get("sent_search_rps") or 0) for level in measured), default=0)
    commit_rps = max((float((level.get("commit") or {}).get("completed_rps") or 0) for level in measured), default=0)
    estimates = []
    for scenario in scenarios:
        peak = float(scenario["peak_to_average_ratio"])
        searches = float(scenario["searches_per_user_day"])
        commits = float(scenario["commits_per_user_day"])
        search_dau = search_rps * 86400 / searches / peak if searches else None
        commit_dau = commit_rps * 86400 / commits / peak if commits else None
        candidates = [value for value in (search_dau, commit_dau) if value is not None and value > 0]
        estimates.append({**scenario, "search_equivalent_dau": search_dau,
                          "commit_equivalent_dau": commit_dau,
                          "traffic_equivalent_dau": min(candidates) if candidates else None,
                          "is_measured_maximum": False})
    return {
        "status": _status(expected=requested, observed=len(measured),
                          blocked=not reports),
        "reason": "容量与 DAU 仅作观测和情景换算，不使用性能门槛",
        "resource_evidence": profile.get("resource_evidence"),
        "highest_measured_hot_users": highest,
        "first_operational_anomaly": boundary[0] if boundary else None,
        "unmeasured_ranges": [r.get("levels_requested", []) for r in reports if len(r.get("levels", [])) < len(r.get("levels_requested", []))],
        "levels": levels,
        "dau_scenarios": estimates,
        "expected_windows": requested,
        "measured_windows": len(measured),
    }


def summarize_m2(suite: dict[str, Any], profile: dict[str, Any], *, quick: bool) -> dict[str, Any]:
    payload = suite.get("fault_isolation") or {}
    cases = []
    for case in payload.get("cases", []):
        detail = _probe_detail(case, "fault-isolation")
        cases.append({
            "target_tenant": case.get("target_tenant"),
            "fault_type": case.get("fault_type"),
            "repetition": case.get("repetition"),
            "fault_observed": detail.get("fault_observed"),
            "before": detail.get("before"), "during": detail.get("during"),
            "after": detail.get("after"),
            "degradation_by_tenant": detail.get("degradation_by_tenant", {}),
            "fault_recovered": detail.get("fault_recovered"),
            "target_recovery_observed_s": detail.get("target_recovery_observed_s"),
            "raw_probe_verdict": case.get("status"),
        })
    expected = 24
    complete = [case for case in cases if case["fault_observed"] is True
                and case["before"] and case["during"] and case["after"]]
    return {"status": "PARTIAL" if quick and cases else _status(
                expected=expected, observed=len(complete),
                blocked=not cases),
            "reason": "quick 仅抽样，不代表完整 24 例" if quick else
                      "保留全部旁观租户请求与前/中/后阶段数据，不设置劣化门槛",
            "expected_cases": expected, "observed_cases": len(cases),
            "complete_cases": len(complete), "cases": cases}


def _fairness_window(run: dict[str, Any], tenant_count: int) -> dict[str, Any]:
    rows = _records(run)
    duration = float(run.get("duration_s") or (run.get("summary") or {}).get("run", {}).get("duration_s") or 0)
    tenants = []
    for tenant in range(tenant_count):
        selected = [row for row in rows if str(row.get("tenant_idx")) == str(tenant)]
        submits = [row for row in selected if row.get("op") == "commit_submit"]
        accepted = [row for row in submits if _number(row.get("http_status")) == 202]
        terminal = [row for row in selected if row.get("op") == "commit_done"]
        done = [row for row in terminal if row.get("status") == "ok"]
        failed = [row for row in terminal if row.get("status") != "ok"]
        completion_times = sorted(value for row in done if (value := _number(row.get("completed_at_ms") or row.get("ts_ms"))) is not None)
        longest_gap = None
        if completion_times:
            request_times = [_number(row.get("ts_ms")) for row in selected]
            request_times = [value for value in request_times if value is not None]
            window_start = min(request_times, default=completion_times[0])
            window_end = window_start + duration * 1000
            points = [window_start, *completion_times, window_end]
            longest_gap = max((b - a for a, b in zip(points, points[1:])), default=0) / 1000
        tenants.append({"tenant_index": tenant, "commit_submitted": len(submits),
                        "commit_accepted": len(accepted), "commit_completed": len(done),
                        "commit_failed": len(failed),
                        "commit_pending": max(0, len(accepted) - len(terminal)),
                        "commit_completed_per_s": len(done) / duration if duration else None,
                        "longest_no_completion_s": longest_gap,
                        "search": _request_stats(selected)})
    commit_values = [float(row["commit_completed_per_s"] or 0) for row in tenants]
    inverse_p95 = [1000 / row["search"]["p95_ms"] if row["search"]["p95_ms"] else 0 for row in tenants]
    return {"scenario": run.get("scenario"), "duration_s": duration,
            "tenant_count": tenant_count, "tenants": tenants,
            "commit_throughput_jain": jain(commit_values),
            "search_inverse_p95_jain": jain(inverse_p95)}


def summarize_m3(runs: dict[str, dict[str, Any]], *, quick: bool) -> dict[str, Any]:
    windows = []
    for count in (4, 8):
        run = runs.get(f"m3-fairness-{count}t")
        if run and _records(run):
            windows.append(_fairness_window(run, count))
    return {"status": "PARTIAL" if quick and windows else _status(expected=2, observed=len(windows)),
            "reason": "展示实际 Jain；零完成租户保留，全部为零时为 undefined",
            "expected_windows": 2, "observed_windows": len(windows), "windows": windows}


def _flood_window(baseline_rows: list[dict[str, Any]], run: dict[str, Any], tenant_count: int = 4) -> dict[str, Any]:
    rows = _records(run)
    accepted = [row for row in rows if row.get("op") == "commit_submit"
                and _number(row.get("http_status")) == 202 and row.get("archive_id")]
    done = {(row.get("tenant_idx"), row.get("session_id"), row.get("archive_id")): row
            for row in rows if row.get("op") == "commit_done"}
    intervals = []
    for row in accepted:
        key = row.get("tenant_idx"), row.get("session_id"), row.get("archive_id")
        start = _number(row.get("accepted_at_ms"))
        terminal = _number((done.get(key) or {}).get("completed_at_ms") or (done.get(key) or {}).get("ts_ms"))
        if start is not None:
            intervals.append((start, terminal or float("inf")))
    overlap = []
    for row in rows:
        if row.get("op") != "read":
            continue
        finished = _number(row.get("ts_ms"))
        latency = _number(row.get("stage_ms"))
        started = finished - latency if finished is not None and latency is not None else None
        if started is not None and any(left <= started <= right for left, right in intervals):
            overlap.append(row)
    by_tenant = []
    for tenant in range(tenant_count):
        before = _request_stats([row for row in baseline_rows if str(row.get("tenant_idx")) == str(tenant)])
        during = _request_stats([row for row in overlap if str(row.get("tenant_idx")) == str(tenant)])
        ratio = during["p95_ms"] / before["p95_ms"] if before["p95_ms"] and during["p95_ms"] else None
        by_tenant.append({"tenant_index": tenant, "baseline": before, "overlap": during,
                          "p95_delta_ms": during["p95_ms"] - before["p95_ms"] if before["p95_ms"] is not None and during["p95_ms"] is not None else None,
                          "p95_ratio": ratio})
    completed = sum(row.get("status") == "ok" for row in done.values())
    failed = sum(row.get("status") != "ok" for row in done.values())
    events = []
    durations = []
    for row in accepted:
        key = row.get("tenant_idx"), row.get("session_id"), row.get("archive_id")
        start = _number(row.get("accepted_at_ms"))
        end = _number((done.get(key) or {}).get("completed_at_ms") or (done.get(key) or {}).get("ts_ms"))
        if start is not None:
            events.append((start, 1))
            if end is not None:
                events.append((end, -1))
                durations.append(end - start)
    queued = peak = 0
    commit_timeline = []
    for event_at, change in sorted(events, key=lambda item: (item[0], item[1])):
        queued += change
        peak = max(peak, queued)
        commit_timeline.append({"at_ms": event_at, "pending": max(0, queued)})
    search_buckets: dict[int, list[float]] = {}
    for row in overlap:
        at = _number(row.get("ts_ms"))
        latency = _number(row.get("stage_ms"))
        if at is not None and latency is not None:
            search_buckets.setdefault(int(at // 10000) * 10000, []).append(latency)
    search_timeline = [
        {"at_ms": at, "count": len(values), "p50_ms": percentile(values, 50),
         "p95_ms": percentile(values, 95), "p99_ms": percentile(values, 99)}
        for at, values in sorted(search_buckets.items())
    ]
    accepted_times = [left for left, _ in intervals]
    terminal_times = [right for _, right in intervals if math.isfinite(right)]
    drain_s = ((max(terminal_times) - max(accepted_times)) / 1000
               if accepted_times and len(terminal_times) == len(intervals) else None)
    return {"scenario": run.get("scenario"), "baseline": _request_stats(baseline_rows),
            "overlap": _request_stats(overlap), "tenants": by_tenant,
            "commit_planned_or_recorded": len([r for r in rows if r.get("op") == "commit_submit"]),
            "commit_accepted_202": len(accepted), "commit_rejected": len([r for r in rows if r.get("op") == "commit_submit"]) - len(accepted),
            "commit_completed": completed, "commit_failed": failed,
            "commit_pending": max(0, len(accepted) - len(done)),
            "queue_peak": peak, "oldest_task_age_s": max(durations, default=None) / 1000 if durations else None,
            "drain_time_s": drain_s,
            "commit_timeline": commit_timeline, "search_timeline": search_timeline,
            "overlap_intervals": len(intervals),
            "internal_order_observation": "内部顺序未观测"}


def summarize_m4(runs: dict[str, dict[str, Any]], *, quick: bool) -> dict[str, Any]:
    baseline_run = runs.get("m4-baseline") or {}
    baseline_rows = _records(baseline_run)
    windows = []
    for name in ("m4-flood-uniform", "m4-flood-single-tenant"):
        if name in runs and _records(runs[name]):
            windows.append(_flood_window(baseline_rows, runs[name]))
    observed = len(windows) + int(bool(baseline_rows))
    return {"status": "PARTIAL" if quick and observed else _status(expected=3, observed=observed),
            "reason": "配对轨迹只按 Search 开始时刻落入 accepted_at 至 completed_at 计算 overlap",
            "expected_windows": 3, "observed_windows": observed, "windows": windows,
            "internal_order_observation": "内部顺序未观测"}


def summarize_m5(suite: dict[str, Any], *, quick: bool) -> dict[str, Any]:
    payload = suite.get("commit_recovery") or {}
    samples = payload.get("samples") if isinstance(payload.get("samples"), list) else ([payload] if payload.get("checks") else [])
    rows = []
    required = {"commit-recovery", "pending-before-kill", "message-reconciliation",
                "cursor-reconciliation", "order-reconciliation", "idempotency-replay"}
    for sample in samples:
        checks = {check.get("name"): check for check in sample.get("checks", [])}
        detail = _probe_detail(sample, "commit-recovery")
        pending = _probe_detail(sample, "pending-before-kill")
        recovery_check = checks.get("commit-recovery", {})
        rows.append({"sample_index": sample.get("sample_index"),
                     "received_202": detail.get("accepted_202"),
                     "unfinished_at_kill": pending.get("accepted_202") is True
                                           and pending.get("state") in {"pending", "queued", "running", "processing", "in_progress", "awaiting_engines"},
                     "autonomous_completed": detail.get("autonomous_recovery_observed"),
                     "fully_reconciled": all(checks.get(name, {}).get("status") == "PASS" for name in required),
                     "recovery_elapsed_s": recovery_check.get("elapsed_s"),
                     "state_before_kill": pending.get("state"),
                     "second_restart": detail.get("second_restart"),
                     "raw_check_verdicts": {name: check.get("status") for name, check in checks.items()}})
    complete = [row for row in rows if row["received_202"] is True and row["unfinished_at_kill"] is True
                and row["autonomous_completed"] is True and row["fully_reconciled"]]
    second_requested = [row for row in rows if (row.get("second_restart") or {}).get("requested")]
    status = "PARTIAL" if quick and rows else _status(expected=3, observed=len(complete), blocked=not rows)
    if status == "MEASURED" and second_requested and not any(
        row["second_restart"].get("exercised") for row in second_requested
    ):
        status = "PARTIAL"
    state_coverage = {
        "queued_or_pending": sum(row.get("state_before_kill") in {"queued", "pending"} for row in rows),
        "running_or_awaiting_engines": sum(row.get("state_before_kill") in {"running", "processing", "in_progress", "awaiting_engines"} for row in rows),
    }
    return {"status": status,
            "reason": "先观察原 archive 自主恢复，之后才进行幂等键 replay",
            "expected_samples": 3, "observed_samples": len(rows),
            "received_202": sum(row["received_202"] is True for row in rows),
            "unfinished_at_kill": sum(row["unfinished_at_kill"] is True for row in rows),
            "autonomous_completed": sum(row["autonomous_completed"] is True for row in rows),
            "fully_reconciled": sum(row["fully_reconciled"] is True for row in rows),
            "complete_samples": len(complete), "state_coverage": state_coverage,
            "second_restart_exercised": sum(bool((row.get("second_restart") or {}).get("exercised")) for row in rows),
            "samples": rows}


def summarize_m6(suite: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    before = suite.get("tenant_observability_before") or {}
    final_payload = suite.get("tenant_observability_after_all") or suite.get("tenant_observability") or {}
    after = _probe_detail(final_payload, "tenant-observability") or final_payload
    initial = {(row.get("tenant_id"), row.get("lane")): row for row in before.get("rows", [])}
    final = {(row.get("tenant_id"), row.get("lane")): row for row in after.get("rows", [])}
    tenants = list((profile.get("tenant_observability") or {}).get("expected_tenants", []))
    lanes = list((profile.get("tenant_observability") or {}).get("expected_lanes", []))
    process_changed = any(
        before.get(field) is not None and after.get(field) is not None
        and before.get(field) != after.get(field)
        for field in ("process_id", "process_started_at", "boot_id")
    )
    fields = ("queued", "wait_seconds_total", "exec_seconds_total", "rejected_total",
              "accepted_total", "completed_total", "failed_total")
    matrix = []
    for tenant in tenants:
        for lane in lanes:
            left, right = initial.get((tenant, lane)), final.get((tenant, lane))
            missing = [field for field in fields if not left or not right or field not in left or field not in right]
            deltas = {}
            invalid = []
            for field in fields:
                a, b = _number((left or {}).get(field)), _number((right or {}).get(field))
                if a is None or b is None:
                    deltas[field] = None
                else:
                    deltas[field] = b - a
                    if b < 0 or (field != "queued" and b < a and not process_changed):
                        invalid.append(field)
            matrix.append({"tenant_id": tenant, "lane": lane, "before": left, "after": right,
                           "delta": deltas, "missing_fields": missing, "invalid_fields": invalid})
    complete = [row for row in matrix if not row["missing_fields"] and not row["invalid_fields"]]
    scenarios = {"NORMAL": False, "QUEUE": False, "REJECT": False, "RESET": False}
    for row in complete:
        delta = row["delta"]
        scenarios["NORMAL"] |= bool((delta.get("accepted_total") or 0) > 0)
        scenarios["QUEUE"] |= bool((row["after"].get("queued") or 0) > 0 or (delta.get("wait_seconds_total") or 0) > 0)
        scenarios["REJECT"] |= bool((delta.get("rejected_total") or 0) > 0)
    samples = [before, *(suite.get("tenant_observability_samples") or []), after]
    valid_samples = [sample for sample in samples if sample.get("rows")]
    queue_timeline = []
    rejection_timeline = []
    process_segments = []
    previous_rows: dict[tuple[Any, Any], dict[str, Any]] = {}
    previous_process = None
    for sample_index, sample in enumerate(valid_samples):
        process = tuple(sample.get(field) for field in ("process_id", "process_started_at", "boot_id"))
        if process != previous_process:
            process_segments.append({"sample_index": sample_index, "process": process})
            previous_process = process
        current_rows = {(row.get("tenant_id"), row.get("lane")): row for row in sample.get("rows", [])}
        for key, row in current_rows.items():
            queued = _number(row.get("queued"))
            if queued is not None:
                queue_timeline.append({"sample_index": sample_index, "created_at": sample.get("created_at"),
                                       "tenant_id": key[0], "lane": key[1], "queued": queued})
                scenarios["QUEUE"] |= queued > 0
            prior = previous_rows.get(key)
            if prior:
                accepted_delta = (_number(row.get("accepted_total")) or 0) - (_number(prior.get("accepted_total")) or 0)
                wait_delta = (_number(row.get("wait_seconds_total")) or 0) - (_number(prior.get("wait_seconds_total")) or 0)
                rejected_delta = (_number(row.get("rejected_total")) or 0) - (_number(prior.get("rejected_total")) or 0)
                scenarios["NORMAL"] |= accepted_delta > 0
                scenarios["QUEUE"] |= wait_delta > 0
                scenarios["REJECT"] |= rejected_delta > 0
                if rejected_delta > 0:
                    rejection_timeline.append({"sample_index": sample_index, "created_at": sample.get("created_at"),
                                               "tenant_id": key[0], "lane": key[1],
                                               "rejected_delta": rejected_delta})
        previous_rows = current_rows
    counter_reset = any(
        (row["delta"].get(field) or 0) < 0
        for row in matrix
        for field in ("accepted_total", "completed_total", "failed_total",
                      "rejected_total", "wait_seconds_total", "exec_seconds_total")
    )
    scenarios["RESET"] = process_changed or counter_reset or len(process_segments) > 1
    expected_cells = len(tenants) * len(lanes)
    status = _status(expected=expected_cells, observed=len(complete), blocked=not tenants or not lanes)
    if status == "MEASURED" and not all(scenarios.values()):
        status = "PARTIAL"
    return {"status": status, "reason": "expected tenant x lane 来自实际配置；缺失值保持 null",
            "expected_tenants": tenants, "expected_lanes": lanes,
            "expected_cells": expected_cells, "complete_cells": len(complete),
            "duplicate_keys": after.get("duplicate_rows", []), "scenarios": scenarios,
            "process_segments": {
                "before": {field: before.get(field) for field in ("process_id", "process_started_at", "boot_id")},
                "after": {field: after.get(field) for field in ("process_id", "process_started_at", "boot_id")},
                "changed": process_changed,
                "counter_reset": counter_reset,
                "segments": process_segments,
            },
            "sample_count": len(valid_samples), "queue_timeline": queue_timeline,
            "rejection_timeline": rejection_timeline,
            "matrix": matrix}


def evaluate_observation(suite: dict[str, Any], profile: dict[str, Any],
                         m1_reports: list[dict[str, Any]] | None = None,
                         *, quick: bool = False,
                         selected_metrics: list[str] | None = None) -> dict[str, Any]:
    runs = {str(run.get("scenario")): run for run in suite.get("runs", [])}
    metrics = {
        "M1": summarize_m1(m1_reports or [], profile),
        "M2": summarize_m2(suite, profile, quick=quick),
        "M3": summarize_m3(runs, quick=quick),
        "M4": summarize_m4(runs, quick=quick),
        "M5": summarize_m5(suite, quick=quick),
        "M6": summarize_m6(suite, profile),
    }
    selected = set(selected_metrics or metrics)
    for code, metric in metrics.items():
        if code not in selected:
            metric["status"] = "BLOCKED"
            metric["reason"] = "本次命令未选择该指标"
        elif quick and metric["status"] == "MEASURED":
            metric["status"] = "PARTIAL"
            metric["reason"] = "quick 仅为非完整采样；" + str(metric.get("reason") or "")
    statuses = [metrics[code]["status"] for code in selected]
    overall = ("EXECUTION_ERROR" if "EXECUTION_ERROR" in statuses else
               "BLOCKED" if all(value == "BLOCKED" for value in statuses) else
               "MEASURED" if all(value == "MEASURED" for value in statuses) else "PARTIAL")
    issues = [
        {"category": "外部模型", "evidence": profile.get("model_preflight"),
         "note": "真实 LLM/embedding 预检结果"},
        {"category": "Search/Recall", "evidence": {
            "m1_windows": metrics["M1"].get("measured_windows"),
            "m4_windows": metrics["M4"].get("observed_windows")},
         "note": "错误、超时、空召回和质量分母见 M1/M4"},
        {"category": "Admission/调度", "evidence": {
            "m3": metrics["M3"]["status"], "m4": metrics["M4"]["status"]},
         "note": "客户端发送优先级不作为服务端出队顺序证据"},
        {"category": "Commit 恢复", "evidence": metrics["M5"].get("state_coverage"),
         "note": metrics["M5"].get("reason")},
        {"category": "原子引擎", "evidence": None,
         "note": "仅在 EchoMem HTTP/debug 或观测接口实际返回时归因"},
        {"category": "租户隔离", "evidence": {
            "expected": metrics["M2"].get("expected_cases"),
            "observed": metrics["M2"].get("observed_cases")},
         "note": "故障是否生效与旁观租户数据分开记录"},
        {"category": "可观测性", "evidence": metrics["M6"].get("scenarios"),
         "note": "缺字段、重复键、非法值和重启分段见 M6"},
        {"category": "测试平台/部署", "evidence": profile.get("resource_evidence"),
         "note": "4U8G cgroup、发送池和原始产物完整性"},
    ]
    return {"schema_version": 1, "assessment": "observation-only",
            "performance_thresholds_applied": False,
            "sampling_mode": "quick-non-complete" if quick else "full",
            "status": overall, "metrics": metrics,
            "selected_metrics": sorted(selected),
            "allowed_statuses": list(STATUSES),
            "issue_categories": issues,
            "raw_suite": "suite.json"}


def derive_observation_recommendations(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn measured symptoms into bounded, module-specific next actions."""
    metrics = result.get("metrics", {})
    m1, m2, m3, m4, m5, m6 = (metrics.get(f"M{i}", {}) for i in range(1, 7))
    m2_changes = [
        float(value) * 100
        for case in m2.get("cases", [])
        for value in (case.get("degradation_by_tenant") or {}).values()
        if _number(value) is not None
    ]
    m3_jain = [
        value for window in m3.get("windows", [])
        for value in (window.get("commit_throughput_jain"),
                      window.get("search_inverse_p95_jain"))
        if value is not None
    ]
    m4_ratios = [
        tenant.get("p95_ratio")
        for window in m4.get("windows", [])
        for tenant in window.get("tenants", [])
        if tenant.get("p95_ratio") is not None
    ]
    levels = m1.get("levels", [])
    highest = max((int(level.get("hot_users") or 0) for level in levels), default=None)
    return [
        {"priority": "P0", "module": "Admission 与容量保护", "metrics": "M1 / M4",
         "evidence": (f"最高已测热用户档 H={highest}；首个运行异常="
                      f"{m1.get('first_operational_anomaly') or '尚未观测'}。"),
         "action": "区分 Search 与 Commit 配额，拒绝时返回 tenant、lane、reason_code 和 retry_after；继续升档直到取得真实边界。"},
        {"priority": "P0", "module": "多租户调度", "metrics": "M3 / M4",
         "evidence": (f"已测 Jain 最低={min(m3_jain) if m3_jain else None}；"
                      f"Commit 洪泛下 Search P95 最大倍率={max(m4_ratios) if m4_ratios else None}。"),
         "action": "Commit 按租户轮询或 DRR，并限制单租户在途数；Search 使用独立 lane、worker 和 admission 预算。"},
        {"priority": "P0", "module": "路由与 Search 编排", "metrics": "M1 / M4",
         "evidence": f"M4 已取得 {m4.get('observed_windows', 0)}/{m4.get('expected_windows', 0)} 个配对窗口。",
         "action": "为 intent/router、embedding、fanout、merge 分别记录排队和执行耗时，并为确定性记忆查询提供有质量校验的快速路径。"},
        {"priority": "P1", "module": "原子引擎 Atomic Engine", "metrics": "M1 / M4 / M6",
         "evidence": "当前黑盒报告尚不能把端到端尾延迟单独归因到索引读取、向量检索或候选合并。",
         "action": "暴露 embedding、索引读取、候选合并的阶段耗时和队列；避免 Commit 建索引持有 Search 所需的全局锁。"},
        {"priority": "P1", "module": "租户故障隔离", "metrics": "M2",
         "evidence": (f"完整故障用例 {m2.get('complete_cases', 0)}/{m2.get('expected_cases', 0)}；"
                      f"旁观租户最差 P95 变化={max(m2_changes) if m2_changes else None}%。"),
         "action": "按租户隔离并发、重试和 provider 连接预算；慢依赖等待不能长期占用全局 Search permit。"},
        {"priority": "P1", "module": "Commit 持久化与恢复", "metrics": "M5",
         "evidence": f"完整恢复样本 {m5.get('complete_samples', 0)}/{m5.get('expected_samples', 0)}。",
         "action": "保证返回 202 前持久化任务、幂等键和 cursor；扩大到入队后、执行中、落 archive 前后的崩溃矩阵。"},
        {"priority": "P1", "module": "可观测性", "metrics": "M6",
         "evidence": (f"完整 tenant×lane 单元 {m6.get('complete_cells', 0)}/{m6.get('expected_cells', 0)}；"
                      f"场景覆盖={m6.get('scenarios', {})}。"),
         "action": "所有启用层统一输出 queued/wait/exec/rejected，并补 accepted/completed/failed、reason_code 与进程代际。"},
    ]


def write_observation_report(result: dict[str, Any], path: Path) -> None:
    def esc(value: Any) -> str:
        return html.escape("-" if value is None else str(value))

    def table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
        head = "".join(f"<th>{esc(label)}</th>" for _, label in columns)
        body = "".join("<tr>" + "".join(f"<td>{esc(row.get(key))}</td>" for key, _ in columns) + "</tr>" for row in rows)
        return f"<div class='scroll'><table><thead><tr>{head}</tr></thead><tbody>{body or '<tr><td colspan=\"9\">暂无数据</td></tr>'}</tbody></table></div>"

    def details(label: str, content: str) -> str:
        return f"<details><summary>{esc(label)}</summary>{content}</details>"

    def bars(title: str, points: list[tuple[str, float | None]], *, signed: bool = False) -> str:
        normalized = [(label, _number(value)) for label, value in points]
        maximum = max((abs(value) for _, value in normalized if value is not None), default=0) or 1
        body = "".join(
            f"<div class='bar'><span>{esc(label)}</span><i><b class='{'worse' if signed and value > 0 else 'better'}' style='width:{100 * abs(value) / maximum:.2f}%'></b></i><strong>{esc(round(value, 3))}</strong></div>"
            for label, value in normalized if value is not None
        )
        return f"<h3>{esc(title)}</h3>{body or '<p>暂无数据</p>'}"

    cards = "".join(
        f"<article><b>{code}</b><h2>{esc(METRIC_NAMES[code])}</h2>"
        f"<span class='{metric['status']}'>{metric['status']}</span>"
        f"<p>{esc(METRIC_PURPOSES[code])}</p><small>{esc(metric.get('reason'))}</small></article>"
        for code, metric in result["metrics"].items()
    )
    sections = []
    for code, metric in result["metrics"].items():
        visual = ""
        if code == "M1":
            visual = bars("已测负载曲线：Search P95 ms", [
                (f"{level.get('topology')} H={level.get('hot_users')} {level.get('load_mode')}",
                 (level.get("search") or {}).get("p95_s") * 1000
                 if (level.get("search") or {}).get("p95_s") is not None else None)
                for level in metric.get("levels", [])
            ])
            visual += details("查看各档吞吐与完成状态", table(metric.get("levels", []), [("topology", "拓扑"), ("hot_users", "热用户"), ("load_mode", "负载"), ("status", "数据状态"), ("sent_search_rps", "Search发送/s"), ("effective_search_rps", "Search完成/s")]))
            visual += details("查看 DAU 画像换算", table(metric.get("dau_scenarios", []), [("name", "DAU情景"), ("searches_per_user_day", "Search/日"), ("commits_per_user_day", "Commit/日"), ("peak_to_average_ratio", "峰均比"), ("traffic_equivalent_dau", "流量等价DAU"), ("is_measured_maximum", "实测最大值")]))
            resource_rows = [{"topology": level.get("topology"), "hot_users": level.get("hot_users"),
                              "load_mode": level.get("load_mode"), **sample}
                             for level in metric.get("levels", []) for sample in level.get("resources", [])]
            visual += details("查看 CPU、内存逐点采样", table(resource_rows, [("topology", "拓扑"), ("hot_users", "热用户"), ("load_mode", "负载"), ("at_epoch_s", "时间"), ("cpu_percent_one_core_100", "CPU% (100%=1核)"), ("rss_bytes", "RSS bytes"), ("phase", "阶段")]))
        elif code == "M2":
            points = []
            for case in metric.get("cases", []):
                values = [_number(value) for value in (case.get("degradation_by_tenant") or {}).values()]
                values = [value * 100 for value in values if value is not None]
                points.append((f"{case.get('fault_type')} · {case.get('target_tenant')} · #{case.get('repetition')}",
                               max(values) if values else None))
            visual = bars("各故障用例最差旁观租户 Search P95 变化 %", points, signed=True)
            visual += details("查看 24 个故障用例状态", table(metric.get("cases", []), [("target_tenant", "目标租户"), ("fault_type", "故障"), ("repetition", "重复"), ("fault_observed", "实际生效"), ("fault_recovered", "恢复")]))
        elif code == "M3":
            visual = bars("公平指数（越接近 1 越均匀）", [
                (f"{window.get('tenant_count')}租户 Commit Jain", window.get("commit_throughput_jain"))
                for window in metric.get("windows", [])
            ] + [
                (f"{window.get('tenant_count')}租户 Search Jain", window.get("search_inverse_p95_jain"))
                for window in metric.get("windows", [])
            ])
            visual += details("查看公平窗口汇总", table(metric.get("windows", []), [("scenario", "场景"), ("tenant_count", "租户"), ("duration_s", "窗口秒"), ("commit_throughput_jain", "Commit Jain"), ("search_inverse_p95_jain", "Search inverse-P95 Jain")]))
            tenant_rows = [{"scenario": window.get("scenario"), **tenant,
                            "search_p95_ms": tenant.get("search", {}).get("p95_ms")}
                           for window in metric.get("windows", []) for tenant in window.get("tenants", [])]
            visual += details("查看逐租户完成数与延迟", table(tenant_rows, [("scenario", "场景"), ("tenant_index", "租户"), ("commit_submitted", "提交"), ("commit_accepted", "受理"), ("commit_completed", "完成"), ("commit_failed", "失败"), ("commit_pending", "Pending"), ("longest_no_completion_s", "最长无服务秒"), ("search_p95_ms", "Search P95 ms")]))
        elif code == "M4":
            visual = bars("Commit 积压重叠窗口 Search P95 / ms", [
                (str(window.get("scenario")), (window.get("overlap") or {}).get("p95_ms"))
                for window in metric.get("windows", [])
            ])
            visual += details("查看 Commit 受理、完成与积压", table(metric.get("windows", []), [("scenario", "场景"), ("commit_planned_or_recorded", "Commit 计划/记录"), ("commit_accepted_202", "202"), ("commit_rejected", "拒绝"), ("commit_completed", "完成"), ("commit_pending", "Pending"), ("overlap_intervals", "Overlap 区间")]))
            tenant_rows = [{"scenario": window.get("scenario"), **tenant,
                            "baseline_p95_ms": tenant.get("baseline", {}).get("p95_ms"),
                            "overlap_p95_ms": tenant.get("overlap", {}).get("p95_ms")}
                           for window in metric.get("windows", []) for tenant in window.get("tenants", [])]
            visual += details("查看逐租户基线与洪泛对比", table(tenant_rows, [("scenario", "场景"), ("tenant_index", "租户"), ("baseline_p95_ms", "Baseline P95"), ("overlap_p95_ms", "Overlap P95"), ("p95_delta_ms", "差值"), ("p95_ratio", "比值")]))
        elif code == "M5":
            visual = bars("完整恢复样本", [("完成 / 期望", 100 * (metric.get("complete_samples") or 0) / max(1, metric.get("expected_samples") or 0))])
            visual += details("查看每次 kill-9 恢复检查", table(metric.get("samples", []), [("sample_index", "样本"), ("received_202", "收到202"), ("unfinished_at_kill", "崩溃时未完成"), ("autonomous_completed", "自主完成"), ("fully_reconciled", "完整对账"), ("recovery_elapsed_s", "恢复秒")]))
        elif code == "M6":
            visual = bars("四元组完整率与行为覆盖率 / %", [
                ("tenant×lane 完整率", 100 * (metric.get("complete_cells") or 0) / max(1, metric.get("expected_cells") or 0)),
                ("NORMAL/QUEUE/REJECT/RESET", 25 * sum(value is True for value in metric.get("scenarios", {}).values())),
            ])
            visual += details("查看四种行为覆盖", table([{"scenario": name, "observed": observed} for name, observed in metric.get("scenarios", {}).items()], [("scenario", "行为"), ("observed", "已观测")]))
            visual += details("查看逐租户逐 Lane 完整性", table(metric.get("matrix", []), [("tenant_id", "租户"), ("lane", "Lane"), ("missing_fields", "缺失"), ("invalid_fields", "非法")]))
        sections.append(f"<section><h2>{code} {esc(METRIC_NAMES[code])}</h2><p class='purpose'>{esc(METRIC_PURPOSES[code])}</p>{visual}{details('原始汇总与完整分母', '<pre>' + esc(json.dumps(metric, ensure_ascii=False, indent=2)) + '</pre>')}</section>")
    recommendations = derive_observation_recommendations(result)
    recommendation_table = table(recommendations, [
        ("priority", "优先级"), ("module", "EchoMem 模块"),
        ("metrics", "关联指标"), ("evidence", "本次证据"),
        ("action", "改进建议"),
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>EchoMem 4U8G 六项黑盒观测</title><style>
body{margin:0;color:#18242b;background:#f4f7f8;font:14px/1.6 system-ui;letter-spacing:0}main{max-width:1320px;margin:auto;padding:24px}h1{font-size:28px}h2{font-size:20px}.lead{border-left:4px solid #17746a;padding:10px 14px;background:#fff}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:18px 0}.cards article{background:#fff;border:1px solid #d5dfe3;padding:14px;border-radius:4px}.cards h2{font-size:16px;margin:6px 0}.cards small{display:block;color:#60727a}.MEASURED{color:#08745d}.PARTIAL{color:#946200}.BLOCKED,.EXECUTION_ERROR{color:#b1372e}section{background:#fff;border-top:1px solid #cbd6da;padding:18px;margin-top:12px}.purpose{color:#40565f;font-size:15px}.scroll{overflow:auto}table{width:100%;border-collapse:collapse}th,td{text-align:left;vertical-align:top;padding:8px;border-bottom:1px solid #dde4e7}th{background:#edf2f4;white-space:nowrap}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f4f5;padding:12px}details{border-top:1px solid #e0e6e8;margin-top:12px;padding-top:8px}summary{cursor:pointer;color:#176d75;font-weight:650}.bar{display:grid;grid-template-columns:260px minmax(120px,1fr) 90px;gap:10px;align-items:center;margin:7px 0}.bar i{display:block;height:12px;background:#e0e7e9}.bar i b{display:block;height:100%;background:#17746a}.bar i b.worse{background:#c05a45}.bar i b.better{background:#278575}.bar strong{text-align:right}@media(max-width:760px){.cards{grid-template-columns:1fr}main{padding:12px}.bar{grid-template-columns:1fr}.bar strong{text-align:left}}</style></head><body><main>""" +
        f"<h1>EchoMem 4U8G 六项黑盒观测</h1><div class='lead'><b>结论先行：{esc(result['status'])}</b><p>这是观测报告，不是性能准入验收；没有 P95、准确率、Jain、吞吐或劣化比例 PASS/FAIL 门槛。错误、超时、空召回与 pending/failed Commit 均保留在分母。采样模式：{esc(result['sampling_mode'])}。</p></div><div class='cards'>{cards}</div>" +
        "<section><h2>EchoMem 模块改进建议</h2><p class='purpose'>建议只由本轮可见证据推导；无法从黑盒区分的阶段明确写为需补观测，不把端到端延迟武断归因给原子引擎。</p>" + recommendation_table + details("查看责任边界与技术证据", table(result.get("issue_categories", []), [("category", "类别"), ("note", "观测/下一步"), ("evidence", "证据")])) + "</section>" +
        "".join(sections) + "<section><h2>原始产物</h2><p><a href='summary.json'>summary.json</a> · <a href='suite.json'>suite.json</a> · <a href='records.csv'>records.csv</a> · <a href='metrics_samples.csv'>metrics_samples.csv</a> · <a href='execution-manifest.json'>execution-manifest.json</a></p></section></main></body></html>", encoding="utf-8")
