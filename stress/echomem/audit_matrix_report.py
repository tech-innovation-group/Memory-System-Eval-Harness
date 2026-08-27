#!/usr/bin/env python3
"""Render an auditable, number-first HTML report for a stress matrix."""

from __future__ import annotations

import argparse
import csv
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OK_STATES = {"completed", "complete", "transcommit", "succeeded", "success"}


def esc(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return html.escape(str(value))


def sec(value: Any) -> str:
    try:
        return f"{float(value):.3f}s"
    except (TypeError, ValueError):
        return "-"


def num(value: Any, digits: int = 3) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "-"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def simple_percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percent / 100
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def csv_timeline(
    commit_rows: list[dict[str, str]],
    search_rows: list[dict[str, str]],
    commit_threshold: float,
    search_threshold: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build the same timeline projection for older summaries.

    Early runner versions already persisted the timestamps in CSV but did not
    copy them into summary.json.  Keeping this compatibility path means an
    audit report never silently loses evidence just because it was generated
    before the summary projection was added.
    """
    raw: list[dict[str, Any]] = []
    for row in commit_rows:
        try:
            duration = float(row.get("end_to_end_s") or row.get("elapsed_s") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        raw.append(
            {
                "operation": "commit",
                "tenant": row.get("tenant", ""),
                "session_id": row.get("session_id", ""),
                "queued_at": row.get("queued_at", ""),
                "started_at": row.get("started_at", ""),
                "completed_at": row.get("completed_at", ""),
                "duration_s": duration,
                "queue_wait_s": float(row.get("queue_wait_s") or 0),
                "admission_wait_s": float(row.get("admission_wait_s") or 0),
                "queue_depth": int(float(row.get("admission_queue_depth") or row.get("queue_depth_at_enqueue") or 0)),
                "admission_order": int(float(row.get("admission_order") or 0)),
                "status": row.get("status", ""),
                "status_code": row.get("status_code") or "",
                "request_id": row.get("request_id", ""),
                "delayed": duration >= commit_threshold,
            }
        )
    for row in search_rows:
        try:
            duration = float(row.get("end_to_end_s") or row.get("service_s") or row.get("elapsed_s") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        try:
            status_code = int(float(row.get("status_code") or 0))
        except (TypeError, ValueError):
            status_code = None
        raw.append(
            {
                "operation": "search",
                "tenant": row.get("tenant", ""),
                "session_id": row.get("session_id", ""),
                "queued_at": row.get("queued_at") or row.get("started_at", ""),
                "started_at": row.get("started_at", ""),
                "completed_at": row.get("finished_at", ""),
                "duration_s": duration,
                "queue_wait_s": float(row.get("queue_wait_s") or 0),
                "admission_wait_s": float(row.get("admission_wait_s") or 0),
                "queue_depth": int(float(row.get("admission_queue_depth") or row.get("queue_depth_at_enqueue") or 0)),
                "admission_order": int(float(row.get("admission_order") or 0)),
                "status": row.get("status", ""),
                "status_code": status_code,
                "request_id": row.get("request_id", ""),
                "delayed": duration >= search_threshold,
            }
        )
    anchor_candidates = [
        parsed for item in raw
        if (parsed := parse_timestamp(item.get("queued_at"))) is not None
    ]
    anchor = min(anchor_candidates) if anchor_candidates else None
    for item in raw:
        queued = parse_timestamp(item.get("queued_at"))
        item["workload_offset_s"] = (
            (queued - anchor).total_seconds() if queued and anchor else None
        )
    raw.sort(
        key=lambda item: (
            item.get("workload_offset_s") is None,
            item.get("workload_offset_s") or 0,
            item.get("admission_order") or 0,
        )
    )
    buckets: list[dict[str, Any]] = []
    bucket_count = max(
        1,
        int(max([item.get("workload_offset_s") or 0 for item in raw] or [0]) // 60) + 1,
    )
    for index in range(bucket_count):
        items = [
            item for item in raw
            if item.get("workload_offset_s") is not None
            and index * 60 <= item["workload_offset_s"] < (index + 1) * 60
        ]
        commits = [item for item in items if item["operation"] == "commit"]
        searches = [item for item in items if item["operation"] == "search"]
        commit_success = [item for item in commits if item.get("status") in OK_STATES]
        search_success = [
            item for item in searches
            if item.get("status_code") is not None
            and 200 <= item["status_code"] < 300
        ]
        buckets.append(
            {
                "bucket": index,
                "start_s": index * 60,
                "end_s": (index + 1) * 60,
                "tenants": sorted({item["tenant"] for item in items}),
                "commit": {
                    "submitted": len(commits),
                    "completed": len(commit_success),
                    "delayed": sum(item["delayed"] for item in commits),
                    "latency": {
                        "mean_s": (
                            sum(item["duration_s"] for item in commit_success) / len(commit_success)
                            if commit_success else None
                        ),
                        "p95_s": simple_percentile(
                            [item["duration_s"] for item in commit_success], 95
                        ),
                    },
                },
                "search": {
                    "submitted": len(searches),
                    "succeeded": len(search_success),
                    "delayed": sum(item["delayed"] for item in searches),
                    "latency": {
                        "mean_s": (
                            sum(item["duration_s"] for item in search_success) / len(search_success)
                            if search_success else None
                        ),
                        "p95_s": simple_percentile(
                            [item["duration_s"] for item in search_success], 95
                        ),
                    },
                },
            }
        )
    return raw, buckets


def parse_prometheus_text(raw: str) -> dict[str, float]:
    """Parse the latest Prometheus sample without retaining tenant secrets."""
    values: dict[str, float] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        name, value = parts
        name = name.split("{", 1)[0]
        try:
            values[name] = values.get(name, 0.0) + float(value)
        except ValueError:
            continue
    return values


def server_snapshot(directory: Path) -> tuple[dict[str, float], int]:
    """Read the latest server /metrics sample and its sample count."""
    path = directory / "server_metrics.jsonl"
    if not path.is_file():
        return {}, 0
    latest: dict[str, float] = {}
    sample_count = 0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            latest = parse_prometheus_text(str(payload.get("raw") or ""))
            sample_count += 1
    except (OSError, json.JSONDecodeError):
        return latest, sample_count
    return latest, sample_count


def load_matrix(path: Path) -> list[tuple[str, dict[str, Any], Path]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        (
            str((summary.get("parameters") or {}).get("scheduler_policy") or "unknown"),
            summary,
            path.parent / str((summary.get("parameters") or {}).get("scheduler_policy") or "unknown"),
        )
        for summary in payload.get("summaries") or []
    ]


def metric_group(group: dict[str, Any], title: str) -> str:
    fields = (
        ("样本数", group.get("count")),
        ("平均", sec(group.get("mean_s"))),
        ("最小", sec(group.get("min_s"))),
        ("P50", sec(group.get("p50_s"))),
        ("P90", sec(group.get("p90_s"))),
        ("P95", sec(group.get("p95_s"))),
        ("P99", sec(group.get("p99_s"))),
        ("最大", sec(group.get("max_s"))),
        ("总耗时", sec(group.get("total_s"))),
    )
    return (
        f"<div class='metric-block'><h4>{esc(title)}</h4><div class='metric-grid'>"
        + "".join(
            f"<div><span>{esc(label)}</span><b>{esc(value)}</b></div>"
            for label, value in fields
        )
        + "</div></div>"
    )


def policy_section(
    policy: str,
    summary: dict[str, Any],
    directory: Path,
    include_header: bool = True,
) -> str:
    metrics = summary.get("metrics") or {}
    commit = metrics.get("commit") or {}
    search = metrics.get("search") or {}
    commit_server = commit.get("server") or {}
    search_server = search.get("server") or {}
    admission = metrics.get("admission") or {}
    details = summary.get("details") or {}
    params = summary.get("parameters") or {}
    targets = metrics.get("targets") or {}
    isolation = details.get("isolation") or {}
    commit_delayed = commit.get("delayed") or []
    search_delayed = search.get("delayed") or []
    time_buckets = metrics.get("time_buckets") or []
    workload_timeline = metrics.get("timeline") or []
    commit_rows = read_csv(directory / "commit_results.csv")
    search_rows = read_csv(directory / "search_results.csv")
    if not workload_timeline:
        workload_timeline, csv_buckets = csv_timeline(
            commit_rows,
            search_rows,
            float(commit.get("delayed_threshold_s") or 10.0),
            float(search.get("delayed_threshold_s") or 2.5),
        )
        if not time_buckets:
            time_buckets = csv_buckets
    if not commit_delayed:
        commit_delayed = [
            {
                "tenant": item.get("tenant"),
                "session_id": item.get("session_id"),
                "started_at": item.get("started_at"),
                "completed_at": item.get("completed_at"),
                "completion_s": item.get("duration_s"),
                "queue_wait_s": item.get("queue_wait_s"),
                "admission_wait_s": item.get("admission_wait_s"),
                "queue_depth": item.get("queue_depth"),
                "admission_order": item.get("admission_order"),
                "request_id": item.get("request_id"),
                "status": item.get("status"),
            }
            for item in workload_timeline
            if item.get("operation") == "commit" and item.get("delayed")
        ]
    if not search_delayed:
        search_delayed = [
            {
                "tenant": item.get("tenant"),
                "session_id": item.get("session_id"),
                "started_at": item.get("started_at"),
                "finished_at": item.get("completed_at"),
                "latency_s": item.get("duration_s"),
                "admission_wait_s": item.get("admission_wait_s"),
                "queue_depth": item.get("queue_depth"),
                "admission_order": item.get("admission_order"),
                "request_id": item.get("request_id"),
                "status_code": item.get("status_code"),
            }
            for item in workload_timeline
            if item.get("operation") == "search" and item.get("delayed")
        ]
    commit_slowest = sorted(
        commit_rows,
        key=lambda row: float(row.get("end_to_end_s") or row.get("elapsed_s") or 0),
        reverse=True,
    )[:20]
    search_slowest = sorted(
        search_rows,
        key=lambda row: float(row.get("service_s") or row.get("elapsed_s") or 0),
        reverse=True,
    )[:20]
    server_metrics, server_sample_count = server_snapshot(directory)
    tenant_rows = []
    for tenant, data in sorted((metrics.get("per_tenant") or {}).items()):
        c = data.get("commit") or {}
        s = data.get("search") or {}
        cc = c.get("completion") or {}
        cq = c.get("queue_wait") or {}
        sl = s.get("latency") or {}
        tenant_rows.append(
            "<tr>"
            f"<td><b>{esc(tenant)}</b></td>"
            f"<td>{esc(c.get('submitted'))} / {esc(c.get('completed'))} / {esc(c.get('failed'))}</td>"
            f"<td>{sec(cc.get('mean_s'))}</td><td>{sec(cc.get('p50_s'))}</td>"
            f"<td>{sec(cc.get('p95_s'))}</td><td>{sec(cc.get('p99_s'))}</td><td>{sec(cc.get('max_s'))}</td>"
            f"<td>{sec(cq.get('mean_s'))}</td><td>{sec(cq.get('max_s'))}</td>"
            f"<td>{esc(s.get('submitted'))} / {esc(s.get('succeeded'))} / {esc(s.get('errors'))}</td>"
            f"<td>{sec(sl.get('mean_s'))}</td><td>{sec(sl.get('p95_s'))}</td>"
            f"<td>{sec(sl.get('p99_s'))}</td>"
            "</tr>"
        )

    delayed_rows = []
    for row in commit_delayed:
        delayed_rows.append(
            "<tr class='commit-row'>"
            f"<td>Commit</td><td>{esc(row.get('tenant'))}</td><td>{esc(row.get('session_id'))}</td>"
            f"<td>{esc(row.get('started_at'))}</td><td>{esc(row.get('completed_at'))}</td>"
            f"<td>{sec(row.get('completion_s'))}</td><td>{sec(row.get('queue_wait_s'))}</td>"
            f"<td>{sec(row.get('admission_wait_s'))}</td><td>{esc(row.get('queue_depth'))}</td>"
            f"<td>{esc(row.get('admission_order'))}</td><td>{esc(row.get('request_id'))}</td>"
            f"<td>{esc(row.get('status'))}</td>"
            "</tr>"
        )
    for row in search_delayed:
        delayed_rows.append(
            "<tr class='search-row'>"
            f"<td>Search</td><td>{esc(row.get('tenant'))}</td><td>{esc(row.get('session_id'))}</td>"
            f"<td>{esc(row.get('started_at'))}</td><td>{esc(row.get('finished_at'))}</td>"
            f"<td>{sec(row.get('latency_s'))}</td><td>-</td><td>{sec(row.get('admission_wait_s'))}</td>"
            f"<td>{esc(row.get('queue_depth'))}</td><td>{esc(row.get('admission_order'))}</td>"
            f"<td>{esc(row.get('request_id'))}</td><td>{esc(row.get('status_code') or row.get('error'))}</td>"
            "</tr>"
        )

    delayed_note = (
        f"Commit >= {sec(commit.get('delayed_threshold_s'))}: {len(commit_delayed)} 条；"
        f"Search >= {sec(search.get('delayed_threshold_s'))}: {len(search_delayed)} 条。"
    )
    bucket_rows = []
    for bucket in time_buckets:
        bc = bucket.get("commit") or {}
        bs = bucket.get("search") or {}
        bucket_rows.append(
            "<tr>"
            f"<td>{esc(bucket.get('bucket'))}</td>"
            f"<td>{sec(bucket.get('start_s'))} - {sec(bucket.get('end_s'))}</td>"
            f"<td>{esc(', '.join(bucket.get('tenants') or []))}</td>"
            f"<td>{esc(bc.get('submitted', 0))} / {esc(bc.get('completed', 0))}</td>"
            f"<td>{sec((bc.get('latency') or {}).get('mean_s'))}</td>"
            f"<td>{sec((bc.get('latency') or {}).get('p95_s'))}</td>"
            f"<td>{esc(bc.get('delayed', 0))}</td>"
            f"<td>{esc(bs.get('submitted', 0))} / {esc(bs.get('succeeded', 0))}</td>"
            f"<td>{sec((bs.get('latency') or {}).get('mean_s'))}</td>"
            f"<td>{sec((bs.get('latency') or {}).get('p95_s'))}</td>"
            f"<td>{esc(bs.get('delayed', 0))}</td>"
            "</tr>"
        )
    timeline_rows = []
    for item in workload_timeline[:500]:
        timeline_rows.append(
            "<tr>"
            f"<td>{esc(item.get('workload_offset_s'))}</td>"
            f"<td>{esc(item.get('operation'))}</td><td>{esc(item.get('tenant'))}</td>"
            f"<td>{esc(item.get('queued_at'))}</td><td>{esc(item.get('started_at'))}</td>"
            f"<td>{esc(item.get('completed_at'))}</td><td>{sec(item.get('duration_s'))}</td>"
            f"<td>{sec(item.get('queue_wait_s'))}</td><td>{sec(item.get('admission_wait_s'))}</td>"
            f"<td>{esc(item.get('queue_depth'))}</td><td>{esc(item.get('admission_order'))}</td>"
            f"<td>{esc(item.get('status_code') or item.get('status'))}</td>"
            f"<td>{'是' if item.get('delayed') else '否'}</td>"
            "</tr>"
        )
    slow_rows = []
    for row in commit_slowest:
        slow_rows.append(
            "<tr class='commit-row'>"
            f"<td>Commit</td><td>{esc(row.get('tenant'))}</td><td>{esc(row.get('session_id'))}</td>"
            f"<td>{esc(row.get('queued_at'))}</td><td>{esc(row.get('started_at'))}</td>"
            f"<td>{esc(row.get('completed_at'))}</td><td>{sec(row.get('queue_wait_s'))}</td>"
            f"<td>{sec(row.get('service_s'))}</td><td>{sec(row.get('end_to_end_s'))}</td>"
            f"<td>{esc(row.get('admission_queue_depth'))}</td><td>{esc(row.get('request_id'))}</td>"
            f"<td>{esc(row.get('status') or row.get('error'))}</td></tr>"
        )
    for row in search_slowest:
        slow_rows.append(
            "<tr class='search-row'>"
            f"<td>Search</td><td>{esc(row.get('tenant'))}</td><td>{esc(row.get('session_id'))}</td>"
            f"<td>{esc(row.get('queued_at'))}</td><td>{esc(row.get('started_at'))}</td>"
            f"<td>{esc(row.get('finished_at'))}</td><td>{sec(row.get('queue_wait_s'))}</td>"
            f"<td>{sec(row.get('service_s'))}</td><td>{sec(row.get('end_to_end_s'))}</td>"
            f"<td>{esc(row.get('admission_queue_depth'))}</td><td>{esc(row.get('request_id'))}</td>"
            f"<td>{esc(row.get('status_code') or row.get('error'))}</td></tr>"
        )
    isolation_rows = []
    for probe in isolation.get("probes") or []:
        isolation_rows.append(
            "<tr>"
            f"<td>{esc(probe.get('writer'))}</td><td>{esc(probe.get('reader'))}</td>"
            f"<td>{esc(probe.get('marker'))}</td><td>{'是' if probe.get('expected') else '否'}</td>"
            f"<td>{'是' if probe.get('marker_found') else '否'}</td>"
            f"<td>{esc(probe.get('status_code'))}</td><td>{sec(probe.get('latency_s'))}</td>"
            f"<td>{esc(len(probe.get('attempts') or []))}</td><td>{esc(probe.get('request_id'))}</td>"
            f"<td>{'PASS' if probe.get('marker_found') == probe.get('expected') else 'FAIL'}</td>"
            "</tr>"
        )
    policy_header = (
        f"""<div class="policy-head">
        <div><span class="eyebrow">策略</span><h2>{esc(policy)}</h2></div>
        <span class="status">{esc(summary.get('status'))}</span>
      </div>"""
        if include_header
        else ""
    )
    return f"""
    <section class="policy">
      {policy_header}
      <div class="facts">
        <div><span>租户数</span><b>{esc(params.get('tenants'))}</b></div>
        <div><span>Session / 租户</span><b>{esc(params.get('sessions_per_tenant'))}</b></div>
        <div><span>正式时长</span><b>{esc(params.get('duration_s'))}s</b></div>
        <div><span>Search 目标</span><b>{esc(params.get('search_rps'))} RPS</b></div>
        <div><span>Commit 提交/完成/失败</span><b>{esc(commit.get('submitted'))} / {esc(commit.get('completed'))} / {esc(commit.get('failed'))}</b></div>
        <div><span>Search 提交/成功/失败</span><b>{esc(search.get('submitted'))} / {esc(search.get('succeeded'))} / {esc(search.get('errors'))}</b></div>
        <div><span>Commit 目标 / 实际 / 缺口</span><b>{esc(targets.get('commit_submitted', commit.get('submitted', 0)))} / {esc(commit.get('submitted', 0))} / {esc(targets.get('commit_gap', 0))}</b></div>
        <div><span>Search 目标 / 实际 / 缺口</span><b>{esc(targets.get('search_submitted', search.get('submitted', 0)))} / {esc(search.get('submitted', 0))} / {esc(targets.get('search_gap', 0))}</b></div>
        <div><span>Commit 成功率</span><b>{pct(commit.get('success_rate'))}</b></div>
        <div><span>Search 成功率</span><b>{pct(search.get('success_rate'))}</b></div>
        <div><span>最大准入队列</span><b>{esc(admission.get('max_queue_depth'))}</b></div>
        <div><span>准入等待平均 / P95 / 最大</span><b>{sec((admission.get('wait') or {}).get('mean_s'))} / {sec((admission.get('wait') or {}).get('p95_s'))} / {sec((admission.get('wait') or {}).get('max_s'))}</b></div>
        <div><span>服务端 /metrics 采样</span><b>{esc(details.get('server_metrics_samples', 0))} 点 · {'可用' if details.get('server_metrics_available') else '不可用'}</b></div>
      </div>
      <div class="metric-columns">
        {metric_group(commit.get('completion') or {}, 'Commit 端到端完成时间')}
        {metric_group(commit.get('queue_wait') or {}, 'Commit 客户端排队时间')}
        {metric_group(commit.get('service') or {}, 'Commit 服务/轮询时间')}
        {metric_group(search.get('latency') or {}, 'Search 服务时间')}
        {metric_group(search.get('admission_wait') or {}, 'Search 客户端排队时间')}
      </div>
      <h3>服务端时序证据</h3>
      <p class="muted">以下数据只在 EchoMem 响应或服务端追踪中提供了
      <code>received_at</code>、<code>queue_entered_at</code>、
      <code>execution_started_at</code>、<code>finished_at</code> 时计算；
      缺失时不能用客户端等待时间替代。</p>
      <div class="metric-columns">
        {metric_group(commit_server.get('queue_wait') or {}, 'Commit 服务端排队时间')}
        {metric_group(commit_server.get('execution') or {}, 'Commit 服务端执行时间')}
        {metric_group(commit_server.get('end_to_end') or {}, 'Commit 服务端端到端')}
        {metric_group(search_server.get('queue_wait') or {}, 'Search 服务端排队时间')}
        {metric_group(search_server.get('execution') or {}, 'Search 服务端执行时间')}
      </div>
      <div class="facts compact-facts">
        <div><span>Commit 服务端时序覆盖</span><b>{esc(commit_server.get('observed_count', 0))} / {esc(commit_server.get('total_count', 0))}</b></div>
        <div><span>Search 服务端时序覆盖</span><b>{esc(search_server.get('observed_count', 0))} / {esc(search_server.get('total_count', 0))}</b></div>
        <div><span>Commit 缺失证据</span><b>{esc(commit_server.get('missing_count', 0))} 条</b></div>
        <div><span>Search 缺失证据</span><b>{esc(search_server.get('missing_count', 0))} 条</b></div>
      </div>
      <div class="facts compact-facts">
        <div><span>最慢 Commit</span><b>{sec(commit.get('completion', {}).get('max_s'))}</b></div>
        <div><span>最慢 Search</span><b>{sec(search.get('latency', {}).get('max_s'))}</b></div>
        <div><span>服务端 /metrics 样本</span><b>{esc(server_sample_count)} 点</b></div>
        <div><span>服务端队列/限流字段</span><b>{'已提供' if any('queue' in name or 'rate' in name or 'retry' in name for name in server_metrics) else '未提供'}</b></div>
        <div><span>服务端常驻内存</span><b>{num(server_metrics.get('echomem_process_resident_memory_bytes') / 1048576) if server_metrics.get('echomem_process_resident_memory_bytes') is not None else '-'} MB</b></div>
        <div><span>服务端 HTTP 请求总量</span><b>{num(server_metrics.get('echomem_http_requests_total'), 0) if server_metrics.get('echomem_http_requests_total') is not None else '-'}</b></div>
      </div>
      <h3>租户隔离探针</h3>
      <p class="muted">覆盖 {esc(isolation.get('probe_count', 0))} / {esc(isolation.get('expected_probe_count', 0))}，异常 {esc(isolation.get('invalid_probe_count', 0))}；同租户搜索失败会按配置重试，跨租户不重试。</p>
      <div class="scroll"><table><thead><tr>
        <th>写入租户</th><th>读取租户</th><th>随机标记</th><th>期望命中</th><th>实际命中</th><th>HTTP</th><th>最后耗时</th><th>尝试次数</th><th>Search request ID</th><th>结果</th>
      </tr></thead><tbody>
        {''.join(isolation_rows) or "<tr><td colspan='10'>没有隔离探针数据</td></tr>"}
      </tbody></table></div>
      <h3>逐租户完整统计</h3>
      <div class="scroll"><table><thead><tr>
        <th>租户</th><th>Commit 提交/完成/失败</th><th>Commit 平均</th><th>P50</th><th>P95</th><th>P99</th><th>最大</th>
        <th>Commit 排队平均</th><th>Commit 排队最大</th><th>Search 提交/成功/失败</th><th>Search 平均</th><th>Search P95</th><th>Search P99</th>
      </tr></thead><tbody>
        {''.join(tenant_rows) or "<tr><td colspan='13'>无逐租户数据</td></tr>"}
      </tbody></table></div>
      <h3>延迟事件时间线</h3>
      <p class="muted">{esc(delayed_note)} 时间为 UTC；admission wait 是压测端等待，不等于服务端内部排队。</p>
      <div class="scroll"><table><thead><tr>
        <th>类型</th><th>租户</th><th>Session</th><th>开始/入队</th><th>完成</th><th>端到端/服务</th>
        <th>客户端排队</th><th>准入等待</th><th>队列深度</th><th>顺序</th><th>服务端请求 ID</th><th>状态</th>
      </tr></thead><tbody>
        {''.join(delayed_rows) or "<tr><td colspan='12'>没有超过阈值的请求</td></tr>"}
      </tbody></table></div>
      <h3>按分钟负载与延迟</h3>
      <p class="muted">按请求入队时间分桶；Commit/Search 的平均和 P95 只统计成功请求，延迟数统计该分钟全部超阈值请求。</p>
      <div class="scroll"><table><thead><tr>
        <th>分钟桶</th><th>相对时间</th><th>涉及租户</th><th>Commit 提交/完成</th><th>Commit 平均</th><th>Commit P95</th><th>Commit 延迟</th>
        <th>Search 提交/成功</th><th>Search 平均</th><th>Search P95</th><th>Search 延迟</th>
      </tr></thead><tbody>
        {''.join(bucket_rows) or "<tr><td colspan='11'>没有时间分桶数据（旧结果可能需要重新运行）</td></tr>"}
      </tbody></table></div>
      <h3>完整请求时间线（最多 500 条）</h3>
      <p class="muted">相对时间从本轮第一条请求入队开始计算；这是调度交替和“哪个租户先被延迟”的直接证据。</p>
      <div class="scroll"><table><thead><tr>
        <th>相对秒</th><th>操作</th><th>租户</th><th>入队</th><th>开始</th><th>完成</th><th>端到端</th>
        <th>客户端排队</th><th>准入等待</th><th>队列深度</th><th>准入顺序</th><th>状态</th><th>超阈值</th>
      </tr></thead><tbody>
        {''.join(timeline_rows) or "<tr><td colspan='13'>没有请求时间线（旧结果可能需要重新运行）</td></tr>"}
      </tbody></table></div>
      <h3>最慢请求明细（Commit / Search 各最多 20 条）</h3>
      <p class="muted">即使没有超过阈值，也保留最慢请求，避免“没有延迟事件”掩盖实际尾延迟。当前压测 CSV 没有独立的服务端执行开始时间时，会显示为空。</p>
      <div class="scroll"><table><thead><tr>
        <th>类型</th><th>租户</th><th>Session</th><th>入队</th><th>执行开始</th><th>完成</th>
        <th>客户端排队</th><th>服务/轮询</th><th>端到端</th><th>队列深度</th><th>request ID</th><th>状态</th>
      </tr></thead><tbody>
        {''.join(slow_rows) or "<tr><td colspan='12'>没有请求明细</td></tr>"}
      </tbody></table></div>
      <details><summary>原始文件</summary>
        <p><a href="{esc((directory / 'summary.json').name)}">summary.json</a> ·
        <a href="{esc((directory / 'commit_results.csv').name)}">commit_results.csv</a> ·
        <a href="{esc((directory / 'search_results.csv').name)}">search_results.csv</a> ·
        <a href="{esc((directory / 'resource_samples.csv').name)}">resource_samples.csv</a> ·
        <a href="{esc((directory / 'server_metrics.csv').name)}">server_metrics.csv</a> ·
        <a href="{esc((directory / 'server_metrics.jsonl').name)}">server_metrics.jsonl</a></p>
        <pre>{esc(json.dumps(summary, ensure_ascii=False, indent=2))}</pre>
      </details>
    </section>
    """


def render(matrix_path: Path, output_path: Path) -> None:
    policies = load_matrix(matrix_path)
    if not policies:
        raise SystemExit(f"no summaries in {matrix_path}")
    first = policies[0][1]
    first_details = first.get("details") or {}
    first_params = first.get("parameters") or {}
    identity_mode = first_details.get("identity_mode", "unknown")
    real_multi = identity_mode == "independent_auth_keys" and int(first_params.get("tenants") or 0) > 1
    identity_text = (
        "已使用独立认证身份，可用于多租户隔离分析。"
        if real_multi
        else "当前结果使用共享认证身份，只能作为压测端性能对照，不能证明真实多租户隔离、公平性或服务端限流。"
    )
    identity_class = "valid" if real_multi else "warning"
    descriptions = {
        "fifo": "Search 和 Commit 共用一个准入队列，按到达顺序处理。",
        "search-priority": "Search 优先获得准入，重点观察 Commit 是否被长期挤压。",
        "dual-lane": "Search 和 Commit 使用独立准入通道，各自有容量。",
        "tenant-fair": "按租户轮询准入，租户内部保持 FIFO。",
        "dual-lane-tenant-fair": "双通道，并在每条通道内按租户公平轮询。",
    }
    comparison_rows = []
    for policy, summary, _ in policies:
        metrics = summary.get("metrics") or {}
        commit = metrics.get("commit") or {}
        search = metrics.get("search") or {}
        c = commit.get("completion") or {}
        s = search.get("latency") or {}
        comparison_rows.append(
            f"<tr><td><b>{esc(policy)}</b><small>{esc(descriptions.get(policy, ''))}</small></td>"
            f"<td>{esc(summary.get('status'))}</td><td>{esc(commit.get('submitted'))}/{esc(commit.get('completed'))}</td>"
            f"<td>{sec(c.get('mean_s'))}</td><td>{sec(c.get('p50_s'))}</td><td>{sec(c.get('p95_s'))}</td><td>{sec(c.get('p99_s'))}</td><td>{sec(c.get('max_s'))}</td>"
            f"<td>{sec(s.get('mean_s'))}</td><td>{sec(s.get('p95_s'))}</td><td>{sec(s.get('p99_s'))}</td><td>{sec(s.get('max_s'))}</td>"
            f"<td>{esc((metrics.get('admission') or {}).get('max_queue_depth'))}</td></tr>"
        )
    policy_detail_blocks = []
    for policy, summary, directory in policies:
        metrics = summary.get("metrics") or {}
        commit = metrics.get("commit") or {}
        search = metrics.get("search") or {}
        commit_stats = commit.get("completion") or {}
        search_stats = search.get("latency") or {}
        policy_html = policy_section(
            policy, summary, directory, include_header=False
        )
        policy_body = policy_html.split(
            '<section class="policy">', 1
        )[1].rsplit("</section>", 1)[0]
        policy_detail_blocks.append(
            f"<details class='policy'><summary><div class='policy-head'>"
            f"<div><span class='eyebrow'>策略详情</span><h2>{esc(policy)}</h2>"
            f"<div class='policy-summary'>Commit 平均 {sec(commit_stats.get('mean_s'))} · "
            f"P95 {sec(commit_stats.get('p95_s'))} · Search 平均 {sec(search_stats.get('mean_s'))} · "
            f"P95 {sec(search_stats.get('p95_s'))} · "
            f"Commit {esc(commit.get('completed'))}/{esc(commit.get('submitted'))} · "
            f"Search {esc(search.get('succeeded'))}/{esc(search.get('submitted'))}</div></div>"
            f"<span class='status'>{esc(summary.get('status'))}</span></div></summary>"
            f"<div class='policy-body'>{policy_body}</div></details>"
        )
    icon = """<svg class="icon" viewBox="0 0 56 56" role="img" aria-label="压测报告">
      <rect x="3" y="3" width="50" height="50" rx="13" fill="#17324d"/>
      <path d="M13 40V29M22 40V20M31 40V25M40 40V13" stroke="#72d5b7" stroke-width="4" stroke-linecap="round"/>
      <path d="M11 44h34M12 17l8-5 8 6 12-9" fill="none" stroke="#ff9d6e" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>"""
    favicon = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%2317324d'/%3E%3Cpath d='M14 48V35M25 48V24M36 48V30M47 48V16' stroke='%2372d5b7' stroke-width='5' stroke-linecap='round'/%3E%3Cpath d='M10 52h44M12 20l10-7 10 8 16-12' fill='none' stroke='%23ff9d6e' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E"
    valid_count = sum(1 for _, summary, _ in policies if str(summary.get("status")) == "PASS")
    inconclusive_count = sum(1 for _, summary, _ in policies if str(summary.get("status")) == "INCONCLUSIVE")
    best_commit = min(
        policies,
        key=lambda item: float(((item[1].get("metrics") or {}).get("commit") or {}).get("completion", {}).get("p95_s") or 1e9),
    )
    best_search = min(
        policies,
        key=lambda item: float(((item[1].get("metrics") or {}).get("search") or {}).get("latency", {}).get("p95_s") or 1e9),
    )
    chart_rows = []
    chart_max = max(
        [
            float((((summary.get("metrics") or {}).get("commit") or {}).get("completion") or {}).get("p95_s") or 0)
            for _, summary, _ in policies
        ]
        + [
            float((((summary.get("metrics") or {}).get("search") or {}).get("latency") or {}).get("p95_s") or 0)
            for _, summary, _ in policies
        ]
        + [1.0]
    )
    for policy, summary, _ in policies:
        metrics = summary.get("metrics") or {}
        c_p95 = float((((metrics.get("commit") or {}).get("completion") or {}).get("p95_s")) or 0)
        s_p95 = float((((metrics.get("search") or {}).get("latency") or {}).get("p95_s")) or 0)
        chart_rows.append(
            f"<div class='chart-row'><div class='chart-label'>{esc(policy)}</div>"
            f"<div class='chart-track'><span class='bar commit' style='width:{c_p95 / chart_max * 100:.1f}%'></span>"
            f"<span class='bar search' style='width:{s_p95 / chart_max * 100:.1f}%'></span></div>"
            f"<div class='chart-values'><b>{c_p95:.2f}s</b><span>{s_p95:.2f}s</span></div></div>"
        )
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="{favicon}">
<title>EchoMem 多租户压测审计报告</title>
<style>
 :root{{--bg:#f2f5f7;--paper:#fff;--ink:#17212b;--muted:#71808d;--line:#dfe6eb;--navy:#17324d;--teal:#177b63;--teal-soft:#eaf5f1;--orange:#e77f56;--amber:#9b6b16;--amber-soft:#fff7df;--blue:#286aa6}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
main{{max-width:1480px;margin:auto;padding:28px 20px 60px}}.top{{display:flex;align-items:center;gap:14px;margin-bottom:20px}}.icon{{width:52px;height:52px;flex:none}}
h1{{margin:0;font-size:25px;line-height:1.2;letter-spacing:0}}h2{{margin:0;font-size:19px;letter-spacing:0}}h3{{margin:22px 0 10px;font-size:15px;letter-spacing:0}}h4{{margin:0 0 10px;font-size:13px}}
.muted,small{{color:var(--muted)}}.sub{{margin-top:4px;color:var(--muted)}}.banner,.panel,.policy{{background:var(--paper);border:1px solid var(--line);border-radius:8px}}
.banner{{padding:16px 19px;border-left:5px solid var(--amber);background:var(--amber-soft);color:#6d5116;margin-bottom:14px}}.banner.valid{{border-left-color:var(--teal);background:var(--teal-soft);color:#155b49}}
.summary{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px}}.summary>div{{background:var(--paper);border:1px solid var(--line);padding:14px 15px;border-radius:8px}}
.summary span,.facts span,.metric-grid span{{display:block;color:var(--muted);font-size:12px}}.summary b{{display:block;font-size:21px;margin-top:3px}}
.panel,.policy{{padding:18px 19px;margin-top:14px}}.panel-head,.policy-head{{display:flex;justify-content:space-between;align-items:baseline;gap:12px}}.status{{font-weight:800;color:var(--amber)}}
.callout{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}}.callout>div{{padding:13px 14px;border:1px solid var(--line);background:#fbfcfc;border-radius:7px}}.callout b{{display:block;margin-top:4px;font-size:16px}}
.legend{{display:flex;gap:16px;color:var(--muted);font-size:12px;margin:14px 0 4px}}.legend i{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px}}.legend .c{{background:var(--teal)}}.legend .s{{background:var(--orange)}}
.chart-row{{display:grid;grid-template-columns:170px 1fr 90px;align-items:center;gap:10px;margin:10px 0}}.chart-label{{font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.chart-track{{height:16px;background:#edf1f3;border-radius:3px;overflow:hidden;position:relative}}.bar{{display:block;height:8px;position:absolute;left:0;border-radius:3px}}.bar.commit{{top:1px;background:var(--teal)}}.bar.search{{top:9px;background:var(--orange)}}.chart-values{{font-size:12px;color:var(--orange);text-align:right}}.chart-values b{{color:var(--teal);margin-right:6px}}
.scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap}}th,td{{padding:9px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{color:var(--muted);background:#fafbfc;font-weight:700}}td small{{display:block;white-space:normal;max-width:220px;margin-top:3px}}
.facts{{display:grid;grid-template-columns:repeat(5,1fr);gap:0 22px;margin-top:14px}}.facts div{{padding:8px 0;border-bottom:1px solid var(--line)}}.facts b{{display:block;margin-top:2px}}.compact-facts{{grid-template-columns:repeat(6,1fr);margin-top:10px}}
.metric-columns{{display:grid;grid-template-columns:repeat(5,1fr);gap:9px;margin-top:15px}}.metric-block{{border:1px solid var(--line);padding:12px 11px;background:#fbfcfc}}.metric-grid{{display:grid;grid-template-columns:1fr 1fr;gap:7px 10px}}.metric-grid b{{display:block;margin-top:2px;font-size:13px}}
.eyebrow{{font-size:11px;text-transform:uppercase;color:var(--muted);letter-spacing:.08em}}.policy-head h2{{margin-top:2px}}.policy-summary{{margin-top:5px;color:var(--muted);font-size:12px;font-weight:400}}.commit-row td:first-child{{color:var(--teal);font-weight:700}}.search-row td:first-child{{color:var(--orange);font-weight:700}}
.policy{{padding:0;overflow:hidden}}.policy>summary{{padding:17px 19px;cursor:pointer;list-style:none}}.policy>summary::-webkit-details-marker{{display:none}}.policy>summary:after{{content:"展开详情";float:right;color:var(--blue);font-size:12px}}.policy[open]>summary:after{{content:"收起详情"}}.policy-body{{padding:0 19px 18px;border-top:1px solid var(--line)}}
details.raw{{border-top:1px solid var(--line);padding-top:10px;margin-top:14px}}summary{{cursor:pointer;font-weight:700}}pre{{max-height:420px;overflow:auto;background:#f7f9fa;padding:12px;font-size:11px;white-space:pre-wrap}}a{{color:var(--blue)}}
@media(max-width:1050px){{.facts,.compact-facts{{grid-template-columns:repeat(3,1fr)}}.metric-columns{{grid-template-columns:repeat(3,1fr)}}}}@media(max-width:700px){{main{{padding:20px 12px 44px}}.summary{{grid-template-columns:1fr 1fr}}.facts,.compact-facts,.metric-columns,.callout{{grid-template-columns:1fr 1fr}}.chart-row{{grid-template-columns:105px 1fr 76px}}.chart-label{{font-size:12px}}}}
</style></head><body><main>
<header class="top">{icon}<div><h1>EchoMem 多租户压测审计报告</h1><div class="sub">策略矩阵 · 先看结论，再展开证据 · {esc(first.get('finished_at'))}</div></div></header>
<div class="banner {identity_class}"><b>证据等级：{'真实多租户' if real_multi else '性能对照，不能作为多租户结论'}</b><br>{esc(identity_text)}
 服务地址：<code>{esc(first.get('base_url'))}</code>，认证模式：<code>{esc(identity_mode)}</code>。</div>
<div class="summary">
  <div><span>策略数</span><b>{len(policies)}</b><small>{valid_count} 个通过 · {inconclusive_count} 个样本不足</small></div>
  <div><span>租户 / 正式时长</span><b>{esc(first_params.get('tenants'))} / {esc(first_params.get('duration_s'))}s</b><small>不含预热和排空</small></div>
  <div><span>Search 目标</span><b>{esc(first_params.get('search_rps'))} RPS</b><small>实际吞吐按完成请求计算</small></div>
  <div><span>隔离探针</span><b>{'PASS' if real_multi and (first_details.get('isolation') or {}).get('status') == 'PASS' else '需谨慎'}</b><small>{esc((first_details.get('isolation') or {}).get('probe_count', 0))} / {esc((first_details.get('isolation') or {}).get('expected_probe_count', 0))} 条</small></div>
</div>
<section class="panel"><div class="panel-head"><h2>一眼看懂</h2><span class="muted">P95 越低越好，单位：秒</span></div>
<div class="callout">
  <div><span>Commit P95 最低</span><b>{esc(best_commit[0])} · {sec((((best_commit[1].get('metrics') or {}).get('commit') or {}).get('completion') or {}).get('p95_s'))}</b></div>
  <div><span>Search P95 最低</span><b>{esc(best_search[0])} · {sec((((best_search[1].get('metrics') or {}).get('search') or {}).get('latency') or {}).get('p95_s'))}</b></div>
</div>
<div class="legend"><span><i class="c"></i>Commit P95</span><span><i class="s"></i>Search P95</span></div>
{''.join(chart_rows)}
</section>
<section class="panel"><div class="panel-head"><h2>策略对比</h2><span class="muted">完整 P50 / P95 / P99 和队列数据见下方详情</span></div>
<div class="scroll"><table><thead><tr><th>策略 / 含义</th><th>状态</th><th>Commit 提交/完成</th><th>Commit 平均</th><th>P50</th><th>P95</th><th>P99</th><th>最大</th><th>Search 平均</th><th>Search P95</th><th>Search P99</th><th>Search 最大</th><th>最大队列</th></tr></thead>
<tbody>{''.join(comparison_rows)}</tbody></table></div></section>
<section class="panel"><div class="panel-head"><h2>测试边界</h2><span class="muted">避免把压测端数据误读成服务端指标</span></div>
<p>本矩阵在同一批真实请求负载下比较 FIFO、Search 优先、双通道和租户公平策略。报告里的“准入等待”和“队列深度”主要来自压测端；只有服务端同时提供 request ID、服务端队列深度、429 和 Retry-After，才能确认服务端限流行为。</p>
</section>
{''.join(policy_detail_blocks)}
<div class="muted" style="margin-top:16px">原始矩阵：{esc(matrix_path)}。本报告不隐藏失败请求；完整请求记录保存在各策略目录的 CSV 中。</div>
</main></body></html>"""
    output_path.write_text(document, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Render auditable EchoMem matrix report")
    parser.add_argument("matrix_json", type=Path)
    parser.add_argument("output_html", type=Path)
    args = parser.parse_args()
    render(args.matrix_json, args.output_html)
    print(args.output_html)


if __name__ == "__main__":
    main()
