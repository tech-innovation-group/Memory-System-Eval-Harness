#!/usr/bin/env python3
"""Render a data-first report for the formal EchoMem stress suite.

The report reads the suite manifest and every request CSV.  It intentionally
does not turn a PASS/FAIL flag into a performance conclusion: all rates,
quantiles, queue timings, missing evidence, and delayed requests remain
visible in the output.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OK_STATES = {"completed", "complete", "transcommit", "succeeded", "success"}


def esc(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return html.escape(str(value))


def number(value: Any, digits: int = 3) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def seconds(value: Any) -> str:
    rendered = number(value, 3)
    return "-" if rendered == "-" else f"{rendered}s"


def percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "-"


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * p / 100.0
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def stats(values: list[float]) -> dict[str, Any]:
    return {
        "count": len(values),
        "mean": statistics.mean(values) if values else None,
        "min": min(values) if values else None,
        "p50": percentile(values, 50),
        "p90": percentile(values, 90),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values) if values else None,
        "total": sum(values) if values else 0.0,
    }


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def float_value(row: dict[str, str], *names: str) -> float | None:
    for name in names:
        try:
            value = float(row.get(name) or "")
        except (TypeError, ValueError):
            continue
        if value >= 0:
            return value
    return None


def int_value(row: dict[str, str], *names: str) -> int | None:
    value = float_value(row, *names)
    return int(value) if value is not None else None


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def timestamp_delta(row: dict[str, str], start: str, end: str) -> float | None:
    left = parse_timestamp(row.get(start))
    right = parse_timestamp(row.get(end))
    if not left or not right:
        return None
    value = (right - left).total_seconds()
    return value if value >= 0 else None


def policy_label(value: str) -> str:
    return {
        "fifo": "FIFO",
        "search-priority": "Search 优先",
        "dual-lane": "双通道",
        "tenant-fair": "租户公平",
        "dual-lane-tenant-fair": "双通道 + 租户公平",
    }.get(value, value)


def scenario_label(value: str) -> str:
    return {
        "baseline": "单租户基线",
        "mixed": "均衡混合负载",
        "commit-storm": "Commit 压力",
        "search-storm": "Search 压力",
        "soak": "长稳态",
    }.get(value, value)


def normalize_row(row: dict[str, str], operation: str, summary: dict[str, Any]) -> dict[str, Any]:
    params = summary.get("parameters") or {}
    if operation == "commit":
        status = str(row.get("status") or "").lower()
        successful = status in OK_STATES
        duration = float_value(row, "end_to_end_s", "elapsed_s") or 0.0
        threshold = float(params.get("commit_delay_threshold_s") or 10.0)
        completed_at = row.get("completed_at", "")
        server_queue = timestamp_delta(
            row, "server_queue_entered_at", "server_execution_started_at"
        )
        server_execution = timestamp_delta(
            row, "server_execution_started_at", "server_finished_at"
        )
        server_e2e = timestamp_delta(
            row, "server_received_at", "server_finished_at"
        )
        return {
            "operation": operation,
            "tenant": row.get("tenant") or "-",
            "session_id": row.get("session_id") or "-",
            "request_id": row.get("request_id") or "-",
            "status": status or "-",
            "status_code": row.get("status_code") or "-",
            "successful": successful,
            "duration": duration,
            "queue_wait": float_value(row, "queue_wait_s") or 0.0,
            "service": float_value(row, "service_s") or 0.0,
            "client_admission_wait": float_value(row, "admission_wait_s") or 0.0,
            "client_queue_depth": int_value(row, "admission_queue_depth", "queue_depth_at_enqueue"),
            "server_queue": server_queue,
            "server_execution": server_execution,
            "server_e2e": server_e2e,
            "server_queue_depth": int_value(row, "server_queue_depth"),
            "server_active_workers": int_value(row, "server_active_workers"),
            "queued_at": row.get("queued_at") or row.get("accepted_at") or "-",
            "started_at": row.get("started_at") or "-",
            "finished_at": completed_at or "-",
            "server_received_at": row.get("server_received_at") or "-",
            "server_execution_started_at": row.get("server_execution_started_at") or "-",
            "delayed": duration >= threshold,
            "threshold": threshold,
            "retry_after": float_value(row, "retry_after_s"),
            "error": row.get("error") or "",
        }
    status_code = int_value(row, "status_code")
    successful = status_code is not None and 200 <= status_code < 300
    duration = float_value(row, "end_to_end_s", "service_s", "elapsed_s") or 0.0
    threshold = float(params.get("search_delay_threshold_s") or 2.5)
    return {
        "operation": operation,
        "tenant": row.get("tenant") or "-",
        "session_id": row.get("session_id") or "-",
        "request_id": row.get("request_id") or "-",
        "status": str(status_code) if status_code is not None else (row.get("error") or "-"),
        "status_code": status_code if status_code is not None else "-",
        "successful": successful,
        "duration": duration,
        "queue_wait": float_value(row, "queue_wait_s") or 0.0,
        "service": float_value(row, "service_s", "elapsed_s") or 0.0,
        "client_admission_wait": float_value(row, "admission_wait_s") or 0.0,
        "client_queue_depth": int_value(row, "admission_queue_depth", "queue_depth_at_enqueue"),
        "server_queue": timestamp_delta(
            row, "server_queue_entered_at", "server_execution_started_at"
        ),
        "server_execution": timestamp_delta(
            row, "server_execution_started_at", "server_finished_at"
        ),
        "server_e2e": timestamp_delta(
            row, "server_received_at", "server_finished_at"
        ),
        "server_queue_depth": int_value(row, "server_queue_depth"),
        "server_active_workers": int_value(row, "server_active_workers"),
        "queued_at": row.get("queued_at") or row.get("started_at") or "-",
        "started_at": row.get("started_at") or "-",
        "finished_at": row.get("finished_at") or "-",
        "server_received_at": row.get("server_received_at") or "-",
        "server_execution_started_at": row.get("server_execution_started_at") or "-",
        "delayed": duration >= threshold,
        "threshold": threshold,
        "retry_after": float_value(row, "retry_after_s"),
        "error": row.get("error") or "",
    }


def load_run(run: dict[str, Any], root: Path) -> dict[str, Any]:
    summary = run.get("summary") or {}
    output_dir = Path(run.get("output_dir") or "")
    if not output_dir.is_absolute():
        output_dir = root / output_dir
    commits = [
        normalize_row(row, "commit", summary)
        for row in read_csv(output_dir / "commit_results.csv")
    ]
    searches = [
        normalize_row(row, "search", summary)
        for row in read_csv(output_dir / "search_results.csv")
    ]
    all_rows = commits + searches
    anchor_candidates = [
        parse_timestamp(row["queued_at"])
        for row in all_rows
        if parse_timestamp(row["queued_at"]) is not None
    ]
    anchor = min(anchor_candidates) if anchor_candidates else None
    for row in all_rows:
        queued = parse_timestamp(row["queued_at"])
        row["run_key"] = str(output_dir)
        row["run_offset_s"] = (
            (queued - anchor).total_seconds()
            if queued is not None and anchor is not None
            else None
        )
    return {
        "scenario": str(run.get("scenario") or "-"),
        "scenario_label": str(run.get("scenario_label") or scenario_label(str(run.get("scenario") or "-"))),
        "repetition": run.get("repetition") or "-",
        "policy": str(run.get("policy") or "-"),
        "status": str(run.get("status") or summary.get("status") or "NO_SUMMARY"),
        "summary": summary,
        "output_dir": output_dir,
        "commits": commits,
        "searches": searches,
    }


def time_buckets(group: dict[str, Any]) -> list[dict[str, Any]]:
    """Aggregate requests by minute within each repeated run."""
    buckets: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in group["commits"] + group["searches"]:
        offset = row.get("run_offset_s")
        if offset is None:
            continue
        key = (str(row.get("run_key")), max(0, int(float(offset) // 60)))
        buckets.setdefault(key, []).append(row)
    output = []
    for (run_key, minute), rows in sorted(buckets.items()):
        commits = [row for row in rows if row["operation"] == "commit"]
        searches = [row for row in rows if row["operation"] == "search"]
        commit_success = [row for row in commits if row["successful"]]
        search_success = [row for row in searches if row["successful"]]
        output.append(
            {
                "run_key": run_key,
                "minute": minute,
                "commit_submitted": len(commits),
                "commit_completed": len(commit_success),
                "commit_mean": stats([row["duration"] for row in commit_success])["mean"],
                "commit_p95": stats([row["duration"] for row in commit_success])["p95"],
                "commit_delayed": sum(row["delayed"] for row in commits),
                "search_submitted": len(searches),
                "search_succeeded": len(search_success),
                "search_mean": stats([row["duration"] for row in search_success])["mean"],
                "search_p95": stats([row["duration"] for row in search_success])["p95"],
                "search_delayed": sum(row["delayed"] for row in searches),
                "rate_limited": sum(row["status_code"] == 429 for row in rows),
            }
        )
    return output


def group_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for run in runs:
        groups.setdefault((run["scenario"], run["policy"]), []).append(run)
    result = []
    for (scenario, policy), items in sorted(groups.items()):
        commits = [row for item in items for row in item["commits"]]
        searches = [row for item in items for row in item["searches"]]
        commit_success = [row for row in commits if row["successful"]]
        search_success = [row for row in searches if row["successful"]]
        all_rows = commits + searches
        server_timed = [
            row for row in all_rows
            if row["server_queue"] is not None
            and row["server_execution"] is not None
            and row["server_e2e"] is not None
        ]
        tenants = sorted({row["tenant"] for row in all_rows})
        result.append(
            {
                "scenario": scenario,
                "scenario_label": items[0]["scenario_label"],
                "policy": policy,
                "items": items,
                "commits": commits,
                "searches": searches,
                "commit_success": commit_success,
                "search_success": search_success,
                "tenants": tenants,
                "commit_latency": stats([row["duration"] for row in commit_success]),
                "commit_queue": stats([row["queue_wait"] for row in commits]),
                "commit_server_queue": stats(
                    [row["server_queue"] for row in commits if row["server_queue"] is not None]
                ),
                "commit_server_execution": stats(
                    [row["server_execution"] for row in commits if row["server_execution"] is not None]
                ),
                "search_latency": stats([row["duration"] for row in search_success]),
                "search_queue": stats([row["queue_wait"] for row in searches]),
                "search_server_queue": stats(
                    [row["server_queue"] for row in searches if row["server_queue"] is not None]
                ),
                "search_server_execution": stats(
                    [row["server_execution"] for row in searches if row["server_execution"] is not None]
                ),
                "server_timed_count": len(server_timed),
                "server_timed_total": len(all_rows),
                "delayed": [row for row in all_rows if row["delayed"]],
                "rate_limited": sum(row["status_code"] == 429 for row in all_rows),
            }
        )
    return result


def tenant_groups(group: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for tenant in group["tenants"]:
        commits = [row for row in group["commits"] if row["tenant"] == tenant]
        searches = [row for row in group["searches"] if row["tenant"] == tenant]
        c_success = [row for row in commits if row["successful"]]
        s_success = [row for row in searches if row["successful"]]
        result.append(
            {
                "tenant": tenant,
                "commit_submitted": len(commits),
                "commit_completed": len(c_success),
                "commit_failed": len(commits) - len(c_success),
                "commit_latency": stats([row["duration"] for row in c_success]),
                "commit_queue": stats([row["queue_wait"] for row in commits]),
                "commit_server_queue": stats(
                    [row["server_queue"] for row in commits if row["server_queue"] is not None]
                ),
                "commit_delayed": sum(row["delayed"] for row in commits),
                "search_submitted": len(searches),
                "search_succeeded": len(s_success),
                "search_failed": len(searches) - len(s_success),
                "search_latency": stats([row["duration"] for row in s_success]),
                "search_queue": stats([row["queue_wait"] for row in searches]),
                "search_server_queue": stats(
                    [row["server_queue"] for row in searches if row["server_queue"] is not None]
                ),
                "search_delayed": sum(row["delayed"] for row in searches),
            }
        )
    return result


def metric_cells(group: dict[str, Any]) -> str:
    c = group["commit_latency"]
    s = group["search_latency"]
    cq = group["commit_queue"]
    sq = group["search_queue"]
    return (
        f"<td>{len(group['commits'])}/{len(group['commit_success'])}</td>"
        f"<td>{seconds(c['mean'])}</td><td>{seconds(c['p50'])}</td>"
        f"<td>{seconds(c['p95'])}</td><td>{seconds(c['p99'])}</td><td>{seconds(c['max'])}</td>"
        f"<td>{len(group['searches'])}/{len(group['search_success'])}</td>"
        f"<td>{seconds(s['mean'])}</td><td>{seconds(s['p95'])}</td>"
        f"<td>{seconds(s['p99'])}</td>"
        f"<td>{seconds(cq['p95'])}</td><td>{seconds(sq['p95'])}</td>"
        f"<td>{len(group['delayed'])}</td><td>{group['rate_limited']}</td>"
        f"<td>{group['server_timed_count']}/{group['server_timed_total']}</td>"
    )


def detail_block(group: dict[str, Any], root: Path) -> str:
    tenants = tenant_groups(group)
    tenant_rows = "".join(
        f"<tr><td><b>{esc(item['tenant'])}</b></td>"
        f"<td>{item['commit_submitted']}/{item['commit_completed']}</td>"
        f"<td>{seconds(item['commit_latency']['mean'])}</td><td>{seconds(item['commit_latency']['p50'])}</td>"
        f"<td>{seconds(item['commit_latency']['p95'])}</td><td>{seconds(item['commit_latency']['p99'])}</td>"
        f"<td>{seconds(item['commit_queue']['mean'])}</td><td>{seconds(item['commit_queue']['p95'])}</td>"
        f"<td>{seconds(item['commit_server_queue']['p95'])}</td><td>{item['commit_delayed']}</td>"
        f"<td>{item['search_submitted']}/{item['search_succeeded']}</td>"
        f"<td>{seconds(item['search_latency']['mean'])}</td><td>{seconds(item['search_latency']['p95'])}</td>"
        f"<td>{seconds(item['search_latency']['p99'])}</td><td>{seconds(item['search_queue']['p95'])}</td>"
        f"<td>{seconds(item['search_server_queue']['p95'])}</td><td>{item['search_delayed']}</td></tr>"
        for item in tenants
    )
    delayed_rows = "".join(
        f"<tr><td>{esc(row['operation'])}</td><td>{esc(row['tenant'])}</td>"
        f"<td>{esc(row['request_id'])}</td><td>{esc(row['queued_at'])}</td>"
        f"<td>{seconds(row['duration'])}</td><td>{seconds(row['queue_wait'])}</td>"
        f"<td>{seconds(row['server_queue'])}</td><td>{seconds(row['server_execution'])}</td>"
        f"<td>{esc(row['client_queue_depth'])}</td><td>{esc(row['server_queue_depth'])}</td>"
        f"<td>{esc(row['status'])}</td><td>{esc(row['error'])}</td></tr>"
        for row in sorted(
            group["delayed"],
            key=lambda item: (item["duration"], item["operation"], item["tenant"]),
            reverse=True,
        )
    )
    raw_links: list[str] = []
    for item in group["items"]:
        try:
            relative = item["output_dir"].relative_to(root)
        except ValueError:
            relative = Path(item["output_dir"].name)
        for filename in ("report.html", "summary.json", "commit_results.csv", "search_results.csv", "server_metrics.csv"):
            if (item["output_dir"] / filename).is_file():
                raw_links.append(
                    f"<a href='{esc(str(relative / filename))}'>"
                    f"第 {esc(item['repetition'])} 轮 {esc(filename)}</a>"
                )
    bucket_rows = "".join(
        f"<tr><td>{esc(Path(bucket['run_key']).name)}</td><td>{bucket['minute'] + 1}</td>"
        f"<td>{bucket['commit_submitted']}/{bucket['commit_completed']}</td>"
        f"<td>{seconds(bucket['commit_mean'])}</td><td>{seconds(bucket['commit_p95'])}</td>"
        f"<td>{bucket['commit_delayed']}</td><td>{bucket['search_submitted']}/{bucket['search_succeeded']}</td>"
        f"<td>{seconds(bucket['search_mean'])}</td><td>{seconds(bucket['search_p95'])}</td>"
        f"<td>{bucket['search_delayed']}</td><td>{bucket['rate_limited']}</td></tr>"
        for bucket in time_buckets(group)
    )
    c_server = group["commit_server_queue"]
    s_server = group["search_server_queue"]
    return (
        f"<details><summary><b>{esc(group['scenario_label'])}</b> · "
        f"{esc(policy_label(group['policy']))} · {len(group['commits'])} Commit / "
        f"{len(group['searches'])} Search · 延迟事件 {len(group['delayed'])}</summary>"
        f"<div class='detail-intro'>重复轮次：{len(group['items'])}；服务端时序覆盖："
        f"{group['server_timed_count']}/{group['server_timed_total']}。覆盖不足时，"
        f"下面的服务端排队列显示为缺失，不使用客户端时间替代。</div>"
        f"<h3>逐租户数值</h3><div class='scroll'><table><thead><tr>"
        f"<th>租户</th><th>Commit 提交/完成</th><th>Commit 平均</th><th>Commit P50</th>"
        f"<th>Commit P95</th><th>Commit P99</th><th>客户端排队平均</th><th>客户端排队 P95</th>"
        f"<th>服务端排队 P95</th><th>Commit 延迟数</th><th>Search 提交/成功</th>"
        f"<th>Search 平均</th><th>Search P95</th><th>Search P99</th><th>客户端排队 P95</th>"
        f"<th>服务端排队 P95</th><th>Search 延迟数</th></tr></thead>"
        f"<tbody>{tenant_rows or '<tr><td colspan=17>没有请求数据</td></tr>'}</tbody></table></div>"
        f"<h3>按时间窗口观察延迟</h3><div class='scroll'><table><thead><tr>"
        f"<th>运行目录</th><th>第几分钟</th><th>Commit 提交/完成</th><th>Commit 平均</th>"
        f"<th>Commit P95</th><th>Commit 延迟</th><th>Search 提交/成功</th><th>Search 平均</th>"
        f"<th>Search P95</th><th>Search 延迟</th><th>429</th></tr></thead><tbody>"
        f"{bucket_rows or '<tr><td colspan=11>没有可解析时间戳</td></tr>'}</tbody></table></div>"
        f"<h3>该组总体分位数</h3><div class='metric-grid'>"
        f"<div><span>Commit 服务端排队</span><b>{seconds(c_server['mean'])} / {seconds(c_server['p95'])}</b><small>平均 / P95</small></div>"
        f"<div><span>Commit 服务端执行</span><b>{seconds(group['commit_server_execution']['mean'])} / {seconds(group['commit_server_execution']['p95'])}</b><small>平均 / P95</small></div>"
        f"<div><span>Search 服务端排队</span><b>{seconds(s_server['mean'])} / {seconds(s_server['p95'])}</b><small>平均 / P95</small></div>"
        f"<div><span>Search 服务端执行</span><b>{seconds(group['search_server_execution']['mean'])} / {seconds(group['search_server_execution']['p95'])}</b><small>平均 / P95</small></div>"
        f"<div><span>Commit 延迟阈值</span><b>{seconds(group['commits'][0]['threshold'] if group['commits'] else None)}</b><small>超过阈值 {sum(row['delayed'] for row in group['commits'])} 次</small></div>"
        f"<div><span>Search 延迟阈值</span><b>{seconds(group['searches'][0]['threshold'] if group['searches'] else None)}</b><small>超过阈值 {sum(row['delayed'] for row in group['searches'])} 次</small></div>"
        f"</div><h3>延迟请求逐条记录</h3><div class='scroll'><table><thead><tr>"
        f"<th>类型</th><th>租户</th><th>Request ID</th><th>进入时间</th><th>端到端</th>"
        f"<th>客户端排队</th><th>服务端排队</th><th>服务端执行</th><th>客户端队列</th>"
        f"<th>服务端队列</th><th>状态</th><th>错误</th></tr></thead>"
        f"<tbody>{delayed_rows or '<tr><td colspan=12>本组没有超过阈值的请求</td></tr>'}</tbody></table></div>"
        f"<p class='links'>{' · '.join(raw_links) or '没有原始文件链接'}</p></details>"
    )


def render(manifest_path: Path, output_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = manifest_path.parent.resolve()
    runs = [load_run(run, root) for run in manifest.get("runs") or []]
    groups = group_runs(runs)
    statuses = [run["status"] for run in runs]
    multi_tenant_runs = [
        run
        for run in runs
        if int((run["summary"].get("parameters") or {}).get("tenants") or 0) >= 2
    ]
    independent = bool(multi_tenant_runs) and all(
        (run["summary"].get("details") or {}).get("identity_mode")
        == "independent_auth_keys"
        for run in multi_tenant_runs
    )
    total_rows = sum(len(group["commits"]) + len(group["searches"]) for group in groups)
    delayed_total = sum(len(group["delayed"]) for group in groups)
    server_count = sum(group["server_timed_count"] for group in groups)
    server_total = sum(group["server_timed_total"] for group in groups)
    overall = (
        "FAIL" if any(status in {"FAIL", "ENVIRONMENT_ERROR", "RESET_FAILED", "NO_SUMMARY"} for status in statuses)
        else "INCONCLUSIVE" if any(status == "INCONCLUSIVE" for status in statuses)
        else "PASS"
    )
    status_class = overall.lower().replace("_", "-")
    rows = "".join(
        f"<tr><td>{esc(group['scenario_label'])}</td><td><b>{esc(policy_label(group['policy']))}</b></td>"
        f"<td>{len(group['items'])}</td><td>{esc(group['items'][0]['status'])}</td>"
        f"{metric_cells(group)}</tr>"
        for group in groups
    )
    detail = "".join(detail_block(group, root) for group in groups)
    icon = (
        "<svg class='logo' viewBox='0 0 56 56' role='img' aria-label='EchoMem 压测报告'>"
        "<rect x='3' y='3' width='50' height='50' rx='13' fill='#17324d'/>"
        "<path d='M13 40V29M22 40V20M31 40V25M40 40V13' stroke='#72d5b7' stroke-width='4' stroke-linecap='round'/>"
        "<path d='M11 44h34M12 17l8-5 8 6 12-9' fill='none' stroke='#ff9d6e' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'/></svg>"
    )
    favicon = (
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E"
        "%3Crect width='64' height='64' rx='14' fill='%2317324d'/%3E"
        "%3Cpath d='M14 49V35M25 49V24M36 49V30M47 49V16' stroke='%2372d5b7' stroke-width='5' stroke-linecap='round'/%3E"
        "%3Cpath d='M10 53h44M12 20l10-7 10 8 16-12' fill='none' stroke='%23ff9d6e' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'/%3E"
        "%3C/svg%3E"
    )
    evidence_note = (
        "本套件使用独立认证租户，可用于隔离结论。"
        if independent
        else "未能证明所有运行都使用独立认证租户，隔离结论不可用于上线。"
    )
    admission_note = (
        "服务端观察模式：压测端准入已关闭，客户端不替服务端排队。"
        if manifest.get("server_observation_mode")
        else "客户端策略模式：报告中的准入等待属于压测端，不等同于 EchoMem 服务端队列。"
    )
    document = f"""<!doctype html>
<html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<link rel='icon' href='{favicon}'><title>EchoMem 多租户压测数据报告</title>
<style>
:root{{--bg:#f3f6f7;--paper:#fff;--ink:#17212b;--muted:#6d7b87;--line:#dce4e8;--green:#177b63;--amber:#9b6b16;--amber-bg:#fff7df;--red:#b6403b}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
.page{{max-width:1560px;margin:auto;padding:28px 18px 60px}}.top{{display:flex;align-items:center;gap:13px;margin-bottom:18px}}.logo{{width:52px;height:52px;flex:none}}
h1{{margin:0;font-size:25px;line-height:1.15}}h2{{font-size:18px;margin:0 0 12px}}h3{{font-size:14px;margin:18px 0 9px}}.muted,small{{color:var(--muted)}}
.hero,.section,.kpi{{background:var(--paper);border:1px solid var(--line);border-radius:8px}}.hero{{padding:18px 20px;border-left:5px solid var(--amber);display:flex;justify-content:space-between;gap:18px}}.hero.pass{{border-left-color:var(--green)}}.hero.fail{{border-left-color:var(--red)}}.hero strong{{font-size:22px}}.hero-meta{{text-align:right;color:var(--muted)}}
.kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:12px 0}}.kpi{{padding:14px 15px}}.label{{color:var(--muted);font-size:12px}}.value{{font-size:23px;font-weight:800;margin-top:3px}}.note{{font-size:12px;color:var(--muted)}}
.section{{padding:18px 19px;margin-top:12px}}.notice{{padding:11px 13px;background:var(--amber-bg);border-left:4px solid var(--amber);color:#6c5117;margin-bottom:12px}}
.scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap}}th,td{{padding:8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:#fafbfc;color:var(--muted);font-weight:700}}
.badge{{display:inline-block;padding:3px 8px;border-radius:999px;font-size:11px;font-weight:700;background:#eef1f3;color:var(--muted)}}.badge.pass{{background:#e8f6f0;color:var(--green)}}.badge.inconclusive{{background:var(--amber-bg);color:var(--amber)}}.badge.fail,.badge.environment_error{{background:#fff0ee;color:var(--red)}}
details{{border-top:1px solid var(--line);padding:11px 0}}details:first-of-type{{border-top:0}}summary{{cursor:pointer;font-weight:700}}.detail-intro{{margin-top:10px;color:var(--muted)}}.metric-grid{{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}}.metric-grid>div{{border:1px solid var(--line);padding:10px;border-radius:6px}}.metric-grid span,.metric-grid small{{display:block;color:var(--muted)}}.metric-grid b{{display:block;font-size:16px;margin:3px 0}}.links{{line-height:2}}a{{color:#286aa6}}code{{background:#eef2f4;padding:2px 5px;border-radius:4px;font-size:12px}}
.footer{{margin-top:14px;color:var(--muted);font-size:12px}}@media(max-width:900px){{.hero{{display:block}}.hero-meta{{text-align:left;margin-top:10px}}.kpis{{grid-template-columns:repeat(2,1fr)}}.metric-grid{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><main class='page'>
<header class='top'>{icon}<div><h1>EchoMem 多租户压测数据报告</h1><small>真实 HTTP / 真实模型 · 生成于 {esc(datetime.now().astimezone().isoformat())}</small></div></header>
<section class='hero {status_class}'><div><div class='label'>套件总判定</div><strong>{esc(overall)}</strong><div>{esc(evidence_note)}</div></div>
<div class='hero-meta'>目标 <code>{esc(manifest.get('base_url'))}</code><br>{len(runs)} 次运行 · {len(groups)} 组策略/场景</div></section>
<div class='kpis'>
<div class='kpi'><div class='label'>请求总数</div><div class='value'>{total_rows}</div><div class='note'>Commit + Search 原始请求</div></div>
<div class='kpi'><div class='label'>延迟事件</div><div class='value'>{delayed_total}</div><div class='note'>每条请求可展开查看</div></div>
<div class='kpi'><div class='label'>服务端时序覆盖</div><div class='value'>{server_count}/{server_total}</div><div class='note'>缺失不会用客户端值补齐</div></div>
<div class='kpi'><div class='label'>独立认证</div><div class='value'>{'是' if independent else '未确认'}</div><div class='note'>上线隔离结论的前提</div></div>
<div class='kpi'><div class='label'>重复轮次</div><div class='value'>{esc(manifest.get('repeats'))}</div><div class='note'>每组数据同时保留原始轮次</div></div>
</div>
<section class='section'><h2>数据口径</h2><div class='notice'><b>先看这里：</b>Commit 的端到端时间从请求进入到最终完成；Search 使用请求端到端时间。客户端排队、服务端排队、服务端执行分别统计。服务端字段没有覆盖时显示为 <b>-</b>，不能据此推断服务端没有排队。{esc(admission_note)}</div>
<p class='muted'>本报告展示均值、P50、P95、P99、最大值、完成率、延迟数、429 数和服务端证据覆盖率；策略只在相同场景、相同租户、相同负载和相同重复轮次之间比较。</p></section>
<section class='section'><h2>场景 × 策略完整数值</h2><div class='scroll'><table><thead><tr>
<th>场景</th><th>策略</th><th>轮次</th><th>状态</th><th>Commit 提交/完成</th><th>Commit 平均</th><th>Commit P50</th><th>Commit P95</th><th>Commit P99</th><th>Commit 最大</th>
<th>Search 提交/成功</th><th>Search 平均</th><th>Search P95</th><th>Search P99</th><th>Commit 客户端排队 P95</th><th>Search 客户端排队 P95</th><th>延迟事件</th><th>429</th><th>服务端时序覆盖</th></tr></thead>
<tbody>{rows or '<tr><td colspan=19>没有运行数据</td></tr>'}</tbody></table></div></section>
<section class='section'><h2>逐组详细数据</h2><p class='muted'>点击每一组展开：逐租户分位数、服务端排队/执行、延迟请求绝对时间、队列深度、状态和错误。</p>{detail or '<p>没有详细数据。</p>'}</section>
<div class='footer'>数据源：<code>{esc(manifest_path)}</code> · 原始请求 CSV 和每轮报告位于同一目录。报告不把 INCONCLUSIVE 当作性能通过。</div>
</main></body></html>"""
    output_path.write_text(document, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("suite_json", type=Path)
    parser.add_argument("output_html", type=Path)
    args = parser.parse_args()
    render(args.suite_json, args.output_html)
    print(args.output_html)


if __name__ == "__main__":
    main()
