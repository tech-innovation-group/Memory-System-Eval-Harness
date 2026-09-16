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

from performance.targets.echomem.acceptance.stage_observability import (
    correlate_requests,
    cross_check,
    read_stage_events,
    summarize_cache_diagnostics,
    summarize_log_stages,
    summarize_http_calls,
    summarize_prometheus_histograms,
)

from performance.stats import percentile
from performance.targets.echomem.acceptance.provenance import render_platform_provenance

STATUSES = ("MEASURED", "PARTIAL", "BLOCKED", "EXECUTION_ERROR")
METRIC_ORDER = ("M1", "M2", "M3", "M4", "M5", "M6")
METRIC_NAMES = {
    "M1": "单实例热用户和 DAU",
    "M2": "多租户公平性",
    "M3": "Commit 洪泛下 Search 性能",
    "M4": "单租户故障隔离",
    "M5": "202 Commit 的 kill-9 恢复",
    "M6": "每层每租户四元组",
}
METRIC_PURPOSES = {
    "M1": "回答当前单实例实际承载多少热用户，以及不同业务画像下的流量等价 DAU。",
    "M2": "检查同档位租户是否获得接近等权的 Commit 吞吐和 Search 响应机会。",
    "M3": "检查 Commit 洪泛期间，交互式 Search 的延迟和服务可用性是否仍受保护；事实命中率仅作诊断。",
    "M4": "观察一个租户失败或变慢时，其他租户的 Search 尾延迟和错误是否被拖累。",
    "M5": "验证已返回 202 的 Commit 在 kill-9 后能否自主恢复，并保持消息、顺序和幂等一致。",
    "M6": "验证每个租户、每个处理层都能观测排队、等待、执行和拒绝四类数据。",
}
METRIC_METHODS = {
    "M1": "按跨租户和租户内两种拓扑逐档增加热用户，分别运行 Search、Commit、混合和热点负载，记录吞吐、延迟、错误、积压、CPU 与内存。",
    "M2": "分别用 4/8 个独立租户，每租户 Search 1 次/秒；预热 30 秒后每租户每 30 秒启动一次 open→add×4→Commit→轮询。第 300 秒停压，最多再观察 180 秒。只统计 [30,300) 秒内的完成吞吐与该窗口发起的 Search 延迟；窗口外排空另列。快速模式使用更短周期，仅验证采集链路。",
    "M3": "先测四租户已预注入记忆的 Search 基线，再分别制造均匀和单租户 Commit 洪泛。按同一 tenant/session/archive 对账受理与轮询；Search 开始时刻落在受理至最后成功非终态轮询之间才计入确认重叠。宽观察窗口另列，未测到的内部调度顺序不作结论。",
    "M4": "四个目标租户依次注入 reject 和 delay，分别采集故障前、故障中、恢复后数据，对比三个旁观租户的 Search P95 与错误。",
    "M5": "Commit 返回 202 且仍未完成时 kill -9 专用容器，重启后只轮询原任务，再对账消息集合、顺序、cursor、archive 和幂等重试。",
    "M6": "负载前、中、后持续读取受保护观测接口，按实际配置逐帧枚举 tenant×lane；空帧、非法值、重复行和采样空档均保留。进程重启按身份分段，计数回退不能单独证明RESET；检查四元组及NORMAL/QUEUE/REJECT/RESET。",
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
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
    latencies = [value for row in selected if (value := _number(row.get("stage_ms"))) is not None and value >= 0]
    ok = [row for row in selected if row.get("status") == "ok"]
    recall_observed = any(row.get("recall_served") not in (None, "") for row in selected)
    served = ([row for row in ok if _truth(row.get("recall_served"))]
              if recall_observed else list(ok))
    served_latencies = [value for row in served
                        if (value := _number(row.get("stage_ms"))) is not None and value >= 0]
    quality_observed = [row for row in selected if str(row.get("quality_ok", "")).lower() in {"true", "false"}]
    quality_ok = [row for row in quality_observed if row.get("status") == "ok"
                  and _truth(row.get("quality_ok")) and not _truth(row.get("degraded"))]
    # Empty Recall is only classifiable when the transport recorded a numeric
    # hit count. Legacy records may omit both ``hit_count`` and
    # ``recall_served``; treating those missing fields as zero created false
    # baseline-health failures in M3 evidence summaries.
    known_hit_counts = [
        row for row in selected
        if _number(row.get("hit_count")) is not None
    ]
    return {
        "planned_or_recorded": len(selected),
        "completed": len(selected),
        "ok": len(ok),
        "errors": len(selected) - len(ok),
        "recall_served": len(served),
        "recall_service_errors": len(selected) - len(served),
        "recall_service_rate": len(served) / len(selected) if selected else None,
        "timeouts": sum("timeout" in str(row.get("error_type") or "").lower() for row in selected),
        "p50_ms": percentile(latencies, 50),
        "mean_ms": sum(latencies) / len(latencies) if latencies else None,
        "p95_ms": percentile(latencies, 95),
        "p99_ms": percentile(latencies, 99),
        "served_p50_ms": percentile(served_latencies, 50),
        "served_p95_ms": percentile(served_latencies, 95),
        "served_p99_ms": percentile(served_latencies, 99),
        "latency_observations": len(latencies),
        "latency_missing_or_invalid": len(selected) - len(latencies),
        "quality_observed": len(quality_observed),
        "quality_missing": len(selected) - len(quality_observed),
        "recall_queries": sum(row.get("query_type") == "recall" for row in selected),
        "quality_ok": len(quality_ok),
        "quality_rate": len(quality_ok) / len(selected) if selected else None,
        "empty_recall": sum(row.get("status") == "ok"
                            and _number(row.get("hit_count")) == 0
                            for row in known_hit_counts),
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


def summarize_api_coverage(suite: dict[str, Any]) -> dict[str, Any]:
    """Build a call ledger without inventing unobserved requests."""
    rows = [row for run in suite.get("runs", []) for row in _records(run)]
    operations = {
        "search": ("POST", "/api/retrieval/search", [row for row in rows if row.get("op") == "read"]),
        "session_open": ("POST", "/api/sessions/open", [row for row in rows if row.get("op") == "open"]),
        "message_add": ("POST", "/api/sessions/{session}/messages", [row for row in rows if row.get("op") == "add"]),
        "commit_submit": ("POST", "/api/sessions/{session}/commit", [row for row in rows if row.get("op") == "commit_submit"]),
        "commit_status": ("GET", "/api/sessions/{session}/commits/{archive}", [row for row in rows if row.get("op") == "commit_done"]),
    }
    ledger = []
    for name, (method, path, selected) in operations.items():
        calls = (sum(int(_number(row.get("poll_count")) or 0) for row in selected)
                 if name == "commit_status" else len(selected))
        ledger.append({
            "operation": name, "method": method, "path": path,
            "calls": calls, "records": len(selected), "calls_exact": True,
            "ok": sum(row.get("status") == "ok" for row in selected),
            "errors": sum(row.get("status") != "ok" for row in selected),
            "status": "COVERED" if calls else "NOT_COVERED", "evidence": "records.csv",
        })
    contract = suite.get("blackbox_contract_probe") or {}
    contract_checks = contract.get("checks", [])
    contract_paths = {
        "history": ("GET", "/api/sessions/{session}/history"),
        "archives": ("GET", "/api/sessions/{session}/archives"),
        "commit_memories": ("GET", "/api/sessions/{session}/commits/{archive}/memories"),
        "commit_cursor": ("GET", "/fs/read?uri=echo://.../commit_cursor.json"),
    }
    for operation, (method, path) in contract_paths.items():
        matches = [row for row in contract_checks if row.get("name") == operation]
        ledger.append({
            "operation": operation, "method": method, "path": path,
            "calls": len(matches), "records": len(matches), "calls_exact": True,
            "ok": sum(row.get("status") == "PASS" for row in matches),
            "errors": sum(row.get("status") == "FAIL" for row in matches),
            "status": "COVERED" if matches else "NOT_COVERED",
            "evidence": "blackbox-contract-probe.json",
        })
    readiness = suite.get("readiness") or (suite.get("resource_preflight") or {}).get("readiness") or {}
    readiness_checks = readiness.get("checks", [])
    ready_records = [row for row in readiness_checks if row.get("name") == "ready"]
    ready_calls = sum(row.get("http_status") is not None for row in ready_records)
    ready_ok = sum(row.get("http_status") == 200 and row.get("status") == "PASS"
                   for row in ready_records)
    ledger.append({"operation": "system_ready", "method": "GET", "path": "/api/v1/system/ready",
                   "calls": ready_calls, "records": ready_calls, "calls_exact": False,
                   "ok": ready_ok, "errors": ready_calls - ready_ok,
                   "status": "COVERED" if ready_calls else "NOT_COVERED",
                   "evidence": "suite.json readiness (minimum observed calls)"})
    metric_calls = metric_rows = 0
    metric_paths = {Path(str(run.get("output_dir") or "")) / "metrics_samples.csv"
                    for run in suite.get("runs", [])}
    for path in metric_paths:
        if path.is_file():
            with path.open(encoding="utf-8", newline="") as handle:
                stamps = set()
                for row in csv.DictReader(handle):
                    metric_rows += 1
                    stamp = _number(row.get("ts"))
                    if stamp is not None:
                        stamps.add(stamp)
                metric_calls += len(stamps)
    ledger.append({"operation": "metrics", "method": "GET", "path": "/metrics",
                   "calls": metric_calls, "records": metric_rows, "calls_exact": False,
                   "ok": metric_calls, "errors": None,
                   "status": "COVERED" if metric_calls else "NOT_COVERED",
                   "evidence": "metrics_samples.csv distinct frame timestamps; minimum successful scrapes, not metric rows"})
    fault_cases = (suite.get("fault_isolation") or {}).get("cases", [])
    ledger.append({"operation": "fault_control", "method": "GET/POST",
                   "path": "/api/inspect/test-control/fault", "calls": len(fault_cases) * 2,
                   "records": len(fault_cases), "ok": sum(bool(row.get("checks")) for row in fault_cases),
                   "errors": 0, "calls_exact": False,
                   "status": "COVERED" if fault_cases else "NOT_COVERED",
                   "evidence": "fault-isolation-*.json"})
    samples = suite.get("tenant_observability_samples") or []
    ledger.append({"operation": "tenant_observability", "method": "GET",
                   "path": "/api/inspect/tenant-observability", "calls": len(samples),
                   "records": len(samples), "ok": sum(bool(row.get("rows")) for row in samples),
                   "errors": sum(not bool(row.get("rows")) for row in samples),
                   "calls_exact": True,
                   "status": "COVERED" if samples else "NOT_COVERED",
                   "evidence": "tenant-observability-samples.json"})
    invalid_payload = suite.get("invalid_input") or {}
    invalid_detail = _probe_detail(invalid_payload, "invalid-input")
    stage_evidence = suite.get("stage_observability") or {}
    events = stage_evidence.get("events") or read_stage_events(Path(str(stage_evidence.get("path") or "")))
    return {
        "scope": "六项压测运行期全部必需接口；不包含与六项指标无关的 EchoMem 产品 API",
        "covered": sum(row["status"] == "COVERED" for row in ledger),
        "expected": len(ledger), "operations": ledger,
        "server_observed_endpoints": summarize_http_calls(events),
        "invalid_input": ({"status": next((row.get("status") for row in invalid_payload.get("checks", [])
                                            if row.get("name") == "invalid-input"), "PARTIAL"),
                           **invalid_detail}
                          if invalid_payload else {
            "status": "NOT_COVERED",
            "reason": "本轮没有运行非法 JSON、缺字段、越界参数、错误凭证和不存在资源的负向契约探针",
        }),
    }


def _m1_trace_records(suite: dict[str, Any]) -> tuple[list[dict], list[str]]:
    rows, missing = [], []
    seen = set()
    for reference in (suite.get("m1") or {}).get("reports", []):
        report_path = Path(str(reference.get("path") or ""))
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            for level in report.get("levels", []):
                name = str(level.get("measurement_file") or "")
                if not name or Path(name).name != name:
                    missing.append(f"{reference.get('topology')}:measurement_file_missing_or_invalid")
                    continue
                path = report_path.parent / name
                if path in seen:
                    continue
                seen.add(path)
                try:
                    measurement = json.loads(path.read_text(encoding="utf-8"))
                    samples = measurement.get("rows")
                    if not isinstance(samples, list):
                        raise ValueError("measurement rows missing")
                    rows.extend(row for row in samples if isinstance(row, dict) and row.get("sent") is not False)
                except (OSError, ValueError, AttributeError):
                    missing.append(f"{reference.get('topology')}:{name}")
        except (OSError, ValueError, AttributeError):
            missing.append(f"{reference.get('topology')}:report_unreadable")
    return rows, missing


def _m1_timing_rows(m1_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Read the capacity runner's own elapsed/drain measurements.

    M1 is executed outside the regular case runner, so its timing evidence is
    stored next to each level measurement rather than in ``suite.json``.
    """
    rows: list[dict[str, Any]] = []
    for report in m1_reports:
        report_path = Path(str(report.get("manifest", {}).get("report_path") or ""))
        if not report_path.is_file():
            # The observation runner does not currently put report_path in the
            # JSON; callers pass report objects loaded from the known output
            # location, so fall back to the level's relative evidence path.
            report_path = Path(str(report.get("_report_path") or ""))
        for level in report.get("levels", []):
            name = str(level.get("measurement_file") or "")
            measurement = {}
            if name and Path(name).name == name:
                candidate = report_path.parent / name if report_path.is_file() else None
                if candidate and candidate.is_file():
                    try:
                        measurement = json.loads(candidate.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        measurement = {}
            duration = _number(measurement.get("duration_s"))
            elapsed = _number(measurement.get("elapsed_with_drain_s"))
            level_rows = measurement.get("rows") if isinstance(measurement, dict) else []
            level_rows = level_rows if isinstance(level_rows, list) else []
            sent = [row for row in level_rows if isinstance(row, dict) and row.get("sent") is not False]
            rows.append({
                "scenario": f"M1/{report.get('topology')}/H={level.get('hot_users')}/{level.get('load_mode')}",
                "status": level.get("status"),
                "target_concurrency": level.get("target_concurrency"),
                "peak_inflight_requests": (level.get("search") or {}).get("peak_inflight_requests"),
                "memory_profile": (level.get("server_stage_timings") or {}).get("memory_profile"),
                "planned_load_s": duration,
                "actual_elapsed_s": elapsed,
                "drain_s": max(0.0, elapsed - duration) if duration is not None and elapsed is not None else None,
                "requests": len(sent),
                "search_requests": sum(row.get("op") == "read" for row in sent),
                "commit_requests": sum(row.get("op") in {"add", "commit_submit", "commit_done"} for row in sent),
                "source": "M1 measurement JSON",
            })
    return rows


def _scenario_timing_rows(suite: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run in suite.get("runs", []):
        summary = run.get("summary") or {}
        clock = summary.get("run_clock") or {}
        records = _records(run)
        planned = _number(clock.get("load_duration_s"))
        actual = _number(clock.get("wall_elapsed_s"))
        engine = _number(clock.get("engine_elapsed_s"))
        if actual is None:
            actual = _number(run.get("wall_elapsed_s"))
        if engine is None:
            engine = _number(run.get("engine_elapsed_s"))
        rows.append({
            "scenario": run.get("scenario"),
            "status": run.get("status"),
            "planned_load_s": planned,
            "actual_elapsed_s": actual,
            "engine_elapsed_s": engine,
            "drain_s": max(0.0, actual - planned) if actual is not None and planned is not None else None,
            "requests": len(records),
            "search_requests": sum(row.get("op") == "read" for row in records),
            "commit_requests": sum(row.get("op") in {"open", "add", "commit_submit", "commit_done"} for row in records),
            "source": "case summary/run_clock",
        })
    return rows


def summarize_timing_evidence(suite: dict[str, Any], m1_reports: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for run in suite.get("runs", []) for row in _records(run)]
    operation_rows = []
    for operation in ("read", "open", "add", "commit_submit", "commit_done"):
        values = [value for row in rows if row.get("op") == operation
                  and (value := _number(row.get("stage_ms"))) is not None and value >= 0]
        label = "Commit受理至终态" if operation == "commit_done" else "HTTP端到端"
        operation_rows.append({"module": f"{label}/{operation}", "source": "客户端计时",
                               "observations": len(values),
                               "p50_ms": percentile(values, 50) if values else None,
                               "p95_ms": percentile(values, 95) if values else None,
                               "p99_ms": percentile(values, 99) if values else None})
    response_rows = []
    for report in m1_reports:
        for level in report.get("levels", []):
            search = level.get("search") or {}
            prefix = f"M1/{level.get('topology')}/H={level.get('hot_users')}/{level.get('load_mode')}"
            for engine, values in (search.get("engine_timings") or {}).items():
                response_rows.append({"module": f"{prefix}/engine:{engine}", "source": "服务响应", **values})
            for route, values in (search.get("route_path_timings") or {}).items():
                response_rows.append({"module": f"{prefix}/route:{route}", "source": "服务响应", **values})
            for stage, values in (level.get("server_stage_timings") or {}).items():
                if stage == "prometheus_histograms":
                    for row in values if isinstance(values, list) else []:
                        response_rows.append({
                            "module": f"{prefix}/prometheus:{row.get('module')}",
                            "source": row.get("source", "Prometheus histogram delta"),
                            "observations": row.get("observations"),
                            "mean_s": row.get("mean_s"),
                            "p50_s": row.get("p50_s"),
                            "p95_s": row.get("p95_s"),
                            "p99_s": row.get("p99_s"),
                        })
                    continue
                if not isinstance(values, dict):
                    continue
                response_rows.append({
                    "module": f"{prefix}/stage:{stage}",
                    "source": values.get("source", "Prometheus histogram delta"),
                    "observations": values.get("observations"),
                    "mean_s": values.get("mean_s"),
                    "p50_s": values.get("p50_s"),
                    "p95_s": values.get("p95_s"),
                    "p99_s": values.get("p99_s"),
                })
    stage_evidence = suite.get("stage_observability") or {}
    events = stage_evidence.get("events") or read_stage_events(
        Path(str(stage_evidence.get("path") or ""))
    )
    log_rows = summarize_log_stages(events)
    metric_paths = [
        Path(str(run.get("output_dir") or "")) / "metrics_samples.csv"
        for run in suite.get("runs", [])
    ]
    metric_rows = summarize_prometheus_histograms(metric_paths)
    cache_diagnostics = summarize_cache_diagnostics(events)
    scenario_timings = _scenario_timing_rows(suite)
    m1_timings = _m1_timing_rows(m1_reports)
    timing_rows = [*scenario_timings, *m1_timings]
    elapsed_values = [value for row in timing_rows
                      if (value := _number(row.get("actual_elapsed_s"))) is not None]
    planned_values = [value for row in timing_rows
                      if (value := _number(row.get("planned_load_s"))) is not None]
    drain_values = [value for row in timing_rows
                    if (value := _number(row.get("drain_s"))) is not None]
    expected = [
        "recall/rule", "recall/semantic", "recall/llm", "recall/query_embedding",
        "recall/memory_profile", "recall/engine_execution",
        "recall/candidate_governance_filter", "recall/rerank", "recall/composer",
        "recall/recall_total", "commit/memory_extraction",
        "atomic/extraction", "atomic/contradiction_resolution",
        "atomic/organized_projection", "atomic/atom_persistence",
        "atomic/atom_vector_publication", "atomic/operation_publication",
        "atomic/cursor_advance",
    ]
    observed = {str(row.get("module")) for row in log_rows if row.get("observations")}
    for row in metric_rows:
        if not row.get("observations"):
            continue
        metric = row.get("metric")
        if metric == "echomem_memrouter_stage_duration_seconds":
            stage = (row.get("labels") or {}).get("stage")
            if stage:
                observed.add(f"recall/{stage}")
        elif metric == "echomem_recall_duration_seconds":
            observed.add("recall/recall_total")
        elif metric == "echomem_router_embedding_duration_seconds":
            observed.add("recall/query_embedding")
    missing = [name for name in expected if not any(
        module == name or module.startswith(name + "/") for module in observed
    )]
    m1_rows, missing_m1 = _m1_trace_records(suite)
    correlation = correlate_requests([*rows, *m1_rows], events)
    correlation["missing_m1_evidence_files"] = missing_m1
    if missing_m1:
        correlation["status"] = "PARTIAL"
    return {
        "operation_timings": operation_rows,
        "scenario_timings": timing_rows,
        "timing_totals": {
            "scenario_count": len(timing_rows),
            "planned_load_s": sum(planned_values) if planned_values else None,
            "actual_elapsed_s": sum(elapsed_values) if elapsed_values else None,
            "drain_s": sum(drain_values) if drain_values else None,
            "note": "M1 使用 measurement JSON；M2/M3 使用每个 case 的 run_clock。种子、预检和报告生成时间另列。",
        },
        "module_timings": [*response_rows, *log_rows],
        "structured_log_timings": log_rows,
        "prometheus_timings": metric_rows,
        "cache_diagnostics": cache_diagnostics,
        "trace_correlation": correlation,
        "cross_check": cross_check(log_rows, metric_rows),
        "stage_collection": {key: value for key, value in stage_evidence.items() if key != "events"},
        "unobservable_modules": missing,
        "missing_stage_reasons": [
            {"module": name, "reason": (
                "结构化日志采集失败或未配置容器，且没有对应阶段的真实耗时指标样本"
                if stage_evidence.get("status") not in {"COLLECTED", "PARTIAL"}
                else "本轮未采到该阶段的真实日志或对应耗时指标样本；需检查是否触发该阶段及采样覆盖"
            )}
            for name in missing
        ],
        "note": "端到端、结构化日志和 Prometheus 是独立证据；不通过端到端耗时相减推定模块耗时。",
    }


def summarize_concurrency_configuration(profile: dict[str, Any]) -> dict[str, Any]:
    """Record service concurrency knobs without using them to reduce client load."""
    target = int(profile.get("required_concurrency") or 0)
    path = Path(str(profile.get("preflight_config") or ""))
    relevant = {
        "max_workers", "max_concurrency", "queue_capacity", "max_queued_per_tenant",
        "queue_max", "max_inflight", "max_in_flight", "max_pending",
        "worker_count", "workers", "executor_workers", "gate_workers",
        "tenant_inflight_max", "admission_permits", "qps",
        "llm_max_concurrent", "embed_max_concurrent",
        "recall_llm_max_concurrent", "recall_embed_max_concurrent",
        "provider_budget_llm", "provider_budget_embed",
        "max_cached_tenants", "active_overshoot", "hard_cap",
        "deadline_s", "timeout_seconds",
    }
    concurrency_keys = {
        "max_workers", "max_concurrency", "queue_capacity", "max_queued_per_tenant",
        "queue_max", "max_inflight", "max_in_flight", "max_pending",
        "worker_count", "workers", "executor_workers", "gate_workers",
        "tenant_inflight_max", "admission_permits",
    }
    rows: list[dict[str, Any]] = []
    if path.is_file():
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            document = None

        def visit(value: Any, parts: tuple[str, ...] = ()) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    current = (*parts, str(key))
                    if key in relevant and isinstance(child, (int, float)) and not isinstance(child, bool):
                        disabled_or_unbounded = key == "max_inflight" and child == 0
                        rows.append({
                            "config_path": ".".join(current),
                            "value": child,
                            "comparison_applies": key in concurrency_keys,
                            "disabled_or_unbounded": disabled_or_unbounded,
                            "below_requested_concurrency": bool(
                                target and key in concurrency_keys and not disabled_or_unbounded and child < target
                            ),
                        })
                    visit(child, current)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, (*parts, str(index)))

        visit(document)
    return {
        "required_client_concurrency": target or None,
        "client_load_auto_capped_by_service_config": False,
        "service_limits_are_test_outcomes": True,
        "config_source": str(path) if path else "",
        "observed_service_limits": rows,
        "limits_below_target": sum(row["below_requested_concurrency"] for row in rows),
        "note": (
            "测试平台不会读取 EchoMem 并发/队列上限后自动降载；这些值属于被测系统。"
            f"若 {target or '目标'} 级客户端负载触发拒绝或排队，应作为容量结果保留，而不是改小分母。"
        ),
    }


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
    required_concurrency = int(profile.get("required_concurrency") or 0)
    peak_inflight = max((int((level.get("search") or {}).get("peak_inflight_requests") or 0)
                         for level in levels), default=0)
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
    status = _status(expected=requested, observed=len(measured), blocked=not reports)
    if status == "MEASURED" and required_concurrency and peak_inflight < required_concurrency:
        status = "PARTIAL"
    concurrency_levels = [
        level for level in levels
        if level.get("topology") == "concurrency" and level.get("target_concurrency") is not None
    ]
    concurrency_rows = []
    for level in concurrency_levels:
        search = level.get("search") or {}
        stage = (level.get("server_stage_timings") or {}).get("memory_profile") or {}
        concurrency_rows.append({
            "target_concurrency": level.get("target_concurrency"),
            "peak_inflight_requests": search.get("peak_inflight_requests"),
            "planned_search": search.get("planned"),
            "search_sent": search.get("sent"),
            "search_p50_s": search.get("p50_s"),
            "search_p95_s": search.get("p95_s"),
            "search_p99_s": search.get("p99_s"),
            "search_errors": search.get("errors"),
            "search_http_status_counts": search.get("http_status_counts"),
            "quality_rate": search.get("quality_rate"),
            "memory_profile_observations": stage.get("observations", 0),
            "memory_profile_mean_s": stage.get("mean_s"),
            "memory_profile_p50_s": stage.get("p50_s"),
            "memory_profile_p95_s": stage.get("p95_s"),
            "memory_profile_p99_s": stage.get("p99_s"),
            "memory_profile_sampled": stage.get("sampled", False),
        })
    by_target = {row["target_concurrency"]: row for row in concurrency_rows}
    row16 = by_target.get(16) or {}
    row64 = by_target.get(64) or {}
    p95_16 = row16.get("memory_profile_p95_s")
    p95_64 = row64.get("memory_profile_p95_s")
    memory_profile_comparison = {
        "baseline_concurrency": 16 if row16 else None,
        "comparison_concurrency": 64 if row64 else None,
        "p95_16_s": p95_16,
        "p95_64_s": p95_64,
        "p95_amplification": (p95_64 / p95_16 if p95_16 and p95_64 is not None else None),
        "comparison_ready": bool(p95_16 is not None and p95_64 is not None),
        "reason": (
            "同一轮 16/64 档位均有 memory_profile Prometheus 增量样本"
            if p95_16 is not None and p95_64 is not None
            else "需要两个档位都采到 memory_profile 直方图样本；端到端耗时不能替代阶段数据"
        ),
    }
    seed_rows = []
    seed_statuses = []
    seed_documents = 0
    seed_characters = 0
    for report in reports:
        seed = report.get("seed") or {}
        if seed.get("status"):
            seed_statuses.append(seed.get("status"))
        seed_documents += int(seed.get("total_documents") or 0)
        seed_characters += int(seed.get("total_input_characters") or 0)
        for actor in seed.get("actors") or []:
            if not isinstance(actor, dict):
                continue
            source = actor.get("corpus_source") or {}
            seed_rows.append({
                "tenant_index": actor.get("tenant_index"),
                "user_index": actor.get("user_index"),
                "sample_id": source.get("assigned_sample_id") or source.get("sample_id"),
                "session_key": source.get("assigned_session_key") or source.get("session_key"),
                "session_messages": source.get("session_messages"),
                "input_documents": actor.get("input_documents"),
                "input_characters": actor.get("input_characters"),
                "semantic_queries": actor.get("semantic_queries"),
                "commit_http_status": actor.get("commit_http_status"),
                "commit_state": actor.get("commit_state"),
                "status": actor.get("status"),
                "elapsed_s": actor.get("elapsed_s"),
            })
    # Older evidence files may contain only per-actor seed rows. Recover the
    # totals from those rows so a report update does not erase valid evidence.
    if seed_documents == 0:
        seed_documents = sum(int(row.get("input_documents") or 0) for row in seed_rows)
    if seed_characters == 0:
        seed_characters = sum(int(row.get("input_characters") or 0) for row in seed_rows)
    return {
        "status": status,
        "reason": "容量与 DAU 仅作观测和情景换算，不使用性能门槛",
        "resource_evidence": profile.get("resource_evidence"),
        "highest_measured_hot_users": highest,
        "required_concurrency": required_concurrency or None,
        "peak_inflight_requests": peak_inflight,
        "concurrency_target_observed": (
            peak_inflight >= required_concurrency if required_concurrency else None
        ),
        "first_operational_anomaly": boundary[0] if boundary else None,
        "unmeasured_ranges": [r.get("levels_requested", []) for r in reports if len(r.get("levels", [])) < len(r.get("levels_requested", []))],
        "levels": levels,
        "concurrency_rows": concurrency_rows,
        "memory_profile_comparison": memory_profile_comparison,
        "seed_contract": "每租户一个完整 LoCoMo session；Search 只取该 session 的真实 QA 子集",
        "seed_memory_policy": next((report.get("seed_memory_policy") for report in reports
                                     if report.get("seed_memory_policy")), None),
        "seed_status": ("PASS" if seed_statuses and all(value == "PASS" for value in seed_statuses)
                         else seed_statuses[0] if seed_statuses else "NOT_MEASURED"),
        "seed_session_count": len(seed_rows),
        "seed_total_documents": seed_documents,
        "seed_total_input_characters": seed_characters,
        "seed_assignments": seed_rows,
        "dau_scenarios": estimates,
        "expected_windows": requested,
        "measured_windows": len(measured),
    }


def summarize_m4(suite: dict[str, Any], profile: dict[str, Any], *, quick: bool) -> dict[str, Any]:
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
            "fault_disable_acknowledged": detail.get("fault_disable_acknowledged"),
            "target_http_responding": detail.get("target_http_responding"),
            "target_after_submitted": detail.get("target_after_submitted"),
            "target_after_http_success": detail.get("target_after_http_success"),
            "target_after_quality_success": detail.get("target_after_quality_success"),
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
    summary = run.get("summary") or {}
    contract = summary.get("measurement_contract") or {}
    clock = summary.get("run_clock") or {}
    origin = _number(clock.get("started_wall_ms"))
    start = _number(contract.get("measurement_start_s"))
    end = _number(contract.get("measurement_end_s"))
    load_end = _number(clock.get("load_duration_s"))
    valid_window = (origin is not None and start is not None and end is not None
                    and load_end is not None and 0 <= start < end <= load_end)
    window_start = origin + start * 1000 if valid_window else None
    window_end = origin + end * 1000 if valid_window else None
    duration = end - start if valid_window else float(run.get("duration_s") or 0)
    issues = []
    if not valid_window:
        issues.append("缺少有效的运行起点/测量窗口；历史数据仅展示全程计数")
    if contract.get("fairness_mode") != "independent-periodic-v1":
        issues.append("不是独立周期公平性场景，不能作为稳态公平性完整证据")

    def inside(value):
        return value is not None and (not valid_window or window_start <= value < window_end)

    def request_start(row):
        ts, elapsed = _number(row.get("ts_ms")), _number(row.get("stage_ms"))
        return ts - elapsed if ts is not None and elapsed is not None else None

    tenants = []
    for tenant in range(tenant_count):
        selected = [row for row in rows if str(row.get("tenant_idx")) == str(tenant)]
        for row in selected:
            if row.get("op") != "commit_done":
                continue
            polls = _number(row.get("poll_count"))
            errors = _number(row.get("poll_http_errors"))
            if (polls is not None and errors is not None and polls > errors
                    and not _number(row.get("last_nonterminal_at_ms"))
                    and not row.get("commit_terminal_state")):
                issues.append(f"租户 {tenant} 轮询有成功响应但未识别任何状态，需核对协议解析")
        submits = [row for row in selected if row.get("op") == "commit_submit" and inside(request_start(row))]
        evidence = _commit_window_evidence(selected)
        if any(evidence.get(name) for name in ("missing_poll_audit", "invalid_intervals", "invalid_202_receipts",
                                               "duplicate_receipts", "duplicate_observations", "orphan_observations")):
            issues.append(f"租户 {tenant} Commit 回执/轮询证据不完整")
        accepted = evidence["accepted"]
        terminal = list(evidence["terminals"].values())
        all_done = [row for row in terminal if row.get("status") == "ok"]
        done = [row for row in all_done if inside(_number(row.get("completed_at_ms")))]
        failed = [row for row in terminal if row.get("status") != "ok"]
        completion_times = sorted(value for row in done if (value := _number(row.get("completed_at_ms"))) is not None)
        longest_gap = None
        if valid_window:
            points = [window_start, *completion_times, window_end]
            longest_gap = max((b - a for a, b in zip(points, points[1:])), default=0) / 1000
        arrivals = {}
        for task in ("read", "write"):
            spec = (contract.get("arrival") or {}).get(task) or {}
            rate, offset = _number(spec.get("rps")), _number(spec.get("start_s"))
            arrival_end = _number(spec.get("end_s"))
            planned = None
            if (valid_window and spec.get("scope") == "per_tenant" and rate is not None
                    and rate > 0 and offset is not None and arrival_end is not None):
                upper = max(0, math.ceil((min(end, arrival_end) - offset) * rate - 1e-9))
                lower = max(0, math.ceil((start - offset) * rate - 1e-9))
                planned = max(0, upper - lower)
            emitted = [row for row in selected if row.get("op") == "arrival"
                       and row.get("arrival_task") == task and inside(_number(row.get("planned_at_ms")))]
            in_window = [row for row in emitted if inside(_number(row.get("ts_ms")))]
            seqs = [row.get("arrival_sequence") for row in in_window]
            lag = [value for row in in_window if (value := _number(row.get("stage_ms"))) is not None]
            duplicate = len(seqs) - len(set(seqs))
            invalid_sequence = any((value := _number(seq)) is None or value < 0 or not value.is_integer() for seq in seqs)
            arrivals[task] = {"planned": planned, "started_in_window": len(in_window),
                              "started_after_window": len(emitted) - len(in_window),
                              "missing_starts": max(0, planned - len(set(seqs))) if planned is not None else None,
                              "duplicate_starts": duplicate,
                              "start_lag_p95_ms": percentile(lag, 95)}
            if planned is None or planned <= 0 or len(in_window) != planned or duplicate or invalid_sequence:
                issues.append(f"租户 {tenant} {task} 计划/实际到达未完整对齐")
        search_rows = [row for row in selected if row.get("op") == "read" and inside(request_start(row))]
        poll_rows = [row for row in selected if row.get("op") == "commit_done"]
        poll_counts = [_number(row.get("poll_count")) for row in poll_rows]
        poll_errors = [_number(row.get("poll_http_errors")) for row in poll_rows]
        poll_totals_valid = not evidence["missing_poll_audit"] and all(n is not None and e is not None and n.is_integer()
                                and e.is_integer() and 0 <= e <= n
                                for n, e in zip(poll_counts, poll_errors))
        tenants.append({"tenant_index": tenant, "commit_submitted": len(submits),
                        "commit_accepted": len(accepted), "commit_completed": len(done),
                        "commit_completed_after_window": sum(
                            valid_window and (_number(row.get("completed_at_ms")) or 0) >= window_end
                            for row in all_done) if valid_window else None,
                        "commit_completed_total": len(all_done), "arrivals": arrivals,
                        "commit_failed": len(failed),
                        "commit_pending": max(0, len(accepted) - len(terminal)),
                        "commit_observation_outcomes": evidence["observation_outcomes"],
                        "commit_poll_count_full_run": int(sum(poll_counts)) if poll_totals_valid else None,
                        "commit_poll_http_errors_full_run": int(sum(poll_errors)) if poll_totals_valid else None,
                        "commit_completed_per_s": len(done) / duration if duration else None,
                        "longest_no_completion_s": longest_gap,
                        "search": _request_stats(search_rows)})
    commit_values = [float(row["commit_completed_per_s"] or 0) for row in tenants]
    inverse_p95 = [
        1000 / value if value else 0
        for row in tenants
        for value in [row["search"].get("served_p95_ms") or row["search"].get("p95_ms")]
    ]
    commit_throughput_jain = jain(commit_values)
    search_inverse_p95_jain = jain(inverse_p95)
    search_health = _request_stats([row for row in rows if row.get("op") == "read"])
    search_health["status"] = "PASS" if not any(
        (search_health.get(key) or 0) for key in ("errors", "timeouts", "empty_recall")
    ) and search_health.get("recall_service_rate") == 1 else "INVALID"
    search_health["reason"] = (
        "所有 Search 都返回非空 Recall，且无 HTTP/传输错误"
        if search_health["status"] == "PASS" else
        "公平指数保留，但 Search 存在错误、超时或空召回，不能把 Jain 解释成健康度"
    )
    return {"scenario": run.get("scenario"), "duration_s": duration,
            "window_start_ms": window_start, "window_end_ms": window_end,
            "evidence_complete": not issues and run.get("status") == "completed",
            "fairness_result_available": (commit_throughput_jain is not None
                                           and search_inverse_p95_jain is not None),
            "fairness_result_reason": (
                "窗口内至少有一个已完成 Commit，两个 Jain 均可计算"
                if commit_throughput_jain is not None and search_inverse_p95_jain is not None
                else "测量窗口内所有租户 Commit 吞吐均为 0，Commit Jain 无定义；排空完成不能替代窗口吞吐"
            ),
            "evidence_issues": issues,
            "tenant_count": tenant_count, "tenants": tenants,
            "search_health": search_health,
            "commit_throughput_jain": commit_throughput_jain,
            "search_inverse_p95_jain": search_inverse_p95_jain}


def summarize_m2(runs: dict[str, dict[str, Any]], *, quick: bool) -> dict[str, Any]:
    windows = []
    for count in (4, 8):
        run = (runs.get(f"m2-fairness-{count}t")
               or runs.get(f"m3-fairness-{count}t"))
        if run and _records(run):
            windows.append(_fairness_window(run, count))
    complete = sum(window["evidence_complete"] and window["fairness_result_available"]
                   for window in windows)
    missing_result = [window["scenario"] for window in windows
                      if window["evidence_complete"] and not window["fairness_result_available"]]
    reason = ("独立周期发压；仅窗口内完成计算吞吐，排空另列。零完成租户保留；"
              "短窗口不证明长期稳态")
    if missing_result:
        reason += "；以下场景窗口内没有已完成 Commit，不能计算 Commit Jain：" + ", ".join(missing_result)
    return {"status": "MEASURED" if not quick and complete == 2 else "PARTIAL" if windows else "BLOCKED",
            "reason": reason,
            "expected_windows": 2, "observed_windows": len(windows),
            "complete_windows": complete, "windows": windows}


def _commit_window_evidence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def key(row):
        return str(row.get("tenant_idx")), row.get("session_id"), row.get("archive_id")

    submitted = [r for r in rows if r.get("op") == "commit_submit"]
    receipts = [r for r in submitted if _number(r.get("http_status")) == 202
                and r.get("session_id") and r.get("archive_id")]
    groups, observations = {}, {}
    for row in receipts:
        groups.setdefault(key(row), []).append(row)
    for row in rows:
        if row.get("op") == "commit_done":
            observations.setdefault(key(row), []).append(row)
    accepted = [group[0] for group in groups.values() if len(group) == 1]
    terminals, intervals, confirmed = {}, [], []
    duplicate_receipts = sum(len(group) for group in groups.values() if len(group) != 1)
    duplicate_observations = sum(len(group) for k, group in observations.items()
                                 if k in groups and len(group) != 1)
    orphan_observations = sum(len(group) for k, group in observations.items() if k not in groups)
    invalid_intervals = missing_audit = poll_errors = 0
    observed_end = max((_number(r.get("ts_ms")) for r in rows
                        if _number(r.get("ts_ms")) is not None), default=None)
    for row in accepted:
        k = key(row)
        candidates = observations.get(k, [])
        observation = candidates[0] if len(candidates) == 1 else {}
        start = _number(row.get("accepted_at_ms"))
        end = _number(observation.get("observation_ended_at_ms"))
        if end is None:
            end = _number(observation.get("ts_ms"))
        if end is None:
            end = observed_end
        versioned = observation.get("poll_evidence_version") == "echomem-poll-v1"
        polls, errors = _number(observation.get("poll_count")), _number(observation.get("poll_http_errors"))
        audit_valid = (versioned and polls is not None and errors is not None
                       and polls.is_integer() and errors.is_integer() and 0 <= errors <= polls)
        missing_audit += not audit_valid
        if audit_valid:
            poll_errors += int(errors)
        terminal_state = str(observation.get("commit_terminal_state") or "")
        terminal = _number(observation.get("terminal_at_ms")) if (
            audit_valid and polls > errors and terminal_state in {"completed", "failed", "error"}
            and _number(observation.get("http_status")) == 200) else None
        if not versioned and observation.get("status") == "ok":
            terminal = _number(observation.get("completed_at_ms"))
        if start is None or start < 0 or end is None or end < start:
            invalid_intervals += 1
            continue
        if terminal is not None and not start <= terminal <= end:
            invalid_intervals += 1
            terminal = None
        if terminal is not None:
            terminals[k] = {**observation, "completed_at_ms": terminal,
                            "status": "ok" if terminal_state == "completed" or not versioned else "error"}
        right = terminal if terminal is not None else end
        intervals.append((start, right))
        pending = _number(observation.get("last_nonterminal_at_ms"))
        if audit_valid and pending is not None and polls > errors:
            if start <= pending <= right:
                confirmed.append((start, pending))
            else:
                invalid_intervals += 1
    return {"accepted": accepted, "terminals": terminals, "intervals": intervals,
            "confirmed": confirmed, "accepted_receipts": len(receipts),
            "http_202_responses": sum(_number(r.get("http_status")) == 202 for r in submitted),
            "invalid_202_receipts": sum(_number(r.get("http_status")) == 202 for r in submitted) - len(receipts),
            "duplicate_receipts": duplicate_receipts, "duplicate_observations": duplicate_observations,
            "orphan_observations": orphan_observations, "invalid_intervals": invalid_intervals,
            "missing_poll_audit": missing_audit,
            "observation_outcomes": dict(Counter(r.get("poll_outcome") if r.get("poll_outcome") in
                                                ("completed", "failed", "timeout", "stopped") else "unknown"
                                                for group in observations.values() for r in group)),
            "poll_http_errors": poll_errors if not missing_audit else None}


def _flood_window(baseline_rows: list[dict[str, Any]], run: dict[str, Any], tenant_count: int = 4) -> dict[str, Any]:
    rows = _records(run)
    evidence = _commit_window_evidence(rows)
    accepted, done, intervals = evidence["accepted"], evidence["terminals"], evidence["intervals"]
    overlap, confirmed = [], []
    for row in rows:
        if row.get("op") != "read":
            continue
        finished = _number(row.get("ts_ms"))
        latency = _number(row.get("stage_ms"))
        started = finished - latency if finished is not None and latency is not None and latency >= 0 else None
        if started is not None and any(left <= started <= right for left, right in intervals):
            overlap.append(row)
            if any(left <= started <= right for left, right in evidence["confirmed"]):
                confirmed.append(row)
    by_tenant = []
    for tenant in range(tenant_count):
        before = _request_stats([row for row in baseline_rows if str(row.get("tenant_idx")) == str(tenant)])
        during = _request_stats([row for row in confirmed if str(row.get("tenant_idx")) == str(tenant)])
        # Compare only successfully served, non-empty Recall requests.  The
        # all-request P95 is retained separately because timeout samples can
        # pin both sides to the client deadline and hide real degradation.
        ratio = (during["served_p95_ms"] / before["served_p95_ms"]
                 if before["served_p95_ms"] and during["served_p95_ms"] else None)
        by_tenant.append({"tenant_index": tenant, "baseline": before, "overlap": during,
                          "p95_delta_ms": (during["served_p95_ms"] - before["served_p95_ms"]
                                           if before["served_p95_ms"] is not None and during["served_p95_ms"] is not None
                                           else None),
                          "p95_ratio": ratio})
    completed = sum(row.get("status") == "ok" for row in done.values())
    failed = sum(row.get("status") != "ok" for row in done.values())
    events = []
    durations = []
    for start, end in intervals:
        events.extend(((start, 1), (end, -1)))
    for row in accepted:
        key = str(row.get("tenant_idx")), row.get("session_id"), row.get("archive_id")
        start = _number(row.get("accepted_at_ms"))
        end = _number((done.get(key) or {}).get("completed_at_ms"))
        if start is not None and end is not None and end >= start:
            durations.append(end - start)
    queued = peak = 0
    commit_timeline = []
    for event_at, change in sorted(events, key=lambda item: (item[0], -item[1])):
        queued += change
        peak = max(peak, queued)
        commit_timeline.append({"at_ms": event_at, "pending": max(0, queued)})
    search_buckets: dict[int, list[float]] = {}
    for row in confirmed:
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
    terminal_times = [_number(row.get("completed_at_ms")) for row in done.values()]
    drain_s = ((max(terminal_times) - max(accepted_times)) / 1000
               if accepted_times and len(terminal_times) == len(accepted) == len(intervals)
               and not any(evidence[k] for k in ("duplicate_receipts", "duplicate_observations",
                                                 "orphan_observations", "invalid_intervals", "missing_poll_audit")) else None)
    open_rows = [r for r in rows if r.get("op") == "open"]
    aborted_rows = [r for r in rows if r.get("op") == "commit_preparation_failed"]
    submitted_rows = [r for r in rows if r.get("op") == "commit_submit"]
    return {"scenario": run.get("scenario"), "baseline": _request_stats(baseline_rows),
            "preparation": {
                "open_requests": len(open_rows),
                "add_requests": sum(r.get("op") == "add" for r in rows),
                "aborted_transactions": len(aborted_rows),
                "attempted_by_tenant": dict(Counter(str(r.get("tenant_idx")) for r in open_rows)),
                "stage_http_counts": dict(Counter(f"{r.get('op')}:{r.get('http_status')}"
                    for r in rows if r.get("op") in {"open", "add"})),
            },
            "overlap": _request_stats(overlap), "tenants": by_tenant,
            "confirmed_overlap": _request_stats(confirmed),
            "uncertain_overlap_reads": len(overlap) - len(confirmed),
            "commit_evidence": {k: v for k, v in evidence.items()
                                if k not in {"accepted", "terminals", "intervals", "confirmed"}},
            "commit_planned_or_recorded": len(submitted_rows),
            "commit_accepted_202": evidence["accepted_receipts"], "unique_accepted_tasks": len(accepted),
            "accepted_by_tenant": dict(Counter(str(row.get("tenant_idx")) for row in accepted)),
            "submitted_by_tenant": dict(Counter(str(row.get("tenant_idx")) for row in submitted_rows)),
            "commit_rejected": sum(r.get("op") == "commit_submit" and (_number(r.get("http_status")) or 0) >= 400 for r in rows),
            "commit_completed": completed, "commit_failed": failed,
            "commit_pending": max(0, len(accepted) - len(done)),
            "observed_inflight_peak": peak, "max_observed_terminal_latency_s": max(durations) / 1000 if durations else None,
            "drain_time_s": drain_s,
            "commit_timeline": commit_timeline, "search_timeline": search_timeline,
            "overlap_intervals": len(intervals), "confirmed_intervals": len(evidence["confirmed"]),
            "internal_order_observation": "内部顺序未观测"}


def _heterogeneous_tenant_window(run: dict[str, Any]) -> dict[str, Any]:
    rows = _records(run)
    contract = (run.get("summary") or {}).get("measurement_contract") or {}
    arrival = contract.get("arrival") or {}
    read_spec = arrival.get("read") or {}
    write_spec = arrival.get("write") or {}
    read_weights = list(read_spec.get("tenant_weights") or [])
    write_weights = list(write_spec.get("tenant_weights") or [])
    tenant_count = int(contract.get("tenant_count") or 0)
    tenants = []
    for tenant in range(tenant_count):
        tenant_rows = [row for row in rows if str(row.get("tenant_idx")) == str(tenant)]
        reads = [row for row in tenant_rows if row.get("op") == "read"]
        arrivals = {
            task: [row for row in tenant_rows if row.get("op") == "arrival"
                   and row.get("arrival_task") == task]
            for task in ("read", "write")
        }
        completed = sum(row.get("op") == "commit_done" and row.get("status") == "ok"
                        for row in tenant_rows)
        tenants.append({
            "tenant_index": tenant,
            "search_weight": read_weights[tenant] if tenant < len(read_weights) else None,
            "commit_weight": write_weights[tenant] if tenant < len(write_weights) else None,
            "planned_search_rps": (
                float(read_spec.get("rps") or 0) * read_weights[tenant]
                if tenant < len(read_weights) else None
            ),
            "planned_commit_rpm": (
                float(write_spec.get("rps") or 0) * 60 * write_weights[tenant]
                if tenant < len(write_weights) else None
            ),
            "search_arrivals": len(arrivals["read"]),
            "commit_arrivals": len(arrivals["write"]),
            "search": _request_stats(reads),
            "commit_completed": completed,
        })
    issues = []
    if run.get("status") != "completed" or run.get("runner_timeout"):
        issues.append("execution_not_complete")
    if not contract.get("heterogeneous_tenant_load") or tenant_count != 4:
        issues.append("heterogeneous_workload_contract_missing")
    if len(read_weights) != tenant_count or len(set(read_weights)) < 2:
        issues.append("search_tenant_weights_missing_or_uniform")
    if len(write_weights) != tenant_count or len(set(write_weights)) < 2:
        issues.append("commit_tenant_weights_missing_or_uniform")
    for tenant in tenants:
        if not tenant["search_arrivals"]:
            issues.append(f"tenant_{tenant['tenant_index']}_search_not_started")
        if not tenant["commit_arrivals"]:
            issues.append(f"tenant_{tenant['tenant_index']}_commit_not_started")
    return {
        "scenario": str(run.get("scenario") or "m3-heterogeneous-tenants"),
        "status": "MEASURED" if tenants and not issues else "PARTIAL" if rows else "BLOCKED",
        "evidence_issues": issues,
        "search_weights": read_weights,
        "commit_weights": write_weights,
        "tenants": tenants,
    }


def summarize_m3(runs: dict[str, dict[str, Any]], *, quick: bool) -> dict[str, Any]:
    baseline_run = runs.get("m3-baseline") or runs.get("m4-baseline") or {}
    baseline_rows = _records(baseline_run)
    windows = []
    for canonical, legacy in (("m3-flood-uniform", "m4-flood-uniform"),
                              ("m3-flood-single-tenant", "m4-flood-single-tenant")):
        run = runs.get(canonical) or runs.get(legacy)
        if run and _records(run):
            windows.append(_flood_window(baseline_rows, run))
    heterogeneous = _heterogeneous_tenant_window(
        runs.get("m3-heterogeneous-tenants") or {}
    )
    observed = len(windows) + int(bool(baseline_rows)) + int(
        heterogeneous["status"] != "BLOCKED"
    )
    issues = []
    baseline = _request_stats(baseline_rows)
    baseline_health_reasons = []
    if not baseline_rows:
        baseline_health_reasons.append("no_search_samples")
    if baseline["errors"]:
        baseline_health_reasons.append(f"http_or_transport_errors={baseline['errors']}")
    if baseline["timeouts"]:
        baseline_health_reasons.append(f"timeouts={baseline['timeouts']}")
    if baseline["empty_recall"]:
        baseline_health_reasons.append(f"empty_recall={baseline['empty_recall']}")
    if baseline["recall_service_rate"] is not None and baseline["recall_service_rate"] < 1:
        baseline_health_reasons.append(
            f"recall_service_rate={baseline['recall_service_rate']:.6f}"
        )
    baseline_health = {
        "status": "PASS" if not baseline_health_reasons else "INVALID",
        "eligible_for_priority_comparison": not baseline_health_reasons,
        "reasons": baseline_health_reasons,
        "planned_or_recorded": baseline["planned_or_recorded"],
        "recall_served": baseline["recall_served"],
        "errors": baseline["errors"],
        "timeouts": baseline["timeouts"],
        "empty_recall": baseline["empty_recall"],
        "recall_service_rate": baseline["recall_service_rate"],
    }
    if baseline_run.get("status") != "completed" or baseline_run.get("runner_timeout"):
        issues.append("baseline_execution_not_complete")
    if not baseline_health["eligible_for_priority_comparison"]:
        issues.append("baseline_health_gate_failed")
    baseline_tenants = []
    for tenant in range(4):
        tenant_rows = [r for r in baseline_rows if r.get("op") == "read" and str(r.get("tenant_idx")) == str(tenant)]
        stats = _request_stats(tenant_rows)
        factual_hits = sum(r.get("status") == "ok" and _truth(
            r.get("expected_fact_found") if r.get("quality_assertion") == "fixed-fact-in-items"
            else r.get("marker_found")) for r in tenant_rows)
        baseline_tenants.append({"tenant_index": tenant, "actual_recall_hits": factual_hits, **stats})
        if (not stats["planned_or_recorded"] or stats["latency_missing_or_invalid"]
                or stats["quality_missing"] or not factual_hits
                or any(r.get("query_type") != "recall" for r in tenant_rows)):
            issues.append(f"baseline_tenant_{tenant}_recall_or_timing_unproven")
    if any(r.get("op") == "commit_submit" for r in baseline_rows):
        issues.append("baseline_contains_commit")
    for window in windows:
        name = window["scenario"]
        run = runs[name]
        gaps = []
        if run.get("status") != "completed" or run.get("runner_timeout"):
            gaps.append("execution_not_complete")
        contract = (run.get("summary") or {}).get("measurement_contract") or {}
        count, waves = _number(contract.get("barrier_count")), _number(contract.get("barrier_waves"))
        planned = (int(count * waves) if count is not None and waves is not None
                   and count > 0 and waves > 0 and count.is_integer() and waves.is_integer() else None)
        window["commit_planned"] = planned
        if (contract.get("version") not in {"echomem-case-v1", "echomem-case-v2"}
                or contract.get("tenant_count") != 4
                or contract.get("query_mode") != "recall" or planned is None):
            gaps.append("effective_workload_contract_missing_or_invalid")
        preparation = window["preparation"]
        open_requests = preparation["open_requests"]
        attempted = open_requests or window["commit_planned_or_recorded"]
        accounted = window["commit_planned_or_recorded"] + preparation["aborted_transactions"]
        if planned is not None and (attempted != planned or accounted != planned):
            gaps.append("planned_commit_attempts_incomplete")
        attempted_by_tenant = (preparation["attempted_by_tenant"]
                               or window["submitted_by_tenant"])
        attempted_tenants = set(attempted_by_tenant)
        if name.endswith("uniform"):
            counts = list(attempted_by_tenant.values())
            if attempted_tenants != {"0", "1", "2", "3"} or max(counts, default=0) - min(counts, default=0) > 1:
                gaps.append("uniform_commit_attempt_distribution_not_observed")
        elif attempted_tenants != {"0"}:
            gaps.append("single_tenant_commit_attempt_distribution_not_observed")
        evidence = window["commit_evidence"]
        gaps.extend(k for k in ("duplicate_receipts", "duplicate_observations", "orphan_observations",
                                "invalid_intervals", "missing_poll_audit", "invalid_202_receipts") if evidence[k])
        conditions = []
        if preparation["aborted_transactions"]:
            conditions.append("preparation_rejected_or_failed")
        if window["commit_rejected"]:
            conditions.append("commit_submit_rejected")
        if window["commit_pending"]:
            conditions.append("accepted_commit_not_terminal_by_cutoff")
        for tenant in window["tenants"]:
            stats = tenant["overlap"]
            if (not stats["planned_or_recorded"] or stats["latency_missing_or_invalid"]
                    or stats["quality_missing"] or stats["recall_queries"] != stats["planned_or_recorded"]):
                gaps.append(f"tenant_{tenant['tenant_index']}_confirmed_overlap_unproven")
        window["evidence_issues"] = gaps
        window["observed_conditions"] = conditions
        issues.extend(f"{name}:{gap}" for gap in gaps)
    if len(windows) != 2:
        issues.append("flood_windows_missing")
    issues.extend(
        f"m3-heterogeneous-tenants:{issue}"
        for issue in heterogeneous.get("evidence_issues", [])
    )
    if heterogeneous["status"] == "BLOCKED":
        issues.append("heterogeneous_tenant_window_missing")
    status_reason = (
        "基线健康门禁通过；非终态轮询确认的重叠与宽观察窗口分别展示。"
        if baseline_health["eligible_for_priority_comparison"]
        else "基线自身已有错误、超时或空召回，保留全部异常但不把本轮洪泛结果作为可信退化对比；需先复测健康基线。"
    )
    return {"status": "MEASURED" if observed == 4 and not issues and not quick else "PARTIAL" if observed else "BLOCKED",
            "reason": status_reason + "服务拒绝和截止未终态是测量结果；MEASURED 不代表性能达标或严格内部优先级",
            "evidence_issues": issues, "baseline": baseline, "baseline_health": baseline_health,
            "baseline_tenants": baseline_tenants,
            "expected_windows": 4, "observed_windows": observed, "windows": windows,
            "heterogeneous_tenants": heterogeneous,
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
    from performance.targets.echomem.acceptance.observability_timeline import (
        _epoch as process_identity,
        timeline_counts,
    )
    from performance.targets.echomem.acceptance.reliability_evidence import observability_counts

    before = suite.get("tenant_observability_before") or {}
    final_payload = suite.get("tenant_observability_after_all") or suite.get("tenant_observability") or {}
    after = _probe_detail(final_payload, "tenant-observability") or final_payload
    config = profile.get("tenant_observability") or {}
    tenants = list(config.get("expected_tenants", []))
    lanes = list(config.get("expected_lanes", []))
    during = suite.get("tenant_observability_samples") or []
    monitoring = suite.get("tenant_observability_monitor") or {}
    timeline = timeline_counts({
        "expected_tenants": tenants, "expected_lanes": lanes,
        "before": before, "during": during, "after": after,
        "window_start_s": monitoring.get("window_start_s"),
        "window_end_s": monitoring.get("window_end_s"),
        "max_sampling_gap_s": monitoring.get("max_sampling_gap_s"),
        "monitor_errors": monitoring.get("errors"),
    }, allow_restarts=True)
    raw_samples = [before, *during, after]
    snapshots = [observability_counts({
        **(sample if isinstance(sample, dict) else {}),
        "expected_tenants": tenants, "expected_lanes": lanes,
    }) for sample in raw_samples]
    fields = ("queued", "wait_seconds_total", "exec_seconds_total", "rejected_total",
              "accepted_total", "completed_total", "failed_total")
    counters = fields[1:]
    initial = {(r["tenant"], r["lane"]): r for r in snapshots[0]["rows"]}
    final = {(r["tenant"], r["lane"]): r for r in snapshots[-1]["rows"]}
    first_epoch, last_epoch = process_identity(before), process_identity(after)
    same_process = first_epoch is not None and first_epoch == last_epoch
    matrix = []
    for i, _tenant in enumerate(tenants):
        for lane in lanes:
            key = (f"T{i + 1}", lane)
            left, right = initial.get(key, {}), final.get(key, {})
            missing = [field for field in fields if left.get(field) is None or right.get(field) is None]
            invalid = [field for field in counters
                       if same_process and left.get(field) is not None and right.get(field) is not None
                       and right[field] < left[field]]
            matrix.append({"tenant_id": key[0], "lane": lane,
                           "before": {field: left.get(field) for field in fields},
                           "after": {field: right.get(field) for field in fields},
                           "delta": {field: right[field] - left[field]
                                     if same_process and field not in missing else None for field in fields},
                           "missing_fields": missing, "invalid_fields": invalid,
                           "delta_scope": "same_process" if same_process else "unavailable_across_restart_or_unknown_process"})
    scenarios = {"NORMAL": False, "QUEUE": False, "REJECT": False, "RESET": False}
    queue_timeline, rejection_timeline, process_segments = [], [], []
    previous_rows, previous_epoch = {}, None
    last_known_epoch = None
    for index, (raw, public) in enumerate(zip(raw_samples, snapshots)):
        raw = raw if isinstance(raw, dict) else {}
        epoch = process_identity(raw)
        if epoch is not None and epoch != last_known_epoch:
            if last_known_epoch is not None:
                scenarios["RESET"] = True
            process_segments.append({"sample_index": index, "segment": len(process_segments) + 1})
            last_known_epoch = epoch
        if public["status"] != "PASS":
            # Do not bridge a missing/invalid snapshot with a fabricated delta.
            previous_rows, previous_epoch = {}, None
            continue
        current = {(r["tenant"], r["lane"]): r for r in public["rows"]
                   if r["tenant"] != "unknown" and r["lane"] in lanes}
        for key, row in current.items():
            queued = row.get("queued")
            if queued is not None:
                queue_timeline.append({"sample_index": index, "created_at": raw.get("created_at"),
                                       "tenant_id": key[0], "lane": key[1], "queued": queued})
                scenarios["QUEUE"] |= queued > 0
            prior = previous_rows.get(key)
            if prior and epoch is not None and epoch == previous_epoch:
                accepted_delta = row["accepted_total"] - prior["accepted_total"]
                wait_delta = row["wait_seconds_total"] - prior["wait_seconds_total"]
                rejected_delta = row["rejected_total"] - prior["rejected_total"]
                scenarios["NORMAL"] |= accepted_delta > 0
                scenarios["QUEUE"] |= wait_delta > 0
                scenarios["REJECT"] |= rejected_delta > 0
                if rejected_delta > 0:
                    rejection_timeline.append({"sample_index": index, "created_at": raw.get("created_at"),
                                               "tenant_id": key[0], "lane": key[1],
                                               "rejected_delta": rejected_delta})
        previous_rows, previous_epoch = current, epoch
    expected_cells = len(tenants) * len(lanes)
    complete_keys = {(f"T{i + 1}", lane) for i in range(len(tenants)) for lane in lanes}
    for snapshot in snapshots:
        bad = {(r["tenant"], r["lane"]) for name in ("missing_details", "invalid_details", "duplicate_details")
               for r in snapshot[name]}
        complete_keys -= bad
        if snapshot["status"] != "PASS" and not bad:
            # Declaration/HTTP errors can invalidate an otherwise present row set.
            complete_keys.clear()
    if not timeline["contract_valid"]:
        complete_keys.clear()
    epochs = [process_identity(raw if isinstance(raw, dict) else {}) for raw in raw_samples]
    identity_gaps = [index for index, epoch in enumerate(epochs) if epoch is None]

    def restart_brackets(index: int) -> bool:
        before = next((epoch for epoch in reversed(epochs[:index]) if epoch is not None), None)
        after_epoch = next((epoch for epoch in epochs[index + 1:] if epoch is not None), None)
        return (snapshots[index]["status"] != "PASS"
                and before is not None and after_epoch is not None and before != after_epoch)

    bracketed_identity_gaps = [index for index in identity_gaps if restart_brackets(index)]
    identity_evidence_complete = not identity_gaps or len(bracketed_identity_gaps) == len(identity_gaps)
    capture_complete = bool(
        timeline["contract_valid"]
        and timeline["during_count"] > 0
        and timeline["sampling_times_valid"]
        and timeline["gaps_exceeded"] == 0
        and identity_evidence_complete
    )
    status = ("BLOCKED" if not timeline["contract_valid"] else
              "MEASURED" if capture_complete else "PARTIAL")
    return {"status": status,
            "reason": "固定分母逐帧核验；服务返回缺行、非法值、拒绝未计数或重启后清零均作为测量结果，不再误判为测试未执行。重启按进程身份分段，缺失不补零。",
            "capture_complete": capture_complete,
            "service_contract_status": timeline["status"],
            "identity_evidence_complete": identity_evidence_complete,
            "restart_transition_identity_gaps": bracketed_identity_gaps,
            "expected_tenants": [f"T{i + 1}" for i in range(len(tenants))], "expected_lanes": lanes,
            "expected_cells": expected_cells, "complete_cells": len(complete_keys),
            "endpoint_complete_cells": min(snapshots[0]["valid_cells"], snapshots[-1]["valid_cells"]),
            "duplicate_keys": [{"sample_index": i, **row} for i, snapshot in enumerate(snapshots)
                               for row in snapshot["duplicate_details"]],
            "scenarios": scenarios, "timeline": timeline,
            "process_segments": {"changed": scenarios["RESET"],
                                 "counter_reset": any(r["classification"] == "restart"
                                                      for r in timeline["counter_regressions"]),
                                 "segments": process_segments},
            "sample_count": len(raw_samples), "queue_timeline": queue_timeline,
            "rejection_timeline": rejection_timeline, "matrix": matrix}


def evaluate_observation(suite: dict[str, Any], profile: dict[str, Any],
                         m1_reports: list[dict[str, Any]] | None = None,
                         *, quick: bool = False,
                         selected_metrics: list[str] | None = None) -> dict[str, Any]:
    runs = {str(run.get("scenario")): run for run in suite.get("runs", [])}
    metrics = {
        "M1": summarize_m1(m1_reports or [], profile),
        "M2": summarize_m2(runs, quick=quick),
        "M3": summarize_m3(runs, quick=quick),
        "M4": summarize_m4(suite, profile, quick=quick),
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
    seed = suite.get("seed") or {}
    # M1 seed preparation is stored in per-topology reports rather than the
    # shared suite seed. Surface that evidence in the top-level report so a
    # reused-seed run is auditable and blocked auth preflights are explicit.
    if not seed and m1_reports:
        reused = [row for row in m1_reports if row.get("seed_reused")]
        blocked = [row for row in m1_reports if row.get("status") == "BLOCKED"]
        seed = {
            "status": "REUSED_PRECHECK_BLOCKED" if blocked else ("REUSED" if reused else None),
            "seed_source": "reused-seed" if reused else None,
            "seed_actor_count": sum(int(row.get("seed_actor_count") or 0) for row in m1_reports),
            "reuse_seed_preflight": [row.get("reuse_seed_preflight") for row in m1_reports
                                      if row.get("reuse_seed_preflight")],
            "error": "; ".join(str(row.get("stop_reason")) for row in blocked
                                  if row.get("stop_reason")),
        }
    # M1 seed reports carry the only direct evidence that the injected Commit
    # produced a readable memory list. Keep it separate from Search quality:
    # a service can return a non-empty Recall while its commit-memory listing
    # is empty (for example when extraction completed with gaps).
    seed_actor_rows = []
    for report in m1_reports or []:
        nested = report.get("seed") if isinstance(report, dict) else None
        rows = nested.get("actors") if isinstance(nested, dict) else None
        if isinstance(rows, list):
            seed_actor_rows.extend(row for row in rows if isinstance(row, dict))
    seed_evidence = seed.get("seed_evidence") or seed.get("evidence") or {}
    if not seed_evidence and seed_actor_rows:
        seed_evidence = {
            "actor_count": len(seed_actor_rows),
            "healthy_actors": sum(row.get("status") == "PASS" for row in seed_actor_rows),
        }
    memory_observed_rows = [row for row in seed_actor_rows if row.get("memory_observation")]
    memory_rows = [row for row in memory_observed_rows
                   if row.get("memory_observation") == "counted"]
    memory_counts = [row.get("memory_count") for row in memory_rows
                     if isinstance(row.get("memory_count"), (int, float))]
    seed_queries = [query for row in seed_actor_rows for query in (row.get("queries") or [])
                    if isinstance(query, dict)]
    seed_statuses = [row.get("seed_status") or (row.get("seed") or {}).get("status")
                     for row in m1_reports or [] if isinstance(row, dict)]
    aggregate_seed_status = seed.get("status")
    if not aggregate_seed_status and seed_statuses:
        aggregate_seed_status = "PARTIAL" if "PARTIAL" in seed_statuses else seed_statuses[0]
    reported_healthy = sum(int(row.get("seed_healthy_actor_count") or 0)
                           for row in m1_reports or [] if isinstance(row, dict))
    reported_failed = sum(int(row.get("seed_failed_actor_count") or 0)
                          for row in m1_reports or [] if isinstance(row, dict))
    healthy_actor_count = reported_healthy or int(seed_evidence.get("healthy_actors") or 0)
    failed_actor_count = reported_failed or max(
        0, int(seed_evidence.get("actor_count") or len(seed_actor_rows)) - healthy_actor_count
    )
    setup_evidence = {
        "seed_status": aggregate_seed_status, "seed_contract": seed.get("seed_contract"),
        "seed_source": seed.get("seed_source"),
        "seed_documents_per_tenant": seed.get("seed_documents_per_tenant"),
        "facts_per_tenant": seed.get("facts_per_tenant"),
        "query_variants_per_tenant": seed.get("query_variants_per_tenant"),
        "healthy_actors": seed_evidence.get("healthy_actors"),
        "expected_actors": seed_evidence.get("actor_count") or seed.get("seed_actor_count"),
        "validated_queries_per_tenant": seed.get("validated_queries_per_tenant"),
        "reuse_seed_preflight": seed.get("reuse_seed_preflight"),
        "bare_marker_gate_failed": str(seed.get("error") or "").startswith("Seed marker not found"),
        "load_cases_completed": len(runs),
        "seed_failed_actor_count": failed_actor_count,
        "seed_healthy_actor_count": healthy_actor_count,
        "seed_memory_observed": len(memory_observed_rows),
        "seed_memory_rows": len(memory_rows),
        "seed_memory_nonempty": sum(bool(row.get("memory_count")) for row in memory_rows),
        "seed_memory_empty": sum(row.get("memory_count") == 0 for row in memory_rows),
        "seed_memory_unknown": sum(row.get("memory_observation") != "counted"
                                    for row in memory_observed_rows),
        "seed_memory_total": sum(memory_counts) if memory_counts else None,
        "seed_recall_served_diagnostic": sum(bool(row.get("recall_served"))
                                              for row in seed_actor_rows),
        "seed_marker_visible": sum(bool(row.get("marker_visible"))
                                    for row in seed_actor_rows),
        "seed_recall_queries": len(seed_queries),
        "seed_recall_served": sum(bool(row.get("recall_hit")) for row in seed_queries),
        "seed_recall_empty": sum(row.get("http_status") == 200 and not row.get("recall_hit")
                                  for row in seed_queries),
        "seed_recall_http_errors": sum(row.get("http_status") not in (200, None)
                                       for row in seed_queries),
    }
    overall = ("EXECUTION_ERROR" if "EXECUTION_ERROR" in statuses else
               "BLOCKED" if all(value == "BLOCKED" for value in statuses) else
               "MEASURED" if all(value == "MEASURED" for value in statuses) else "PARTIAL")
    issues = [
        {"category": "外部模型", "evidence": profile.get("model_preflight"),
         "note": "真实 LLM/embedding 预检结果"},
        {"category": "Search/Recall", "evidence": {
            "m1_windows": metrics["M1"].get("measured_windows"),
            "m3_windows": metrics["M3"].get("observed_windows")},
         "note": "错误、超时、空召回和质量分母见 M1/M3"},
        {"category": "Admission/调度", "evidence": {
            "m2": metrics["M2"]["status"], "m3": metrics["M3"]["status"]},
         "note": "客户端发送优先级不作为服务端出队顺序证据"},
        {"category": "Commit 恢复", "evidence": metrics["M5"].get("state_coverage"),
         "note": metrics["M5"].get("reason")},
        {"category": "原子引擎", "evidence": None,
         "note": "仅在 EchoMem HTTP/debug 或观测接口实际返回时归因"},
        {"category": "租户隔离", "evidence": {
            "expected": metrics["M4"].get("expected_cases"),
            "observed": metrics["M4"].get("observed_cases")},
         "note": "故障是否生效与旁观租户数据分开记录"},
        {"category": "可观测性", "evidence": metrics["M6"].get("scenarios"),
         "note": "缺字段、重复键、非法值和重启分段见 M6"},
        {"category": "测试平台/部署", "evidence": profile.get("resource_evidence"),
         "note": "实际容器资源、发送池和原始产物完整性"},
    ]
    supplemental_probes = {}
    for key, check_name, artifact in (
        ("concurrency_topology", "concurrency-topology", "concurrency-topology.json"),
        ("payload_boundary", "payload-boundary", "payload-boundary.json"),
    ):
        payload = suite.get(key) or {}
        if payload:
            supplemental_probes[key] = {
                "status": payload.get("status"),
                "reason": next((row.get("reason") for row in payload.get("checks", [])
                                if row.get("name") == check_name), None),
                "artifact": artifact,
                **_probe_detail(payload, check_name),
            }
    result = {"schema_version": 2,
            "metric_numbering": "capacity-fairness-priority-isolation-recovery-observability-v2",
            "assessment": "observation-only",
            "instance_profile": suite.get("instance_profile") or profile.get("name") or "local",
            "performance_thresholds_applied": False,
            "sampling_mode": "quick-non-complete" if quick else "full",
            "status": overall, "metrics": metrics,
            "selected_metrics": sorted(selected),
            "setup_evidence": setup_evidence,
            "model_preflight": profile.get("model_preflight") or {},
            "allowed_statuses": list(STATUSES),
            "issue_categories": issues,
        "api_coverage": summarize_api_coverage(suite),
        "timing_evidence": summarize_timing_evidence(suite, m1_reports or []),
        "concurrency_configuration": summarize_concurrency_configuration(profile),
        "supplemental_probes": supplemental_probes,
            "raw_suite": "suite.json"}
    result["new_findings"] = derive_new_findings(result)
    return result


def derive_observation_recommendations(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn measured symptoms into bounded, module-specific next actions."""
    metrics = result.get("metrics", {})
    setup = result.get("setup_evidence") or {}
    if setup.get("seed_status") == "ENV_ERROR" and setup.get("load_cases_completed") == 0:
        return [{"priority": "P0", "module": "测试准备 / Recall 验证",
                 "metrics": ", ".join(result.get("selected_metrics") or metrics),
                 "evidence": "种子验证未通过，尚无负载场景；不能归因于调度、原子引擎吞吐或容量。",
                 "action": "用固定事实和自然语言问题检查返回记忆正文，分别记录路由、降级、空召回及命中；前置验证通过后再压测。"}]
    m1, m2, m3, m4, m5, m6 = (metrics.get(f"M{i}", {}) for i in range(1, 7))
    m4_changes = [
        float(value) * 100
        for case in m4.get("cases", [])
        for value in (case.get("degradation_by_tenant") or {}).values()
        if _number(value) is not None
    ]
    m2_jain = [
        value for window in m2.get("windows", [])
        for value in (window.get("commit_throughput_jain"),
                      window.get("search_inverse_p95_jain"))
        if value is not None
    ]
    m3_ratios = [
        tenant.get("p95_ratio")
        for window in m3.get("windows", [])
        for tenant in window.get("tenants", [])
        if tenant.get("p95_ratio") is not None
    ]
    m3_overlaps = [
        window.get("confirmed_overlap") or window.get("overlap") or {}
        for window in m3.get("windows", [])
    ]
    m3_baseline = m3.get("baseline") or {}
    m3_worst_service_rate = min(
        (float(row["recall_service_rate"]) for row in m3_overlaps
         if _number(row.get("recall_service_rate")) is not None),
        default=None,
    )
    m3_503 = sum(int((row.get("http_status") or {}).get("503") or 0) for row in m3_overlaps)
    m3_timeouts = sum(int(row.get("timeouts") or 0) for row in m3_overlaps)
    m3_empty = sum(int(row.get("empty_recall") or 0) for row in m3_overlaps)
    timing_rows = (result.get("timing_evidence") or {}).get("module_timings", [])

    def stage_p95(module: str) -> float | None:
        return next((row.get("p95_ms") for row in timing_rows
                     if row.get("module") == module and row.get("observations")), None)

    atomic_p95 = stage_p95("recall_engine/atomic_engine")
    embedding_p95 = stage_p95("recall/query_embedding")
    recall_total_p95 = stage_p95("recall/recall_total")
    levels = m1.get("levels", [])
    concurrency_rows = m1.get("concurrency_rows", [])
    highest = max((int(level.get("hot_users") or 0) for level in levels), default=None)
    highest_concurrency = max(
        (int(row.get("target_concurrency") or 0) for row in concurrency_rows),
        default=None,
    )
    measured_concurrency = [row for row in concurrency_rows if row.get("target_concurrency") is not None]
    if measured_concurrency:
        m1_evidence = (
            f"并发档 C={','.join(str(row.get('target_concurrency')) for row in measured_concurrency)}；"
            f"最高实际在途={max(int(row.get('peak_inflight_requests') or 0) for row in measured_concurrency)}；"
            f"最高 C={highest_concurrency}。这是并发观测，不等于热用户容量边界。"
        )
    else:
        m1_evidence = (
            f"最高已测热用户档 H={highest}；首个运行异常="
            f"{m1.get('first_operational_anomaly') or '尚未观测'}。"
        )
    if not levels and m3.get("status") != "BLOCKED":
        m1_evidence = (
            f"M3 基线 Recall 服务率={m3_baseline.get('recall_service_rate')}；"
            f"洪泛重叠窗口最低={m3_worst_service_rate}；503={m3_503}，超时={m3_timeouts}。"
        )
    quality_rows = [level.get("search") or {} for level in levels]
    quality_failures = sum(int(row.get("errors") or 0) for row in quality_rows)
    degraded = sum(int(row.get("degraded") or 0) for row in quality_rows)
    fact_hits = sum(int(row.get("fact_hits") or 0) for row in quality_rows)
    fact_observations = sum(int(row.get("fact_hit_observations") or 0) for row in quality_rows)
    recommendations = [
        {"priority": "P0", "module": "Admission 与容量保护", "metrics": "M1 / M3",
         "evidence": m1_evidence,
         "action": "区分 Search 与 Commit 配额，拒绝时返回 tenant、lane、reason_code 和 retry_after；继续升档直到取得真实边界。"},
        {"priority": "P0", "module": "多租户调度", "metrics": "M2 / M3",
         "evidence": (f"已测 Jain 最低={min(m2_jain) if m2_jain else '本轮未测'}；"
                      f"Commit 洪泛重叠窗口 Recall 服务率最低={m3_worst_service_rate}；"
                      f"非空 Recall P95 比率最大={max(m3_ratios) if m3_ratios else '无可比样本'}。"),
         "action": "Commit 按租户轮询或 DRR，并限制单租户在途数；Search 使用独立 lane、worker 和 admission 预算。"},
        {"priority": "P0", "module": "路由与 Search 编排", "metrics": "M1 / M3",
         "evidence": (
             f"M1 观察到 HTTP 200 质量失败 {quality_failures} 次，其中降级 {degraded} 次；"
             f"固定事实命中 {fact_hits}/{fact_observations}。"
             + (f"本轮未选择 M3，未生成洪泛配对窗口。" if m3.get("status") == "BLOCKED"
                else f"M3 洪泛重叠窗口 HTTP 200 空召回={m3_empty}，503={m3_503}，超时={m3_timeouts}。")
         ),
         "action": "为 intent/router、embedding、fanout、merge 分别记录排队和执行耗时；对 intent LLM 非法输出增加结构化重试或确定性回退，不能以 HTTP 200 返回空召回并继续占用下游资源。"},
        {"priority": "P1", "module": "原子引擎 Atomic Engine", "metrics": "M1 / M3 / M6",
         "evidence": (f"本轮结构化日志：Recall Atomic Engine P95={atomic_p95} ms；"
                      f"Query Embedding P95={embedding_p95} ms；Recall Total P95={recall_total_p95} ms。"),
         "action": "继续细分索引读取、候选合并和原子引擎排队；避免 Commit 建索引持有 Search 所需的全局锁，并将阶段指标按 trace_id 与请求关联。"},
        {"priority": "P1", "module": "租户故障隔离", "metrics": "M4",
         "evidence": (f"完整故障用例 {m4.get('complete_cases', 0)}/{m4.get('expected_cases', 0)}；"
                      f"旁观租户最差 P95 变化={max(m4_changes) if m4_changes else None}%。"),
         "action": "按租户隔离并发、重试和 provider 连接预算；慢依赖等待不能长期占用全局 Search permit。"},
        {"priority": "P1", "module": "Commit 持久化与恢复", "metrics": "M5",
         "evidence": f"完整恢复样本 {m5.get('complete_samples', 0)}/{m5.get('expected_samples', 0)}。",
         "action": "保证返回 202 前持久化任务、幂等键和 cursor；扩大到入队后、执行中、落 archive 前后的崩溃矩阵。"},
        {"priority": "P1", "module": "可观测性", "metrics": "M6",
         "evidence": (f"全部采样均完整的单元 {m6.get('complete_cells', 0)}/{m6.get('expected_cells', 0)}；"
                      f"已观察行为：{', '.join(k for k, v in m6.get('scenarios', {}).items() if v is True) or '无'}。"),
         "action": "先补平台采样时间边界并核对启用层分母；确认服务未提供的四元组或进程代际，再补只读观测接口。"},
    ]
    selected = set(result.get("selected_metrics") or metrics)
    return [item for item in recommendations
            if selected.intersection(part.strip() for part in item["metrics"].split("/"))]


def derive_new_findings(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Surface concrete findings from this run, separate from known PR issues."""
    metrics = result.get("metrics", {})
    findings: list[dict[str, Any]] = []
    m1 = metrics.get("M1") or {}
    invalid_output = sum(
        int(((level.get("search") or {}).get("degraded_reason_counts") or {}).get("invalid_output") or 0)
        for level in m1.get("levels", [])
    )
    m1_sent = sum(int((level.get("search") or {}).get("sent") or 0)
                  for level in m1.get("levels", []))
    if invalid_output:
        findings.append({
            "id": "N1", "priority": "P0", "module": "MemRouter / intent",
            "metrics": "M1 / M2 / M3",
            "phenomenon": "Intent LLM 返回 invalid_output 后仍以 HTTP 200 降级继续处理",
            "evidence": f"M1 记录 {invalid_output}/{m1_sent} 个请求带 invalid_output；该字段来自真实服务响应。",
            "impact": "增加无效路由、空召回和额外模型等待；业务成功不能只看 HTTP 200。",
            "action": "对 Intent 输出做结构化校验、有限重试和确定性回退；把降级原因透传到响应及日志，并按租户/路由统计。",
            "relation": "不是 PR534 已列的 intent 缓存缺口；缓存不能修复非法输出。",
        })
    m3 = metrics.get("M3") or {}
    health = m3.get("baseline_health") or {}
    baseline = m3.get("baseline") or {}
    if health.get("status") == "INVALID" and (baseline.get("empty_recall") or 0):
        findings.append({
            "id": "N2", "priority": "P0", "module": "MemRouter / 租户 Recall 可见性",
            "metrics": "M2 / M3",
            "phenomenon": "健康基线自身出现租户级空召回，洪泛前已不具备严格优先级比较资格",
            "evidence": f"基线 Search {baseline.get('planned_or_recorded')} 次，HTTP 错误 {baseline.get('errors')}，空召回 {baseline.get('empty_recall')}，Recall 服务率 {baseline.get('recall_service_rate'):.3f}。",
            "impact": "不能把洪泛前后 P95 差值解释成 Search 优先级；公平性数字即使可计算，业务 Recall 仍不健康。",
            "action": "按 tenant_id、user/session、query、engine_origin 追踪记忆可见性；核对写入完成、索引发布和路由过滤是否一致，再复测健康基线。",
            "relation": "区别于 PR534 的 per-tenant admission 配额缺口：本轮在无 429/503 的基线就出现。",
        })
    correlation = (result.get("timing_evidence") or {}).get("trace_correlation") or {}
    missing_trace = int(correlation.get("requests_missing_trace") or 0)
    eligible = int(correlation.get("eligible_requests") or 0)
    if missing_trace:
        findings.append({
            "id": "N3", "priority": "P1", "module": "入口日志 / 可观测性",
            "metrics": "M1 / M2 / M3",
            "phenomenon": "部分 HTTP 完成事件没有 trace_id，无法逐请求关联内部阶段",
            "evidence": f"{missing_trace}/{eligible} 个可关联请求缺 Trace；其余请求已关联到内部阶段。",
            "impact": "阶段 P95 可以独立统计，但无法对缺 Trace 的请求核验端到端与内部阶段的对应关系。",
            "action": "在 HTTP 入口生成并向下游透传稳定 trace_id；http_request_completed 必须记录同一 trace_id，并增加缺失率门禁。",
            "relation": "不是 PR534 的 lane 四元组缺口；这是请求级关联链路缺失。",
        })
    startup = result.get("startup_cache_diagnostics") or {}
    if startup.get("requested_positions") and startup.get("unique_texts"):
        findings.append({
            "id": "N4", "priority": "P1", "module": "MemRouter / 启动缓存",
            "metrics": "M1-M3 准备阶段",
            "phenomenon": "静态路由模板启动缓存按位置重复请求 Embedding，种子指纹变化时启动预热被放大",
            "evidence": f"本次 develop 指纹变化时请求位置 {startup.get('requested_positions')} 个，规范化后唯一文本 {startup.get('unique_texts')} 个；默认路径约 {startup.get('default_batches')} 批。",
            "impact": "服务启动和首轮压测前置时间显著增加，容易被误认为压测本身挂起；重复文本也放大 Provider 调用。",
            "action": "按规范化文本去重后批量生成并原子写入缓存；缓存指纹变化时输出命中/缺失/唯一文本数，并允许受控并行预热。",
            "relation": "不在 PR534 的 443/514 模块问题清单中；这是本次最新 develop 启动路径核验发现。",
        })
    return findings


def write_observation_report(result: dict[str, Any], path: Path) -> None:
    result.setdefault("new_findings", derive_new_findings(result))
    empty_state = (
        "等待真实样本：种子准备或负载窗口尚未完成"
        if result.get("run_state") == "RUNNING"
        else "本项未产生可用样本"
    )
    def esc(value: Any) -> str:
        if isinstance(value, float):
            value = f"{value:.6g}"
        return html.escape("-" if value is None else str(value))

    def table(rows: list[dict[str, Any]], columns: list[tuple[str, str]], *, min_width_px: int = 0) -> str:
        head = "".join(f"<th>{esc(label)}</th>" for _, label in columns)
        body = "".join("<tr>" + "".join(f"<td>{esc(row.get(key))}</td>" for key, _ in columns) + "</tr>" for row in rows)
        if not body:
            body = f'<tr><td colspan="{len(columns)}">{esc(empty_state)}</td></tr>'
        style = f" style='min-width:{int(min_width_px)}px'" if min_width_px else ""
        return f"<div class='scroll'><table{style}><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"

    def details(label: str, content: str) -> str:
        return f"<details><summary>{esc(label)}</summary>{content}</details>"

    def bars(title: str, points: list[tuple[str, float | None]], *, signed: bool = False,
             axis_max: float | None = None) -> str:
        normalized = [(label, _number(value)) for label, value in points]
        maximum = axis_max or max((abs(value) for _, value in normalized if value is not None), default=0) or 1
        body = "".join(
            f"<div class='bar'><span>{esc(label)}</span><i><b class='{'worse' if signed and value > 0 else 'better'}' style='width:{min(100, 100 * abs(value) / maximum):.2f}%'></b></i><strong>{esc(round(value, 3))}</strong></div>"
            for label, value in normalized if value is not None
        )
        return f"<h3>{esc(title)}</h3>{body or '<p>' + esc(empty_state) + '</p>'}"

    def grouped_bars(title: str, groups: list[tuple[str, list[tuple[str, float | None]]]]) -> str:
        normalized = [
            (group, [(label, _number(value)) for label, value in points])
            for group, points in groups
        ]
        maximum = max((value for _, points in normalized for _, value in points
                       if value is not None), default=0) or 1
        group_html = "".join(
            "<div class='tree-group'><div class='tree-root'><b>" + esc(group) + "</b>"
            "<span>并发档位</span></div><div class='tree-children'>" + "".join(
                f"<div class='tree-node'><span class='tree-label'>{esc(label)}</span>"
                f"<i><b style='width:{min(100, 100 * value / maximum):.2f}%'></b></i>"
                f"<strong>{esc(round(value, 2))} ms</strong></div>"
                for label, value in points if value is not None
            ) + "</div></div>"
            for group, points in normalized
        )
        return f"<h3>{esc(title)}</h3><div class='tree-chart'>{group_html}</div>"

    def hierarchy_bars(title: str, groups: list[tuple[str, list[tuple[int, str, float | None]]]]) -> str:
        normalized = [
            (group, [(depth, label, _number(value)) for depth, label, value in nodes])
            for group, nodes in groups
        ]
        maximum = max((value for _, nodes in normalized for _, _, value in nodes
                       if value is not None), default=0) or 1
        group_html = "".join(
            "<div class='hierarchy-group' style='background:#f3f7f7;border-left:3px solid #17746a'>"
            "<div class='tree-root'><b>" + esc(group)
            + "</b><span>同一批请求</span></div><div class='hierarchy-children' style='padding:5px 10px 5px 14px'>" + "".join(
                f"<div class='hierarchy-node' style='margin-left:{depth * 24}px;display:grid;grid-template-columns:1fr 1.2fr;gap:3px 8px;padding:7px 0 4px'>"
                f"<span class='hierarchy-label' style='font-size:12px;color:#40565f;grid-column:1/-1'>{esc(label)}</span>"
                f"<i style='display:block;height:8px;background:#dde6e7'><b style='display:block;height:100%;background:#17746a;width:{min(100, 100 * value / maximum):.2f}%'></b></i>"
                f"<strong style='font-size:12px;text-align:right'>{esc(round(value, 2))} ms</strong></div>"
                for depth, label, value in nodes if value is not None
            ) + "</div></div>"
            for group, nodes in normalized
        )
        return f"<h3>{esc(title)}</h3><div class='hierarchy-chart' style='display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px'>{group_html}</div>"

    def outcome_bars(title: str, rows: list[dict[str, Any]]) -> str:
        body = "".join(
            f"<div class='outcome-row'><b>{esc(row.get('label'))}</b>"
            "<div class='outcome-track'>"
            f"<i class='served' style='width:{row.get('served_rate', 0):.3f}%'></i>"
            f"<i class='empty' style='width:{row.get('empty_rate', 0):.3f}%'></i>"
            f"<i class='failed' style='width:{row.get('failed_rate', 0):.3f}%'></i>"
            "</div>"
            f"<span>非空 {esc(round(row.get('served_rate', 0), 2))}% · "
            f"空召回 {esc(round(row.get('empty_rate', 0), 2))}% · "
            f"异常 {esc(round(row.get('failed_rate', 0), 2))}% "
            f"（{esc(row.get('served'))}/{esc(row.get('sent'))} 非空）</span></div>"
            for row in rows
        )
        return (
            f"<h3>{esc(title)}</h3><div class='outcome-legend'>"
            "<span class='served'>非空召回</span><span class='empty'>空召回</span>"
            "<span class='failed'>HTTP/传输/超时异常</span></div>"
            f"{body or '<p>' + esc(empty_state) + '</p>'}"
        )

    def paired_m1_stage_timings(metric: dict[str, Any]) -> dict[int, dict[str, Any]]:
        """Build per-concurrency stage percentiles from the same trace set."""
        stage_path = Path(str(
            ((result.get("timing_evidence") or {}).get("stage_collection") or {}).get("path") or ""
        ))
        if not stage_path.is_file():
            return {}
        by_trace: dict[str, dict[str, float]] = {}
        for event in read_stage_events(stage_path):
            trace_ref = str(event.get("trace_ref") or "")
            module = str(event.get("module") or "")
            duration_ms = _number(event.get("duration_ms"))
            if trace_ref and (
                module.startswith("recall/")
                or module.startswith("atomic/")
                or module.startswith("provider/")
            ) and duration_ms is not None:
                by_trace.setdefault(trace_ref, {})[module] = duration_ms
        result_rows: dict[int, dict[str, Any]] = {}
        for level in metric.get("levels", []):
            if level.get("topology") != "concurrency" or level.get("target_concurrency") is None:
                continue
            name = str(level.get("measurement_file") or "")
            if not name or Path(name).name != name:
                continue
            candidates = [
                path.parent / "M1" / "concurrency" / name,
                stage_path.parent / "M1" / "concurrency" / name,
            ]
            measurement_path = next((candidate for candidate in candidates if candidate.is_file()), None)
            if measurement_path is None:
                continue
            try:
                measurement = json.loads(measurement_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            samples = [
                row for row in measurement.get("rows", [])
                if isinstance(row, dict) and row.get("sent") is not False and row.get("trace_ref")
            ]
            traces = [str(row["trace_ref"]) for row in samples]
            stage_stats: dict[str, Any] = {
                "trace_count": len(traces),
                "client_p95_ms": percentile([
                    float(row["elapsed_s"]) * 1000 for row in samples
                    if _number(row.get("elapsed_s")) is not None
                ], 95),
                "client_p99_ms": percentile([
                    float(row["elapsed_s"]) * 1000 for row in samples
                    if _number(row.get("elapsed_s")) is not None
                ], 99),
                "source": "trace_id 配对结构化日志",
            }
            for module in (
                "recall/recall_total", "recall/semantic", "recall/query_embedding",
                "recall/engine_execution", "recall/memory_profile",
                "atomic/extraction", "provider/llm_atom_extraction",
                "provider/llm_atom_extraction_repair", "provider/embedding",
                "provider/dashscope_rerank",
            ):
                values = [by_trace[trace][module] for trace in traces
                          if trace in by_trace and module in by_trace[trace]]
                stage_stats[module] = {
                    "observations": len(values),
                    "p95_ms": percentile(values, 95),
                    "p99_ms": percentile(values, 99),
                }
            result_rows[int(level["target_concurrency"])] = stage_stats
        return result_rows

    selected = set(result.get("selected_metrics", result["metrics"]))
    included = [code for code in METRIC_ORDER if code in selected]
    excluded = [code for code in METRIC_ORDER if code not in selected]
    ordered_metrics = {code: result["metrics"][code] for code in METRIC_ORDER if code in result["metrics"]}
    scope = "六项" if not excluded else " / ".join(included) or "未选择指标"
    profile_label = result.get("instance_profile") or "local"
    title = f"EchoMem {profile_label} {scope}黑盒观测"
    scope_notice = (f"<p>本报告不包含：{esc('、'.join(excluded))}。这些指标未在本次命令中执行，不代表测试失败，也不说明其他运行的进度。</p>"
                    if excluded else "")
    live_seed_progress = """
<section id="live-seed-progress" class="live-progress" hidden>
  <h2>M1 种子实时进度</h2><p id="live-seed-progress-text"></p>
</section>
<script>
(() => {
  const box = document.getElementById('live-seed-progress');
  const text = document.getElementById('live-seed-progress-text');
  const render = (counts, total) => {
    box.hidden = false;
    text.textContent = `正在注入固定记忆：${total} 个租户；Commit 已完成 ${counts.completed || 0}，失败 ${counts.failed || 0}，等待终态 ${counts.pending || 0}。种子完成前不会发送压测 Search 请求，负载请求分母将在种子完成后生成。`;
  };
  const refresh = async () => {
    try {
      const response = await fetch('M1/cross-tenant/seed-progress.json?ts=' + Date.now(), {cache: 'no-store'});
      if (!response.ok) return;
      const rows = Object.values(await response.json());
      const counts = rows.reduce((result, row) => {
        const state = String(row.commit_state || 'pending');
        result[state] = (result[state] || 0) + 1;
        return result;
      }, {});
      render(counts, rows.length);
    } catch (_) {
      // The progress file does not exist before M1 starts or after cleanup.
    }
  };
  refresh();
  window.setInterval(refresh, 5000);
})();
</script>
""" if "M1" in selected else ""
    model_preflight = result.get("model_preflight") or {}
    model_rows = model_preflight.get("engines") or []
    verified_kinds = {
        str(row.get("kind") or "") for row in model_rows
        if row.get("status") == "ok" and row.get("model_supported") is True
    }
    models_verified = bool(model_preflight.get("ok")) and {"llm", "embedding"}.issubset(verified_kinds)
    timing_evidence = result.get("timing_evidence") or {}
    model_stage_modules = sorted({
        str(row.get("module") or "")
        for row in timing_evidence.get("module_timings", [])
        if row.get("observations") and any(marker in str(row.get("module") or "") for marker in (
            "recall/llm", "query_embedding", "provider/", "memory_extraction"
        ))
    })
    if models_verified and model_stage_modules:
        model_verdict = "真实模型预检通过，且压测期间观察到模型相关阶段调用"
        model_class = "MEASURED"
    elif models_verified:
        model_verdict = "真实模型预检通过，但没有压测期间模型调用证据，不能宣称负载使用了模型"
        model_class = "PARTIAL"
    elif model_rows:
        model_verdict = "模型预检失败：不能声称本次使用了可用的真实模型"
        model_class = "BLOCKED"
    else:
        model_verdict = "未记录模型预检证据：无法判断本次是否使用真实模型"
        model_class = "PARTIAL"
    model_table = table(model_rows, [
        ("kind", "类型"), ("id", "配置路径/用途"), ("model", "模型"),
        ("api_base", "Endpoint"), ("status", "真实请求状态"),
        ("model_supported", "返回结构有效"), ("code", "HTTP"),
        ("elapsed_s", "预检耗时秒"), ("error", "失败原因"),
    ], min_width_px=1100) if model_rows else f"<p>{esc(empty_state)}；模型预检明细尚未写入。</p>"
    model_section = (
        "<section class='model-evidence'><h2>本次使用的模型</h2>"
        f"<p class='{model_class}'><b>{esc(model_verdict)}</b></p>"
        f"<p>配置指纹：<code>{esc(model_preflight.get('digest'))}</code>；"
        f"预检尝试：{esc(model_preflight.get('probe_attempts'))}；"
        f"检查引擎数：{esc(model_preflight.get('engines_checked'))}；"
        f"负载期模型阶段：{esc('、'.join(model_stage_modules) or '未观测')}。API Key 不写入报告。</p>" +
        model_table + "</section>"
    )
    cards = "".join(
        f"<article><b>{code}</b><h2>{esc(METRIC_NAMES[code])}</h2>"
        f"<span class='{status}'>{label}</span>"
        f"<p>{esc(METRIC_PURPOSES[code])}</p><small>{esc(reason)}</small></article>"
        for code, metric in ordered_metrics.items() if code in selected
        for status, label, reason in [
            ("PARTIAL", "进行中", "等待种子与负载样本")
            if result.get("run_state") == "RUNNING" and metric.get("status") == "BLOCKED"
            else (metric["status"], metric["status"], metric.get("reason"))
        ]
    )
    sections = []
    for code, metric in ordered_metrics.items():
        if code not in selected:
            continue
        visual = ""
        headline_visual = ""
        if code == "M1":
            def milliseconds(value: Any) -> float | None:
                value = _number(value)
                return round(value * 1000, 3) if value is not None else None

            visual = (
                "<p><b>Search 成功统计：</b>HTTP 200 且返回非空、未被意图拒绝的 Recall 即计为成功。"
                "事实/答案命中（quality_ok）仅作诊断，不参与 M1-M3 的通过、容量或公平性计算。</p>"
                f"<p><b>客户端并发目标：</b>{esc(metric.get('required_concurrency'))}；"
                f"<b>实测 Search 在途峰值：</b>{esc(metric.get('peak_inflight_requests'))}；"
                f"<b>是否真正达到目标：</b>{esc(metric.get('concurrency_target_observed'))}。"
                "热用户数和同时在途请求不是同一个概念，报告分别保留。</p>"
            )
            visual += (
                f"<p><b>记忆准备：</b>{esc(metric.get('seed_contract'))}；"
                f"策略={esc(metric.get('seed_memory_policy'))}，"
                f"状态={esc(metric.get('seed_status'))}，"
                f"session={esc(metric.get('seed_session_count'))}，"
                f"消息/文档={esc(metric.get('seed_total_documents'))}，"
                f"字符={esc(metric.get('seed_total_input_characters'))}。</p>"
            )
            visual += details("查看每个租户的完整 LoCoMo session 与 Commit 结果", table(
                metric.get("seed_assignments", []), [
                    ("tenant_index", "租户序号"), ("sample_id", "LoCoMo sample"),
                    ("session_key", "完整 session"), ("session_messages", "原始消息数"),
                    ("input_documents", "注入文档数"), ("input_characters", "字符数"),
                    ("semantic_queries", "Search题数"), ("commit_http_status", "Commit HTTP"),
                    ("commit_state", "Commit终态"), ("elapsed_s", "注入耗时秒"),
                    ("status", "状态"),
                ], min_width_px=1200))
            comparison = metric.get("memory_profile_comparison") or {}
            paired_stage_rows = paired_m1_stage_timings(metric)

            def stage_stat(level: dict[str, Any], metric_name: str, stage_name: str, field: str = "p95_s") -> Any:
                for sample in ((level.get("server_stage_timings") or {}).get("prometheus_histograms") or []):
                    if sample.get("metric") == metric_name and (sample.get("labels") or {}).get("stage") == stage_name:
                        return milliseconds(sample.get(field))
                return None

            concurrency_rows = []
            for level in metric.get("levels", []):
                search = level.get("search") or {}
                status_counts = search.get("http_status_counts") or {}
                route = search.get("route_path_timings") or {}
                intent = route.get("intent_llm") or {}
                paired = paired_stage_rows.get(int(level.get("target_concurrency") or 0), {})
                paired_module = lambda name: (paired.get(name) or {}).get("p95_ms")
                concurrency_rows.append({
                    "concurrency": level.get("target_concurrency"),
                    "peak_inflight": search.get("peak_inflight_requests"),
                    "observations": search.get("latency_observations"),
                    "p50_ms": milliseconds(search.get("p50_s")),
                    "p95_ms": paired.get("client_p95_ms") or milliseconds(search.get("p95_s")),
                    "p99_ms": paired.get("client_p99_ms") or milliseconds(search.get("p99_s")),
                    "intent_llm_p95_ms": milliseconds(intent.get("p95_s")),
                    "recall_total_p95_ms": paired_module("recall/recall_total"),
                    "query_embedding_p95_ms": paired_module("recall/query_embedding") or stage_stat(level, "echomem_memrouter_stage_duration_seconds", "query_embedding"),
                    "semantic_p95_ms": paired_module("recall/semantic") or stage_stat(level, "echomem_memrouter_stage_duration_seconds", "semantic"),
                    "engine_execution_p95_ms": paired_module("recall/engine_execution") or stage_stat(level, "echomem_memrouter_stage_duration_seconds", "engine_execution"),
                    "memory_profile_p95_ms": paired_module("recall/memory_profile") or stage_stat(level, "echomem_memrouter_stage_duration_seconds", "memory_profile"),
                    "atomic_extraction_p95_ms": paired_module("atomic/extraction"),
                    "llm_provider_p95_ms": max(
                        (paired_module(name) for name in (
                            "provider/llm_atom_extraction",
                            "provider/llm_atom_extraction_repair",
                        ) if paired_module(name) is not None),
                        default=None,
                    ),
                    "embedding_provider_p95_ms": paired_module("provider/embedding"),
                    "rerank_p95_ms": paired_module("provider/dashscope_rerank"),
                    "stage_timing_source": paired.get("source") or "Prometheus Histogram fallback",
                    "paired_trace_count": paired.get("trace_count", 0),
                    "query_embedding_queue_p95_ms": stage_stat(level, "echomem_memrouter_stage_queue_wait_seconds", "query_embedding"),
                    "engine_queue_p95_ms": stage_stat(level, "echomem_memrouter_stage_queue_wait_seconds", "engine_execution"),
                    "llm_queue_p95_ms": stage_stat(level, "echomem_memrouter_stage_queue_wait_seconds", "llm"),
                    "http_200": status_counts.get("200", 0),
                    "http_503": status_counts.get("503", 0),
                    "http_429": status_counts.get("429", 0),
                    "timeouts": (search.get("error_breakdown") or {}).get("timeout_censored", search.get("timeout_censored", 0)),
                    "sent": search.get("sent", 0),
                    "recall_served": search.get("recall_served", search.get("nonempty_results", 0)),
                    "empty_recall": search.get("empty_results", 0),
                    "request_errors": search.get("transport_or_http_errors", search.get("errors", 0)),
                })
            concurrency_rows = [row for row in concurrency_rows if row.get("concurrency") is not None]
            visual += details(
                "并发档位端到端与模块耗时对比",
                "<p>端到端 P95 是客户端观测；模块 P95 和 queue wait P95 均来自该档位的 Prometheus 直方图窗口增量，不能把端到端耗时减去某阶段来推算其他模块。HTTP 200/503/429 和超时单独列出。</p>" +
                table(concurrency_rows, [
                    ("concurrency", "目标并发"), ("peak_inflight", "实际峰值在途"),
                    ("observations", "端到端样本"), ("p50_ms", "端到端 P50(ms)"),
                    ("p95_ms", "端到端 P95(ms)"), ("p99_ms", "端到端 P99(ms)"),
                    ("intent_llm_p95_ms", "Intent LLM P95(ms)"),
                    ("recall_total_p95_ms", "Recall Total P95(ms)"),
                    ("query_embedding_p95_ms", "Query Embedding P95(ms)"),
                    ("semantic_p95_ms", "Semantic P95(ms)"),
                    ("engine_execution_p95_ms", "Engine Execution P95(ms)"),
                    ("memory_profile_p95_ms", "Memory Profile P95(ms)"),
                    ("atomic_extraction_p95_ms", "Atomic extraction P95(ms)"),
                    ("llm_provider_p95_ms", "LLM provider P95(ms)"),
                    ("embedding_provider_p95_ms", "Embedding provider P95(ms)"),
                    ("rerank_p95_ms", "Rerank P95(ms)"),
                    ("query_embedding_queue_p95_ms", "Query Embedding Queue P95(ms)"),
                    ("engine_queue_p95_ms", "Engine Queue P95(ms)"),
                    ("llm_queue_p95_ms", "LLM Queue P95(ms)"),
                    ("http_200", "HTTP 200"), ("http_503", "HTTP 503"),
                    ("http_429", "HTTP 429"), ("timeouts", "超时"),
                    ("stage_timing_source", "阶段耗时来源"),
                    ("paired_trace_count", "配对 Trace 数"),
                ], min_width_px=2200)
            )
            stage_bars = []
            for row in concurrency_rows:
                for key, label in (
                    ("p95_ms", "端到端"),
                    ("intent_llm_p95_ms", "Intent LLM"),
                    ("query_embedding_p95_ms", "Query Embedding"),
                    ("engine_execution_p95_ms", "Engine Execution"),
                    ("memory_profile_p95_ms", "Memory Profile"),
                    ("atomic_extraction_p95_ms", "Atomic extraction"),
                    ("llm_provider_p95_ms", "LLM provider"),
                    ("embedding_provider_p95_ms", "Embedding provider"),
                    ("rerank_p95_ms", "Rerank"),
                ):
                    if row.get(key) is not None:
                        stage_bars.append((f"C={row.get('concurrency')} {label}", row[key]))
            visual += bars("各并发档位模块 P95 对比（ms）", stage_bars)
            if concurrency_rows:
                slowest = max(concurrency_rows, key=lambda row: row.get("p95_ms") or -1)
                dominant = max(
                    (("Intent LLM", slowest.get("intent_llm_p95_ms")),
                     ("Query Embedding/Semantic", slowest.get("semantic_p95_ms")),
                     ("Engine Execution", slowest.get("engine_execution_p95_ms"))),
                    key=lambda item: item[1] if item[1] is not None else -1,
                )
                visual += f"<p><b>耗时结论：</b>最高端到端 P95 出现在 {esc(slowest.get('concurrency'))} 并发；在该档位可观测模块中，P95 最大的是 {esc(dominant[0])}（{esc(dominant[1])} ms）。memory_profile 仅表示画像阶段本身，不等于整条 Recall 耗时。Atomic extraction、LLM provider、Embedding provider 或 Rerank 显示为缺失时，表示该并发窗口没有对应 trace 样本，不能解释为 0 ms。</p>"
            if comparison.get("comparison_ready"):
                visual += f"<p><b>16→64 memory_profile 放大：</b>{esc(comparison.get('p95_amplification'))}；该结论只在两档都有真实阶段样本时成立。</p>"
            visual += bars("已测负载曲线：Search P95 ms", [
                (f"{level.get('topology')} H={level.get('hot_users')} {level.get('load_mode')}",
                 (level.get("search") or {}).get("p95_s") * 1000
                 if (level.get("search") or {}).get("p95_s") is not None else None)
                for level in metric.get("levels", [])
            ])
            visual += details("查看各档吞吐与完成状态", table(metric.get("levels", []), [("topology", "拓扑"), ("hot_users", "热用户"), ("load_mode", "负载"), ("status", "数据状态"), ("sent_search_rps", "Search发送/s"), ("effective_search_rps", "Search完成/s")]))
            level_stage_rows = []
            for level in metric.get("levels", []):
                for stage in ((level.get("server_stage_timings") or {}).get("prometheus_histograms") or []):
                    level_stage_rows.append({
                        "topology": level.get("topology"),
                        "hot_users": level.get("hot_users"),
                        "load_mode": level.get("load_mode"),
                        "module": stage.get("module"),
                        "labels": stage.get("labels"),
                        "observations": stage.get("observations"),
                        "p50_ms": (stage.get("p50_s") * 1000
                                   if stage.get("p50_s") is not None else None),
                        "p95_ms": (stage.get("p95_s") * 1000
                                   if stage.get("p95_s") is not None else None),
                        "p99_ms": (stage.get("p99_s") * 1000
                                   if stage.get("p99_s") is not None else None),
                    })
            visual += details("查看每个 16/64 档位的模块直方图耗时", table(level_stage_rows, [
                ("topology", "拓扑"), ("hot_users", "租户数"), ("load_mode", "负载"),
                ("module", "Prometheus模块"), ("labels", "标签"),
                ("observations", "样本"), ("p50_ms", "P50 ms"),
                ("p95_ms", "P95 ms"), ("p99_ms", "P99 ms"),
            ], min_width_px=1300))
            failure_rows = []
            for level in metric.get("levels", []):
                search = level.get("search") or {}
                breakdown = search.get("error_breakdown") or {}
                partition = breakdown.get("outcome_partition") or {}
                failure_rows.append({
                    "topology": level.get("topology"),
                    "hot_users": level.get("hot_users"),
                    "load_mode": level.get("load_mode"),
                    "sent": breakdown.get("denominator_sent", search.get("sent")),
                    "strict_success": partition.get("strict_success", search.get("success")),
                    "quality_failures": breakdown.get("http_200_quality_failures"),
                    "http_non_200": breakdown.get("http_non_200"),
                    "transport_errors": breakdown.get("transport_errors"),
                    "reason_codes": breakdown.get("reason_code_counts"),
                    "failure_domains": breakdown.get("failure_domain_counts"),
                    "provider_codes": breakdown.get("provider_error_code_counts"),
                    "provider_evidence": breakdown.get("provider_evidence_available"),
                    "unclassified": breakdown.get("unclassified_failures"),
                    "partition_complete": breakdown.get("partition_complete"),
                })
            visual += details("查看 M1 错误、API 异常与失败责任域", table(failure_rows, [
                ("topology", "拓扑"), ("hot_users", "热用户"), ("load_mode", "负载"),
                ("sent", "已发出分母"), ("strict_success", "严格成功"),
                ("quality_failures", "HTTP 200质量失败"), ("http_non_200", "HTTP非200"),
                ("transport_errors", "传输错误"), ("reason_codes", "EchoMem reason_code"),
                ("failure_domains", "失败责任域"), ("provider_codes", "Provider/API错误码"),
                ("provider_evidence", "Provider证据已采集"), ("unclassified", "未分类"),
                ("partition_complete", "分母闭合"),
            ], min_width_px=1500))
            visual += "<p>只有明确的 Provider/API 错误码或服务端安全日志才归因为模型、额度或 API Key；EchoMem 429、引擎降级和网络超时分别统计。Provider 证据未采集时，空错误码不等于外部 API 一定正常。</p>"
            anomaly = metric.get("first_operational_anomaly") or {}
            if anomaly.get("kind") == "congestion":
                visual += f"<p><b>已观测拥塞档：{esc(anomaly.get('hot_users'))} 热用户，负载 {esc(anomaly.get('load_profile'))}。</b>已按持续阻塞规则停止加压；这不是稳定承载量，也不证明服务崩溃。停压后恢复情况另列。</p>"
                visual += details("查看拥塞停止规则与逐窗口分母", "<pre>" + esc(json.dumps(anomaly, ensure_ascii=False, indent=2)) + "</pre>")
            visual += details("查看 DAU 画像换算", table(metric.get("dau_scenarios", []), [("name", "DAU情景"), ("searches_per_user_day", "Search/日"), ("commits_per_user_day", "Commit/日"), ("peak_to_average_ratio", "峰均比"), ("traffic_equivalent_dau", "流量等价DAU"), ("is_measured_maximum", "实测最大值")]))
            resource_rows = [{"topology": level.get("topology"), "hot_users": level.get("hot_users"),
                              "load_mode": level.get("load_mode"), **sample}
                             for level in metric.get("levels", []) for sample in level.get("resources", [])]
            visual += details("查看 CPU、内存逐点采样", table(resource_rows, [("topology", "拓扑"), ("hot_users", "热用户"), ("load_mode", "负载"), ("at_epoch_s", "时间"), ("cpu_percent_one_core_100", "CPU% (100%=1核)"), ("rss_bytes", "RSS bytes"), ("phase", "阶段")]))
            outcome_rows = []
            for row in concurrency_rows:
                sent = max(1, int(row.get("sent") or 0))
                served = int(row.get("recall_served") or 0)
                empty = int(row.get("empty_recall") or 0)
                failed = int(row.get("request_errors") or 0)
                outcome_rows.append({
                    "label": f"C={row.get('concurrency')}", "sent": row.get("sent"),
                    "served": served, "served_rate": 100 * served / sent,
                    "empty_rate": 100 * empty / sent, "failed_rate": 100 * failed / sent,
                })
            concurrency_baseline = concurrency_rows[0] if concurrency_rows else {}
            stage_keys = (
                ("Search 端到端", "p95_ms"),
                ("Recall Total", "recall_total_p95_ms"),
                ("Query Embedding", "query_embedding_p95_ms"),
                ("Semantic（含 Embedding）", "semantic_p95_ms"),
                ("Engine Execution", "engine_execution_p95_ms"),
                ("Memory Profile", "memory_profile_p95_ms"),
            )
            stage_groups = []
            for row in concurrency_rows:
                points = []
                for label, key in stage_keys:
                    value = row.get(key)
                    baseline_value = concurrency_baseline.get(key)
                    ratio = (
                        value / baseline_value
                        if value is not None and baseline_value not in (None, 0)
                        else None
                    )
                    ratio_label = f"{label} · {ratio:.2f}x" if ratio is not None else label
                    points.append((ratio_label, value))
                stage_groups.append((f"C={row.get('concurrency')}", [
                    (0, points[0][0], points[0][1]),
                    (1, points[1][0], points[1][1]),
                    (2, points[2][0], points[2][1]),
                    (3, points[3][0], points[3][1]),
                    (2, points[4][0], points[4][1]),
                    (2, points[5][0], points[5][1]),
                ]))
            paired_complete = bool(concurrency_rows) and all(
                int(row.get("paired_trace_count") or 0) == int(row.get("sent") or 0)
                for row in concurrency_rows
            )
            pairing_message = (
                "本图所有展示档位均使用 measurement 与结构化日志中相同 trace_id 的完整配对样本；"
                "Semantic 是 Recall Total 的子阶段，可以直接检查单请求包含关系。"
                if paired_complete else
                "部分档位缺少完整 trace_id 配对，缺失档位回退为 Prometheus Histogram；"
                "回退值只用于趋势观察，不能与客户端端到端 P95 做包含关系判断。"
            )
            headline_visual = (
                "<div class='primary-charts'>"
                "<figure>" + hierarchy_bars("不同并发下 Query / Recall 阶段 P95（ms，括号为相对 C=1 放大倍数）", stage_groups)
                + f"<p class='chart-warning'><b>配对口径：</b>{esc(pairing_message)}</p>"
                + "<p class='chart-note'>包含关系：Search 端到端 → Recall Total → Semantic → Query Embedding；Memory Profile 和 Engine Execution 是 Recall Total 下的并行阶段。放大倍数 = 当前并发档 P95 ÷ C=1 P95；C=1 固定为 1.00x。阶段不可相加；Search 端到端还包含 Recall 外层编排和网络开销。</p></figure>"
                "<figure>" + outcome_bars("不同并发下空召回率与请求结果", outcome_rows)
                + "<p class='chart-note'>空召回指 HTTP 成功但返回结果为空；异常包含 HTTP 非 2xx、传输错误和超时。每个档位的比例以本轮实际已发请求为分母。</p></figure>"
                "</div>"
            )
        elif code == "M4":
            points = []
            worst_rows = []
            all_degradations = []
            worst_by_fault: dict[str, list[float]] = {}
            recovery_seconds = []
            phase_totals = {
                phase: {"phase": phase, "submitted": 0, "strict_success": 0}
                for phase in ("before", "during", "after")
            }
            for case in metric.get("cases", []):
                values = [_number(value) for value in (case.get("degradation_by_tenant") or {}).values()]
                values = [value * 100 for value in values if value is not None]
                all_degradations.extend(values)
                if values:
                    worst_by_fault.setdefault(str(case.get("fault_type") or "unknown"), []).append(max(values))
                    worst_tenant, worst_ratio = max(
                        (case.get("degradation_by_tenant") or {}).items(),
                        key=lambda item: _number(item[1]) if _number(item[1]) is not None else -math.inf,
                    )
                    before_tenant = ((case.get("before") or {}).get("by_tenant") or {}).get(worst_tenant) or {}
                    during_tenant = ((case.get("during") or {}).get("by_tenant") or {}).get(worst_tenant) or {}
                    worst_rows.append({
                        "target_tenant": case.get("target_tenant"),
                        "fault_type": case.get("fault_type"),
                        "repetition": case.get("repetition"),
                        "worst_bystander": worst_tenant,
                        "before_p95_s": before_tenant.get("p95_s"),
                        "during_p95_s": during_tenant.get("p95_s"),
                        "change_pct": _number(worst_ratio) * 100 if _number(worst_ratio) is not None else None,
                    })
                recovery = _number(case.get("target_recovery_observed_s"))
                if recovery is not None:
                    recovery_seconds.append(recovery)
                for phase in phase_totals:
                    by_tenant = (case.get(phase) or {}).get("by_tenant") or {}
                    for tenant_id, tenant in by_tenant.items():
                        if tenant_id == case.get("target_tenant") or not isinstance(tenant, dict):
                            continue
                        phase_totals[phase]["submitted"] += int(tenant.get("submitted") or 0)
                        phase_totals[phase]["strict_success"] += int(tenant.get("quality_ok") or 0)
                points.append((f"{case.get('fault_type')} · {case.get('target_tenant')} · #{case.get('repetition')}",
                               max(values) if values else None))
            for totals in phase_totals.values():
                submitted = totals["submitted"]
                totals["strict_failures"] = submitted - totals["strict_success"]
                totals["strict_success_rate_pct"] = (
                    totals["strict_success"] / submitted * 100 if submitted else None
                )
            all_degradations.sort()
            median = (all_degradations[len(all_degradations) // 2]
                      if all_degradations else None)
            case_count = len(metric.get("cases", []))
            interpretation = (
                "<div class='explain'><h3>这组 M4 数据怎么读</h3><ol>"
                "<li><b>故障注入：</b>测试平台携带受保护 token 调用 EchoMem 的故障控制接口，只对目标 tenant 开启 reject 或 delay；每个租户使用独立请求池。每轮执行正常基线、故障中和关闭故障后的恢复采样。</li>"
                f"<li><b>分母：</b>{esc(case_count)} 个用例 = 目标租户 × 故障类型 × 重复次数；"
                f"每个用例同时观察其他租户，共形成 {esc(len(all_degradations))} 组 before/during P95 配对。</li>"
                "<li><b>公式：</b>P95 变化 = (故障中 P95 / 故障前 P95 - 1) × 100%。正数表示旁观租户变慢，负数只表示本轮更快，通常属于波动，不能解释成隔离能力超过 100%。</li>"
                "<li><b>柱子：</b>每根柱只画该用例所有旁观租户中的最差变化；它不是目标故障租户自己的延迟，也不是三个旁观租户的平均值。</li>"
                "<li><b>延迟与质量：</b>P95 覆盖本轮已返回的 Search 请求；HTTP 与召回质量严格成功另算。延迟、失败和空召回必须一起看。</li></ol>"
                f"<p><b>本次观测：</b>{esc(len(all_degradations))} 组旁观租户配对的平均变化为 "
                f"{esc(round(sum(all_degradations) / len(all_degradations), 3) if all_degradations else None)}%，"
                f"中位数约 {esc(round(median, 3) if median is not None else None)}%，范围 "
                f"{esc(round(min(all_degradations), 3) if all_degradations else None)}% 至 "
                f"{esc(round(max(all_degradations), 3) if all_degradations else None)}%。"
                f"目标租户关闭故障后首次 HTTP 成功平均为 "
                f"{esc(round(sum(recovery_seconds) / len(recovery_seconds), 3) if recovery_seconds else None)} 秒，范围 "
                f"{esc(round(min(recovery_seconds), 3) if recovery_seconds else None)} 至 "
                f"{esc(round(max(recovery_seconds), 3) if recovery_seconds else None)} 秒。</p>"
                "<p><b>解释边界：</b>劣化百分比较小并不自动代表隔离良好。若严格成功率偏低，说明大量请求已在 HTTP、召回质量或降级路径上失败；必须把下面的成功/失败分母与 P95 一起判断。</p></div>"
            )
            visual = interpretation
            visual += table(list(phase_totals.values()), [
                ("phase", "阶段"), ("submitted", "旁观租户请求"),
                ("strict_success", "HTTP+质量严格成功"),
                ("strict_failures", "严格失败"),
                ("strict_success_rate_pct", "严格成功率 %"),
            ])
            visual += bars("各故障用例最差旁观租户 Search P95 变化 %", points, signed=True)
            visual += details("查看每根柱子的旁观租户与 P95 原值", table(worst_rows, [
                ("target_tenant", "故障目标租户"), ("fault_type", "故障"),
                ("repetition", "轮次"), ("worst_bystander", "最差旁观租户"),
                ("before_p95_s", "故障前 P95 秒"),
                ("during_p95_s", "故障中 P95 秒"), ("change_pct", "变化 %"),
            ], min_width_px=1000))
            visual += details("查看 24 个故障用例状态", table(metric.get("cases", []), [("target_tenant", "目标租户"), ("fault_type", "故障"), ("repetition", "重复"), ("fault_observed", "实际生效"), ("fault_disable_acknowledged", "关闭指令确认"), ("target_http_responding", "关闭后有成功响应"), ("fault_recovered", "关闭后全部请求质量成功")]))
            visual += '<p>关闭指令确认、接口有成功响应、所有请求召回质量成功是三种不同证据；最后一项为否不等于故障开关未关闭。未采集字段显示缺失，不据此推定恢复或失败。</p>'
            visual += details("查看目标租户恢复分母", table(metric.get("cases", []), [("target_tenant", "目标租户"), ("fault_type", "故障"), ("repetition", "重复"), ("target_after_submitted", "关闭后请求"), ("target_after_http_success", "HTTP 成功"), ("target_after_quality_success", "HTTP 与质量均成功"), ("target_recovery_observed_s", "关闭起至首次 HTTP 成功秒")]))
        elif code == "M2":
            visual = bars("公平指数（越接近 1 越均匀）", [
                (f"{window.get('tenant_count')}租户 Commit Jain", window.get("commit_throughput_jain"))
                for window in metric.get("windows", [])
            ] + [
                (f"{window.get('tenant_count')}租户 Search Jain", window.get("search_inverse_p95_jain"))
                for window in metric.get("windows", [])
            ], axis_max=1)
            fairness_rows = [{"scenario": window.get("scenario"), "tenant_count": window.get("tenant_count"),
                              "duration_s": window.get("duration_s"),
                              "commit_throughput_jain": window.get("commit_throughput_jain"),
                              "search_inverse_p95_jain": window.get("search_inverse_p95_jain"),
                              "search_health_status": (window.get("search_health") or {}).get("status"),
                              "search_recall_service_rate": (window.get("search_health") or {}).get("recall_service_rate"),
                              "search_errors": (window.get("search_health") or {}).get("errors"),
                              "search_timeouts": (window.get("search_health") or {}).get("timeouts"),
                              "search_empty_recall": (window.get("search_health") or {}).get("empty_recall"),
                              "search_health_reason": (window.get("search_health") or {}).get("reason")}
                             for window in metric.get("windows", [])]
            visual += details("查看公平窗口汇总", table(fairness_rows, [("scenario", "场景"), ("tenant_count", "租户"), ("duration_s", "窗口秒"), ("commit_throughput_jain", "Commit Jain"), ("search_inverse_p95_jain", "Search inverse-P95 Jain"), ("search_health_status", "Search 健康"), ("search_recall_service_rate", "非空 Recall 服务率"), ("search_errors", "错误"), ("search_timeouts", "超时"), ("search_empty_recall", "空召回"), ("search_health_reason", "解释")]))
            tenant_rows = [{"scenario": window.get("scenario"), **tenant,
                            "search_attempt_p95_ms": tenant.get("search", {}).get("p95_ms"),
                            "search_recall_p95_ms": tenant.get("search", {}).get("served_p95_ms"),
                            "search_count": tenant.get("search", {}).get("completed"),
                            "search_errors": tenant.get("search", {}).get("errors"),
                            "search_recall_served": tenant.get("search", {}).get("recall_served"),
                            "search_recall_errors": tenant.get("search", {}).get("recall_service_errors"),
                            "search_recall_service_rate": tenant.get("search", {}).get("recall_service_rate"),
                            "search_empty": tenant.get("search", {}).get("empty_recall"),
                            "search_quality_ok": tenant.get("search", {}).get("quality_ok"),
                            "search_mean_ms": tenant.get("search", {}).get("mean_ms")}
                           for window in metric.get("windows", []) for tenant in window.get("tenants", [])]
            visual += bars("各租户窗口内 Commit 完成数", [
                (f"{row['scenario']} / 租户 {row['tenant_index']}", row.get("commit_completed")) for row in tenant_rows])
            visual += bars("各租户非空 Recall P95 / ms", [
                (f"{row['scenario']} / 租户 {row['tenant_index']}", row.get("search_recall_p95_ms")) for row in tenant_rows])
            visual += '<p>Commit 吞吐 = 窗口内完成数 ÷ 窗口秒数；Search Jain 使用非空 Recall 的 1/P95（越大越快）。两者分别计算 J=(Σx)²/(n×Σx²)，n 包含零完成租户。J 接近 1 只表示幸存请求的延迟较均匀，不表示吞吐高、延迟低、空召回少或长期稳态已得到证明。</p>'
            visual += details("查看逐租户完成数与延迟", table(tenant_rows, [("scenario", "场景"), ("tenant_index", "租户"), ("commit_submitted", "窗口内提交"), ("commit_accepted", "全程受理"), ("commit_completed", "窗口内完成"), ("commit_completed_after_window", "停压后完成"), ("commit_failed", "全程失败"), ("commit_pending", "观察截止未确认"), ("longest_no_completion_s", "窗口内最长无完成秒"), ("search_attempt_p95_ms", "全部尝试 P95 ms"), ("search_recall_p95_ms", "非空 Recall P95 ms"), ("search_recall_service_rate", "Recall 服务率")]))
            visual += '<p>观察截止未确认不等于永久失败；后续原任务完成不能回填历史窗口吞吐。状态轮询也是服务负载，下面统计整个场景（含排空），不与测量窗口 Search 请求数混用。</p>'
            visual += details("查看状态轮询额外负载", table(tenant_rows, [("scenario", "场景"), ("tenant_index", "租户"), ("commit_poll_count_full_run", "全程状态请求数"), ("commit_poll_http_errors_full_run", "状态 HTTP/传输错误")]))
            visual += details("查看 Search 错误与召回质量", table(tenant_rows, [("scenario", "场景"), ("tenant_index", "租户"), ("search_count", "请求数"), ("search_errors", "HTTP/传输错误"), ("search_recall_served", "非空 Recall"), ("search_recall_service_rate", "Recall 服务率"), ("search_recall_errors", "Recall 服务失败"), ("search_empty", "HTTP 200空召回"), ("search_quality_ok", "事实质量通过"), ("search_attempt_p95_ms", "全部尝试 P95 ms"), ("search_recall_p95_ms", "非空 Recall P95 ms")]))
            arrival_rows = [{"scenario": row["scenario"], "tenant_index": row["tenant_index"], "task": task, **values}
                            for row in tenant_rows for task, values in row.get("arrivals", {}).items()]
            visual += details("查看计划到达与实际发压", table(arrival_rows, [("scenario", "场景"), ("tenant_index", "租户"), ("task", "路径"), ("planned", "计划启动"), ("started_in_window", "窗口内启动"), ("missing_starts", "未启动"), ("duplicate_starts", "重复"), ("start_lag_p95_ms", "发压延迟 P95 ms")]))
            visual += details("查看证据完整性", table(metric.get("windows", []), [("scenario", "场景"), ("evidence_complete", "完整"), ("evidence_issues", "缺口")]))
            headline_visual = (
                "<div class='chart-grid'>"
                "<figure>" + bars("不同租户之间的 Jain 公平指数", [
                    (f"{window.get('tenant_count')}租户 Commit", window.get("commit_throughput_jain"))
                    for window in metric.get("windows", [])
                ] + [
                    (f"{window.get('tenant_count')}租户 Search", window.get("search_inverse_p95_jain"))
                    for window in metric.get("windows", [])
                ], axis_max=1) + "</figure>"
                "<figure>" + bars("各租户非空 Recall P95（ms）", [
                    (f"{row.get('scenario')} / T{row.get('tenant_index')}", row.get("search_recall_p95_ms"))
                    for row in tenant_rows
                ]) + "</figure></div>"
            )
        elif code == "M3":
            baseline_health = metric.get("baseline_health") or {}
            visual = details("M3 基线健康门禁", table([baseline_health], [
                ("status", "状态"), ("eligible_for_priority_comparison", "可用于优先级对比"),
                ("planned_or_recorded", "Search 样本"), ("recall_served", "非空 Recall"),
                ("recall_service_rate", "Recall 服务率"), ("errors", "HTTP/传输错误"),
                ("timeouts", "超时"), ("empty_recall", "空召回"), ("reasons", "阻断原因")
            ]))
            visual += bars("非空 Recall P95 / ms", [
                ("无 Commit 基线", (metric.get("baseline") or {}).get("served_p95_ms"))] + [
                (str(window.get("scenario")), (window.get("confirmed_overlap") or {}).get("served_p95_ms"))
                for window in metric.get("windows", [])
            ])
            visual += bars("Recall 服务率 / %", [
                ("无 Commit 基线", 100 * (metric.get("baseline") or {}).get("recall_service_rate"))
                if (metric.get("baseline") or {}).get("recall_service_rate") is not None else ("无 Commit 基线", None)
            ] + [
                (str(window.get("scenario")),
                 100 * (window.get("confirmed_overlap") or {}).get("recall_service_rate"))
                if (window.get("confirmed_overlap") or {}).get("recall_service_rate") is not None
                else (str(window.get("scenario")), None)
                for window in metric.get("windows", [])
            ], axis_max=100)
            visual += "<p>延迟只统计真正返回非空记忆的 Recall；服务率以全部计划 Search 为分母。全部尝试 P95 受客户端超时上限影响，单独列在下表。宽观察窗口不等于任务始终未完成；最后一次成功 pending/running 轮询之前才有确认重叠证据。未确认终态不等于任务执行失败。</p>"
            overlap_rows = ([{"scenario": "无 Commit 基线", "scope": "独立基线", **metric["baseline"]}]
                            if metric.get("baseline") else [])
            overlap_rows += [{"scenario": w.get("scenario"), "scope": scope, **(w.get(key) or {})}
                            for w in metric.get("windows", []) for scope, key in (
                                ("宽观察窗口", "overlap"), ("非终态确认窗口", "confirmed_overlap"))]
            visual += table(overlap_rows, [("scenario", "场景"), ("scope", "窗口"),
                ("planned_or_recorded", "Search 样本"), ("recall_served", "非空 Recall"),
                ("recall_service_rate", "Recall 服务率"), ("served_p95_ms", "非空 Recall P95 ms"),
                ("p95_ms", "全部尝试 P95 ms"), ("errors", "HTTP/传输错误"),
                ("timeouts", "超时"), ("empty_recall", "HTTP 200空召回"),
                ("quality_ok", "事实质量通过"), ("quality_missing", "质量字段缺失"),
                ("mean_ms", "平均耗时 ms"),
                ("http_status", "HTTP 状态")], min_width_px=1300)
            visual += details("查看基线逐租户召回证据", table(metric.get("baseline_tenants", []), [
                ("tenant_index", "租户"), ("planned_or_recorded", "样本数"),
                ("actual_recall_hits", "真实内容命中"), ("quality_ok", "质量有效"),
                ("errors", "HTTP/传输错误"), ("p95_ms", "P95 ms")]))
            visual += details("查看洪泛准备阶段", table([
                {"scenario": w.get("scenario"), **(w.get("preparation") or {})}
                for w in metric.get("windows", [])], [
                    ("scenario", "场景"), ("open_requests", "Open 请求"),
                    ("add_requests", "Add 请求"), ("aborted_transactions", "准备失败事务"),
                    ("attempted_by_tenant", "Open 尝试分布"),
                    ("stage_http_counts", "阶段 HTTP 分布")]))
            visual += details("查看 Commit 受理、终态与观察范围", table(metric.get("windows", []), [
                ("scenario", "场景"), ("commit_planned", "计划事务"), ("commit_planned_or_recorded", "实际提交"),
                ("commit_accepted_202", "202"), ("commit_rejected", "拒绝"), ("commit_completed", "确认完成"),
                ("commit_failed", "确认执行失败"), ("commit_pending", "未确认终态"),
                ("observed_inflight_peak", "观察在途峰值（非服务排队）"), ("confirmed_intervals", "非终态区间"),
                ("observed_conditions", "服务表现/截止状态")]))
            audit_rows = [{"scenario": w.get("scenario"), **(w.get("commit_evidence") or {})}
                          for w in metric.get("windows", [])]
            visual += details("查看轮询与对账证据", table(audit_rows, [("scenario", "场景"),
                ("observation_outcomes", "轮询结果"), ("poll_http_errors", "轮询 HTTP/传输异常"),
                ("missing_poll_audit", "缺轮询审计"), ("duplicate_receipts", "重复受理记录"),
                ("duplicate_observations", "重复终态观察"), ("orphan_observations", "无对应受理的观察"),
                ("invalid_intervals", "非法时间区间")]))
            visual += details("查看尚缺证据", "<pre>" + esc(json.dumps(metric.get("evidence_issues", []), ensure_ascii=False, indent=2)) + "</pre>")
            tenant_rows = [{"scenario": window.get("scenario"), **tenant,
                            "baseline_p95_ms": tenant.get("baseline", {}).get("p95_ms"),
                            "overlap_p95_ms": tenant.get("overlap", {}).get("p95_ms"),
                            "baseline_served_p95_ms": tenant.get("baseline", {}).get("served_p95_ms"),
                            "overlap_served_p95_ms": tenant.get("overlap", {}).get("served_p95_ms"),
                            "baseline_recall_service_rate": tenant.get("baseline", {}).get("recall_service_rate"),
                            "overlap_recall_service_rate": tenant.get("overlap", {}).get("recall_service_rate"),
                            "served_p95_ratio": tenant.get("p95_ratio")}
                           for window in metric.get("windows", []) for tenant in window.get("tenants", [])]
            visual += details("查看逐租户基线与洪泛对比", table(tenant_rows, [
                ("scenario", "场景"), ("tenant_index", "租户"),
                ("baseline_recall_service_rate", "Baseline Recall率"),
                ("overlap_recall_service_rate", "Overlap Recall率"),
                ("baseline_served_p95_ms", "Baseline 非空P95"),
                ("overlap_served_p95_ms", "Overlap 非空P95"),
                ("served_p95_ratio", "非空P95劣化倍数"),
                ("baseline_p95_ms", "Baseline 全尝试P95"),
                ("overlap_p95_ms", "Overlap 全尝试P95")], min_width_px=1200))
            heterogeneous = metric.get("heterogeneous_tenants") or {}
            heterogeneous_rows = [{**row,
                "search_count": (row.get("search") or {}).get("planned_or_recorded"),
                "search_p95_ms": (row.get("search") or {}).get("p95_ms"),
                "search_errors": (row.get("search") or {}).get("errors"),
                "search_quality_ok": (row.get("search") or {}).get("quality_ok")}
                for row in heterogeneous.get("tenants", [])]
            visual += details("查看不同租户不同强度的 Commit/Search", table(heterogeneous_rows, [
                ("tenant_index", "租户"), ("search_weight", "Search 权重"),
                ("planned_search_rps", "计划 Search RPS"), ("search_arrivals", "Search 到达"),
                ("search_count", "Search 请求"), ("search_p95_ms", "Search P95 ms"),
                ("search_errors", "Search 错误"), ("search_quality_ok", "召回质量通过"),
                ("commit_weight", "Commit 权重"), ("planned_commit_rpm", "计划 Commit RPM"),
                ("commit_arrivals", "Commit 到达"), ("commit_completed", "Commit 完成")
            ], min_width_px=1200))
            headline_visual = (
                "<div class='chart-grid'>"
                "<figure>" + bars("基线与 Commit 洪泛：非空 Recall P95（ms）", [
                    ("无 Commit 基线", (metric.get("baseline") or {}).get("served_p95_ms"))
                ] + [
                    (str(window.get("scenario")), (window.get("confirmed_overlap") or {}).get("served_p95_ms"))
                    for window in metric.get("windows", [])
                ]) + "</figure>"
                "<figure>" + bars("基线与 Commit 洪泛：Recall 服务率（%）", [
                    ("无 Commit 基线", 100 * (metric.get("baseline") or {}).get("recall_service_rate"))
                    if (metric.get("baseline") or {}).get("recall_service_rate") is not None
                    else ("无 Commit 基线", None)
                ] + [
                    (str(window.get("scenario")),
                     100 * (window.get("confirmed_overlap") or {}).get("recall_service_rate"))
                    if (window.get("confirmed_overlap") or {}).get("recall_service_rate") is not None
                    else (str(window.get("scenario")), None)
                    for window in metric.get("windows", [])
                ], axis_max=100) + "</figure></div>"
            )
        elif code == "M5":
            visual = bars("完整恢复样本 / %", [("完成 / 期望",
                100 * metric["complete_samples"] / metric["expected_samples"]
                if metric.get("expected_samples") and metric.get("complete_samples") is not None else None)], axis_max=100)
            visual += details("查看每次 kill-9 恢复检查", table(metric.get("samples", []), [("sample_index", "样本"), ("received_202", "收到202"), ("unfinished_at_kill", "崩溃时未完成"), ("autonomous_completed", "自主完成"), ("fully_reconciled", "完整对账"), ("recovery_elapsed_s", "恢复秒")]))
        elif code == "M6":
            timeline = metric.get("timeline", {})
            visual = bars("四元组完整率与行为覆盖率 / %", [
                ("全过程采样单元完整率", 100 * metric["complete_cells"] / metric["expected_cells"]
                 if metric.get("expected_cells") and metric.get("complete_cells") is not None else None),
                ("NORMAL/QUEUE/REJECT/RESET", 25 * sum(value is True for value in metric.get("scenarios", {}).values())),
            ], axis_max=100)
            visual += ("<p>测试平台采样完整：" + esc(metric.get("capture_complete")) +
                       "；服务四元组合同状态：" + esc(metric.get("service_contract_status")) +
                       "；全过程采样核验：" + esc(timeline.get("status")) +
                       "；完整快照 " + esc(timeline.get("passed_snapshots")) + "/" + esc(timeline.get("snapshot_count")) +
                       "；含窗口边界最大间隔 " + esc(timeline.get("max_gap_s")) + " 秒。</p>"
                       "<p>末次字段齐全不代表中途齐全。RESET需进程身份变化证据；跨重启不计算累计差值。"
                       "已确认重启允许分段核验，无法访问的快照仍计为采样失败；快照间的全部活动不能由采样证明。</p>")
            visual += details("查看四种行为覆盖", table([{"scenario": name, "observed": observed} for name, observed in metric.get("scenarios", {}).items()], [("scenario", "行为"), ("observed", "已观测")]))
            visual += details("查看逐租户逐 Lane 完整性", table(metric.get("matrix", []), [("tenant_id", "租户"), ("lane", "Lane"), ("missing_fields", "缺失"), ("invalid_fields", "非法")]))
            visual += details("查看每次快照与完整分母", table(timeline.get("snapshots", []), [
                ("index", "序号"), ("phase", "阶段"), ("status", "核验状态"),
                ("valid_cells", "有效单元"), ("expected_cells", "预期单元"),
                ("missing_cells", "缺失"), ("invalid_cells", "非法"), ("duplicate_cells", "重复")]))
            visual += details("查看计数回退与进程分段", table(timeline.get("counter_regressions", []), [
                ("tenant", "租户"), ("lane", "层"), ("counter", "计数"),
                ("before_index", "前序号"), ("after_index", "后序号"),
                ("before", "之前"), ("after", "之后"), ("classification", "归类")]))
        metric_body = visual + details(
            "原始汇总与完整分母",
            "<pre>" + esc(json.dumps(metric, ensure_ascii=False, indent=2)) + "</pre>",
        )
        if code in {"M1", "M2", "M3"}:
            metric_body = headline_visual + details("展开测试明细、诊断图与完整证据", metric_body)
        else:
            metric_body = details("展开本项全部结果", metric_body)
        sections.append(
            f"<section><h2>{code} {esc(METRIC_NAMES[code])}</h2>"
            f"<p class='purpose'><b>反映什么：</b>{esc(METRIC_PURPOSES[code])}</p>"
            f"<p class='method'><b>测试方式：</b>{esc(METRIC_METHODS[code])}</p>"
            + ("<p class='method'><b>Search 成功统计：</b>HTTP 200 且返回非空、未被意图拒绝的 Recall；"
               "事实/答案命中仅作诊断，不参与本项的通过、容量或公平性计算。</p>" if code in {"M2", "M3"} else "")
            + f"<div class='metric-body metric-{code.lower()}'>{metric_body}</div></section>"
        )
    recommendations = derive_observation_recommendations(result)
    setup = result.get("setup_evidence") or {}
    source_labels = {
        "validated-cache": "复用已有记忆，本次重新验证召回",
        "fresh": "本次重新注入固定事实记忆",
        "locomo-single-session": "本次注入一段 LoCoMo 真实会话，并使用证据完全位于该 session 的标准问题验证召回",
    }
    memory_evidence = (
        "<p class='method'>Memory endpoint 证据：已观测 "
        f"{esc(setup.get('seed_memory_observed'))} 个租户，可解析 "
        f"{esc(setup.get('seed_memory_rows'))} 个租户，其中非空 "
        f"{esc(setup.get('seed_memory_nonempty'))}、空列表 "
        f"{esc(setup.get('seed_memory_empty'))}；同时种子 Recall HTTP 200 且非空 "
        f"{esc(setup.get('seed_recall_served'))}/{esc(setup.get('seed_recall_queries'))}；"
        f"种子诊断非空候选 {esc(setup.get('seed_recall_served_diagnostic'))}，"
        f"其中 marker/事实命中 {esc(setup.get('seed_marker_visible'))}。"
        "Memory endpoint 空列表表示该 Commit 没有报告抽取条目；Search 非空只表示服务返回了候选，"
        "两者同时出现时应标记为 extraction gap 候选，不能把它当作 marker 已命中。</p>"
        if setup.get("seed_memory_rows") else
        "<p class='method'>本轮没有可解析的 Memory endpoint 证据；不能从 HTTP 200 或 Commit completed 推断记忆已持久化，"
        "需先补采该端点或服务端抽取日志。</p>"
    )
    seed_source = ("<section><h2>记忆与问题来源</h2><p>" + esc(source_labels.get(setup.get("seed_source"), "来源未记录，不能推定为本次重新注入")) + "</p>" + table([setup], [
        ("seed_documents_per_tenant", "每租户注入文本数"), ("facts_per_tenant", "每租户事实数"),
        ("query_variants_per_tenant", "每租户问题池大小"),
        ("validated_queries_per_tenant", "每租户本次预检问题数"),
        ("seed_healthy_actor_count", "实际进入负载租户"),
        ("seed_failed_actor_count", "失败或 pending 种子租户"),
        ("seed_memory_observed", "Memory endpoint 已观测租户"),
        ("seed_memory_rows", "Memory endpoint 可解析租户"),
        ("seed_memory_nonempty", "Memory endpoint 非空租户"),
        ("seed_memory_empty", "Memory endpoint 空列表租户"),
        ("seed_memory_total", "可解析记忆条目总数"),
        ("seed_recall_served_diagnostic", "种子诊断非空候选"),
        ("seed_marker_visible", "种子 marker/事实命中"),
        ("seed_recall_queries", "种子 Recall 预检数"),
        ("seed_recall_served", "种子非空 Recall"),
        ("seed_recall_empty", "种子 HTTP 200 空召回"),
        ("seed_recall_http_errors", "种子 HTTP 错误")
    ]) + "<p>预检抽样通过不代表整个问题池全部通过；正式发压中的空召回、错误和降级仍计入失败分母。</p>"
    + memory_evidence + "</section>")
    seed_diagnosis = ""
    if result.get("seed_query_diagnosis"):
        labels = {"bare-marker": "只查询编号", "personal-recall": "请回忆编号事项", "personal-question": "询问编号具体事项"}
        diagnosis_rows = [{**row, "query_form": labels.get(row.get("query_form"), row.get("query_form"))}
                          for row in result["seed_query_diagnosis"]]
        seed_diagnosis = "<section><h2>种子召回对比诊断</h2><p>这是低并发诊断请求，不是压测 P95 或容量。未进入负载阶段时，下方测试方式仅代表计划。</p>" + table(diagnosis_rows, [("query_form", "查询形式"), ("http_status", "HTTP"), ("elapsed_s", "耗时秒"), ("hit_count", "返回条数"), ("marker_found", "编号命中"), ("quality_ok", "原编号断言通过"), ("degraded", "降级")]) + "</section>"
    recommendation_table = table(recommendations, [
        ("priority", "优先级"), ("module", "EchoMem 模块"),
        ("metrics", "关联指标"), ("evidence", "本次证据"),
        ("action", "改进建议"),
    ], min_width_px=900)
    findings_section = (
        "<section><h2>本轮新增问题（已排除 PR534 已知项）</h2>"
        "<p class='purpose'>只列本轮运行证据直接支持的新现象；PR534 已覆盖的 Atomic 锁、Commit gate、"
        "per-tenant 配额和 4U8G squeeze 不在此表重复列为新问题。</p>"
        + table(result.get("new_findings", []), [
            ("id", "编号"), ("priority", "优先级"), ("module", "EchoMem 模块"),
            ("metrics", "指标"), ("phenomenon", "现象"), ("evidence", "数据证据"),
            ("impact", "影响"), ("action", "修改建议"), ("relation", "与 PR534 的关系"),
        ], min_width_px=1900) + "</section>"
    )
    api_coverage = result.get("api_coverage") or {}
    api_section = (
        "<section><h2>关键接口调用账本与边界输入</h2>"
        f"<p>{esc(api_coverage.get('scope'))}。已覆盖 "
        f"{esc(api_coverage.get('covered'))}/{esc(api_coverage.get('expected'))} 项；"
        "调用次数为原始记录或探针次数，未调用明确显示 NOT_COVERED。</p>" +
        table(api_coverage.get("operations", []), [
            ("operation", "接口能力"), ("method", "方法"), ("path", "路径"),
            ("calls", "HTTP调用/轮询次数"), ("calls_exact", "精确计数"),
            ("records", "汇总记录"), ("ok", "成功"), ("errors", "错误"),
            ("status", "覆盖状态"), ("evidence", "证据"),
        ]) +
        "<h3>服务端日志实际调用接口</h3><p>只统计采集窗口内完成的 HTTP 请求，可能包含准备阶段和健康探针；"
        "不等于客户端全部尝试数，也不等于业务成功。没有日志的接口不推定已覆盖。</p>" +
        table(api_coverage.get("server_observed_endpoints", []), [
            ("endpoint", "方法与路由"), ("observed_completions", "观察到的完成次数"),
            ("status_counts", "HTTP 状态码分布"),
        ]) +
        "<h3>不合法输入与边界处理</h3>" +
        table([api_coverage.get("invalid_input") or {}], [
            ("status", "状态"), ("reason", "说明"),
            ("passed_cases", "通过"), ("expected_cases", "总数")]) +
        details("查看每个非法输入用例", table(
            (api_coverage.get("invalid_input") or {}).get("cases", []), [
                ("case", "用例"), ("method", "方法"), ("path", "路径"),
                ("expected_status", "期望状态"), ("http_status", "实际状态"),
                ("contract_ok", "合同符合"),
                ("accepted_invalid_input", "错误输入被接受")
            ], min_width_px=1000)) + "</section>"
    )
    timing = result.get("timing_evidence") or {}
    correlation = timing.get("trace_correlation") or {}
    timing_totals = timing.get("timing_totals") or {}
    timing_rows = timing.get("scenario_timings") or []
    timing_chart = bars(
        "各场景实际耗时（秒，包含本场景排空）",
        [(str(row.get("scenario") or "unknown"), row.get("actual_elapsed_s"))
         for row in timing_rows],
    )
    timing_section = (
        "<section><h2>接口与模块耗时</h2><p>" + esc(timing.get("note")) + "</p>" +
        "<h3>本轮场景耗时总览</h3>" +
        table([timing_totals], [
            ("scenario_count", "场景数"), ("planned_load_s", "计划负载秒"),
            ("actual_elapsed_s", "实际耗时秒"), ("drain_s", "排空耗时秒"),
            ("note", "口径")]) +
        timing_chart +
        details("查看每个 M1/M2/M3 场景耗时与请求量", table(timing_rows, [
            ("scenario", "场景"), ("status", "状态"),
            ("planned_load_s", "计划负载秒"), ("actual_elapsed_s", "实际耗时秒"),
            ("engine_elapsed_s", "引擎耗时秒"), ("drain_s", "排空秒"),
            ("requests", "请求/记录数"), ("search_requests", "Search记录"),
            ("commit_requests", "Commit相关记录"), ("source", "证据来源")], min_width_px=1300)) +
        table([correlation], [
            ("status", "Trace 关联状态"), ("eligible_requests", "可关联请求"),
            ("requests_with_trace", "带 Trace"),
            ("requests_linked_to_internal_stage", "关联内部阶段"),
            ("requests_missing_trace", "缺 Trace"),
            ("traced_without_stage_log", "有 Trace 无阶段日志"),
            ("missing_m1_evidence_files", "M1 缺失证据文件")]) +
        table(timing.get("operation_timings", []), [
            ("module", "接口/阶段"), ("source", "来源"), ("observations", "样本"),
            ("p50_ms", "P50 ms"), ("p95_ms", "P95 ms"), ("p99_ms", "P99 ms")]) +
        details("结构化日志：逐阶段真实耗时", table(timing.get("structured_log_timings", []), [
            ("module", "模块路径"), ("observations", "耗时样本"),
            ("trace_count", "Trace 数"), ("p50_ms", "P50 ms"),
            ("p95_ms", "P95 ms"), ("p99_ms", "P99 ms"),
            ("queue_wait_observations", "排队样本"),
            ("queue_wait_p50_ms", "排队 P50 ms"),
            ("queue_wait_p95_ms", "排队 P95 ms"),
            ("queue_wait_p99_ms", "排队 P99 ms")], min_width_px=1250)) +
        details("Prometheus：运行窗口 Histogram 增量", table(timing.get("prometheus_timings", []), [
            ("module", "指标与标签"), ("observations", "窗口样本"),
            ("mean_ms", "均值 ms"), ("p50_ms", "P50 ms"),
            ("p95_ms", "P95 ms"), ("p99_ms", "P99 ms"),
            ("quantile_missing_reason", "分位数缺失原因"),
            ("counter_resets", "计数器重置次数"),
            ("count_is_lower_bound", "样本数仅为下界")], min_width_px=1250) +
            "<p>分位数缺失不代表 0 ms：没有有限桶，或分位数落在最大有限桶之外时不报告精确值。"
            "计数器重置后按相邻采样累计可见增量，但重置前未采到的请求无法恢复，样本数仅为下界。"
            "Histogram 分位数是桶内估算值，不等同于逐请求日志的精确分位数。</p>") +
        details("模型与 Embedding 调用/缓存计数", table(timing.get("cache_diagnostics", []), [
            ("counter", "计数器"), ("observations", "日志样本"),
            ("total", "总计"), ("p50", "P50"), ("p95", "P95"),
            ("source", "来源"), ("note", "解释")], min_width_px=1100)) +
        "<p>Commit 的原子引擎日志会直接提供 LLM/Embedding provider service time、admission wait、cache hit/miss；Search 的阶段时间来自对应结构化日志和 Prometheus。若服务没有暴露 LLM 厂商内部 prompt-cache 命中字段，本报告只写‘未观测’，不把重复 query 的变快解释为缓存命中。</p>" +
        details("日志与 Prometheus 交叉校验", table(timing.get("cross_check", []), [
            ("check", "阶段"), ("log_observations", "日志样本"),
            ("prometheus_metric", "Prometheus 指标"),
            ("prometheus_observations", "指标样本"), ("status", "两路状态"),
            ("note", "说明")], min_width_px=1150)) +
        details("服务响应附带的阶段计时", table([
            row for row in timing.get("module_timings", []) if row.get("source") == "服务响应"
        ], [
            ("module", "模块路径"), ("source", "来源"), ("observations", "样本"),
            ("p50_s", "P50 秒"), ("p95_s", "P95 秒"),
            ("p99_s", "P99 秒")], min_width_px=900)) +
        ("<p><b>本轮没有真实样本的阶段：</b>" + esc("、".join(timing.get("unobservable_modules", []))) + "。</p>"
         if timing.get("unobservable_modules") else "<p><b>阶段可观测性：</b>本轮要求的阶段均采到真实日志样本。</p>") +
        details("查看缺失阶段及原因", table(timing.get("missing_stage_reasons", []), [
            ("module", "阶段"), ("reason", "缺失原因")])) + "</section>"
    )
    concurrency = result.get("concurrency_configuration") or {}
    concurrency_section = (
        f"<section><h2>客户端目标并发与 EchoMem 配置隔离（{esc(concurrency.get('required_client_concurrency'))}）</h2>"
        f"<p>{esc(concurrency.get('note'))}</p>"
        f"<p>客户端目标并发：<b>{esc(concurrency.get('required_client_concurrency'))}</b>；"
        f"测试平台按服务配置自动降载：<b>{esc(concurrency.get('client_load_auto_capped_by_service_config'))}</b>；"
        f"低于目标的服务端上限：<b>{esc(concurrency.get('limits_below_target'))}</b> 项。</p>" +
        table(concurrency.get("observed_service_limits", []), [
            ("config_path", "EchoMem 配置路径"), ("value", "当前值"),
            ("comparison_applies", "参与并发比较"),
            ("disabled_or_unbounded", "0=禁用/不设上限"),
            ("below_requested_concurrency", "低于客户端目标")
        ]) + "</section>"
    )
    supplemental = result.get("supplemental_probes") or {}
    topology = supplemental.get("concurrency_topology") or {}
    topology_rows = topology.get("matrix") if isinstance(topology.get("matrix"), list) else []
    boundary = topology.get("first_boundary") or {}
    payload = supplemental.get("payload_boundary") or {}
    payload_rows = payload.get("cases") if isinstance(payload.get("cases"), list) else []
    supplemental_section = ""
    if topology or payload:
        artifact_links = " · ".join(
            f"<a href='{esc(probe.get('artifact'))}'>{esc(probe.get('artifact'))}</a>"
            for probe in (topology, payload) if probe.get("artifact")
        )
        topology_visual = bars("并发拓扑 P95 / ms", [
            (f"C{row.get('requested_concurrency')} {row.get('topology')}", row.get("p95_ms"))
            for row in topology_rows
        ]) if topology_rows else ""
        topology_table = table(topology_rows, [
            ("requested_concurrency", "并发档"), ("topology", "拓扑"),
            ("actual_users", "实际用户"), ("requested_users", "计划用户"),
            ("actual_sessions", "实际Session"),
            ("generator_workers", "客户端工作线程"),
            ("observed_inflight_peak", "实测峰值在途"),
            ("offered", "总请求"), ("completed_2xx", "2xx"),
            ("p95_ms", "P95 ms"), ("throughput_rps_2xx", "2xx吞吐/s"),
            ("search_offered", "Search分母"),
            ("search_quality_observed", "质量断言分母"),
            ("search_recall_hits", "事实命中"),
            ("search_quality_ok", "召回质量通过"),
            ("search_quality_failures", "召回质量失败"),
            ("search_degraded", "降级"),
            ("commit_offered", "Commit"), ("commit_accepted", "Commit受理"),
            ("commit_completed", "Commit完成"), ("commit_failed", "Commit失败"),
            ("commit_timed_out", "Commit超时"), ("commit_missing_receipt", "Commit缺Receipt"),
            ("operational_failures", "运行边界事件"),
            ("search_strict_throughput_jain", "严格Search吞吐Jain"),
            ("commit_completion_throughput_jain", "Commit完成吞吐Jain"),
            ("http_counts", "HTTP/传输分布"), ("boundary_reasons", "边界原因"),
        ], min_width_px=1800) if topology_rows else "<p>未执行并发拓扑探针。</p>"
        payload_table = table(payload_rows, [
            ("outcome", "实际边界处理结果"),
            ("api", "API"), ("encoding", "编码"),
            ("content_bytes", "内容字节"), ("wire_bytes", "Wire字节"),
            ("http_status", "HTTP"), ("reason_code", "原因码"),
            ("transport_error_type", "传输错误"), ("elapsed_ms", "耗时ms"),
            ("accepted", "受理"),
        ], min_width_px=1100) if payload_rows else "<p>未执行请求体边界探针。</p>"
        supplemental_section = (
            "<section><h2>并发拓扑与请求边界补充证据</h2>"
            "<p class='purpose'>这是 M1-M3 的横向诊断数据，不替代六项指标。并发档位只有在实际用户、"
            "实际在途和请求分母均实现时才算完整；HTTP 2xx 与真实召回质量分开统计。"
            "Commit 同时展示受理和最终完成，不能把 202 当作完成。</p>"
            f"<p><b>拓扑证据：</b>{esc(topology.get('status') or 'NOT_SELECTED')}；"
            f"已测档位：{esc(topology.get('measured_levels'))}；"
            f"边界状态：{esc(topology.get('boundary_status'))}；"
            f"首个边界：C{esc(boundary.get('level'))} {esc(boundary.get('topology'))} "
            f"{esc(boundary.get('reasons'))}。</p>"
            + topology_visual + details("查看并发拓扑精确分母", topology_table)
            + f"<p><b>请求边界证据：</b>{esc(payload.get('status') or 'NOT_SELECTED')}。"
              "文本 JSON 与非法二进制分别展示；超长 Commit 的终态和 MCP add_memory 单列，"
              "不从某个 HTTP 200 推定全部接口支持该尺寸。</p>"
            + details("查看每个请求体用例", payload_table)
            + details("查看超长 Commit 与 MCP", "<pre>" + esc(json.dumps({
                "long_commit": payload.get("long_commit"),
                "mcp_add_memory": payload.get("mcp_add_memory"),
            }, ensure_ascii=False, indent=2)) + "</pre>")
            + f"<p>原始证据：{artifact_links or '未记录路径'}</p></section>"
        )
    combined = result.get("combined_evidence") or {}
    combined_section = ""
    if combined:
        source_rows = [
            {**row, "actual_elapsed_min": round(float(row.get("actual_elapsed_s") or 0) / 60, 2),
             "drain_min": round(float(row.get("drain_s") or 0) / 60, 2)}
            for row in combined.get("sources", [])
        ]
        combined_section = (
            "<section><h2>跨运行总览与实际耗时</h2>"
            f"<p class='purpose'>{esc(combined.get('note'))}</p>"
            + bars("各指标实际耗时（分钟）", [
                (str(row.get("label")), float(row.get("actual_elapsed_s") or 0) / 60)
                for row in combined.get("sources", [])
            ])
            + table([combined], [
                ("status", "汇总状态"), ("planned_load_s", "计划负载秒"),
                ("active_elapsed_s", "实际场景耗时秒"), ("drain_s", "排空耗时秒"),
                ("generated_at", "报告生成时间"), ("note", "口径")
            ])
            + details("查看各指标来源与耗时", table(source_rows, [
                ("label", "来源"), ("path", "原始目录"),
                ("scenario_count", "场景数"), ("planned_load_s", "计划负载秒"),
                ("actual_elapsed_s", "实际耗时秒"), ("actual_elapsed_min", "实际耗时分钟"),
                ("drain_s", "排空秒"), ("drain_min", "排空分钟")
            ], min_width_px=1200))
            + "</section>"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    stage_notice = ""
    if result.get("pending_metrics"):
        label = "阶段性结果，尚未完成" if result.get("checkpoint") else "运行中断，以下指标未完成"
        stage_notice = f"<p class='PARTIAL'><b>{label}：{esc(', '.join(result['pending_metrics']))}</b>。本页不是完整六项结论。</p>"
    setup_section = (
        "<section><h2>准备阶段证据</h2><p>没有完成负载场景时不能给出性能结论。"
        "裸编号未命中不等于语义事实没有写入；需分别验证实际返回的记忆内容、路由和降级。</p>"
        + table([result.get("setup_evidence") or {}], [
            ("seed_status", "种子状态"), ("seed_contract", "校验方式"),
            ("healthy_actors", "验证通过租户"), ("expected_actors", "验证租户总数"),
            ("validated_queries_per_tenant", "每租户预检问题数"),
            ("bare_marker_gate_failed", "裸编号前置校验失败"),
            ("load_cases_completed", "已有负载场景"),
        ]) + "</section>"
    )
    recommendation_section = (
        "<section><h2>EchoMem 模块改进建议</h2>"
        "<p class='purpose'>建议只由本轮可见证据推导；无法从黑盒区分的阶段明确写为需补观测，"
        "不把端到端延迟武断归因给原子引擎。</p>" + recommendation_table
        + details("查看责任边界与技术证据", table(result.get("issue_categories", []), [
            ("category", "类别"), ("note", "观测/下一步"), ("evidence", "证据"),
        ])) + "</section>"
    )
    artifact_section = (
        "<section><h2>原始产物</h2><p><a href='summary.json'>summary.json</a> · "
        "<a href='combined-sources.json'>combined-sources.json</a></p></section>"
        if combined else
        "<section><h2>原始产物</h2><p><a href='summary.json'>summary.json</a> · "
        "<a href='suite.json'>suite.json</a> · <a href='records.csv'>records.csv</a> · "
        "<a href='metrics_samples.csv'>metrics_samples.csv</a> · "
        "<a href='structured-stage-events.jsonl'>structured-stage-events.jsonl</a> · "
        "<a href='execution-manifest.json'>execution-manifest.json</a></p></section>"
    )
    supporting_content = (
        model_section + setup_section + findings_section + recommendation_section + combined_section + api_section
        + timing_section + concurrency_section + supplemental_section + seed_source + seed_diagnosis
        + render_platform_provenance(result.get("platform_provenance")) + artifact_section
    )
    path.write_text("""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>""" + esc(title) + """</title><style>
body{margin:0;color:#18242b;background:#f4f7f8;font:14px/1.6 system-ui;letter-spacing:0}main{max-width:1320px;margin:auto;padding:24px}h1{font-size:28px}h2{font-size:20px}.lead{border-left:4px solid #17746a;padding:10px 14px;background:#fff}.live-progress{border-left:4px solid #946200;background:#fff9e7}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:18px 0}.cards article{background:#fff;border:1px solid #d5dfe3;padding:14px;border-radius:4px}.cards h2{font-size:16px;margin:6px 0}.cards small{display:block;color:#60727a}.MEASURED{color:#08745d}.PARTIAL{color:#946200}.BLOCKED,.EXECUTION_ERROR{color:#b1372e}section{background:#fff;border-top:1px solid #cbd6da;padding:18px;margin-top:12px}.metric-body{border-top:3px solid #17746a;padding-top:2px}.purpose,.method{color:#40565f;font-size:15px}.method{background:#f0f5f6;border-left:3px solid #4d8791;padding:8px 12px}.chart-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin:16px 0}.chart-grid figure,.primary-charts figure{margin:0;padding:14px;border:1px solid #d7e1e4;border-radius:4px;background:#fbfcfc}.chart-grid h3,.primary-charts h3{margin:0 0 12px;font-size:16px}.primary-charts{display:grid;gap:16px;margin:16px 0}.tree-chart{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.tree-group{padding:0 0 10px;background:#f3f7f7;border-left:3px solid #7aa7a2}.tree-root{display:flex;justify-content:space-between;align-items:baseline;padding:10px;background:#e7f0ef;border-bottom:1px solid #c8dcda}.tree-root span{font-size:12px;color:#60727a}.tree-children{padding:4px 10px 0 18px;position:relative}.tree-children:before{content:'';position:absolute;left:8px;top:0;bottom:13px;border-left:1px solid #9bb8b4}.tree-node{position:relative;display:grid;grid-template-columns:1fr 1.4fr;gap:4px 8px;padding:7px 0 3px}.tree-node:before{content:'';position:absolute;left:-10px;top:17px;width:8px;border-top:1px solid #9bb8b4}.tree-label{font-size:12px;color:#40565f;grid-column:1/-1}.tree-node i{display:block;height:8px;background:#dde6e7}.tree-node i b{display:block;height:100%;background:#17746a}.tree-node strong{font-size:12px;grid-column:2;text-align:right}.outcome-legend{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:12px}.outcome-legend span:before{content:'';display:inline-block;width:10px;height:10px;margin-right:6px}.outcome-legend .served:before,.outcome-track .served{background:#17746a}.outcome-legend .empty:before,.outcome-track .empty{background:#d39a38}.outcome-legend .failed:before,.outcome-track .failed{background:#b84a3b}.outcome-row{display:grid;grid-template-columns:55px minmax(160px,1fr) 360px;gap:12px;align-items:center;margin:10px 0}.outcome-track{display:flex;height:16px;background:#e2e8ea;overflow:hidden}.outcome-track i{display:block;height:100%}.chart-note{color:#60727a;font-size:12px;margin:12px 0 0}.scroll{overflow:auto}table{width:100%;border-collapse:collapse}th,td{text-align:left;vertical-align:top;padding:8px;border-bottom:1px solid #dde4e7}th{background:#edf2f4;white-space:nowrap}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f4f5;padding:12px}details{border-top:1px solid #e0e6e8;margin-top:12px;padding-top:8px}summary{cursor:pointer;color:#176d75;font-weight:650}.supporting>summary{font-size:18px;background:#fff;padding:14px;border:1px solid #cbd6da}.bar{display:grid;grid-template-columns:minmax(150px,220px) minmax(100px,1fr) 78px;gap:10px;align-items:center;margin:7px 0}.bar i{display:block;height:12px;background:#e0e7e9}.bar i b{display:block;height:100%;background:#17746a}.bar i b.worse{background:#c05a45}.bar i b.better{background:#278575}.bar strong{text-align:right}@media(max-width:900px){.chart-grid{grid-template-columns:1fr}.tree-chart{grid-template-columns:repeat(2,minmax(0,1fr))}.outcome-row{grid-template-columns:50px 1fr}.outcome-row span{grid-column:1/-1}}@media(max-width:760px){.cards{grid-template-columns:1fr}main{padding:12px}.bar{grid-template-columns:1fr}.bar strong{text-align:left}.tree-chart{grid-template-columns:1fr}}</style></head><body><main>""" +
        f"<h1>{esc(title)}</h1><div class='lead'><b>本次所选指标结论：{'进行中' if result.get('run_state') == 'RUNNING' else esc(result['status'])}</b><p>这是观测报告，不是性能准入验收；没有 P95、准确率、Jain、吞吐或劣化比例 PASS/FAIL 门槛。错误、超时、空召回与 pending/failed Commit 均保留在分母。采样模式：{esc(result['sampling_mode'])}。</p>{scope_notice}</div>{live_seed_progress}<div class='cards'>{cards}</div>" +
        stage_notice + "".join(sections)
        + "<details class='supporting'><summary>展开模型、准备、模块耗时、接口与原始证据</summary>"
        + supporting_content + "</details>" +
        "</main></body></html>", encoding="utf-8")
