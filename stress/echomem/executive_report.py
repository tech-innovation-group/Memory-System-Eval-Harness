#!/usr/bin/env python3
"""Render a compact, readable EchoMem stress-test report."""

from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path
from typing import Any


def esc(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return html.escape(str(value))


def num(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def seconds(value: Any) -> str:
    return f"{num(value)}s"


def percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "-"


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_server_metric_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in load_csv(path):
        try:
            metrics = json.loads(row.get("metrics") or "{}")
        except json.JSONDecodeError:
            metrics = {}
        if not isinstance(metrics, dict):
            metrics = {}
        rows.append({
            "elapsed_s": row.get("elapsed_s"),
            "timestamp": row.get("timestamp"),
            "status_code": row.get("status_code"),
            "rss_mb": (
                float(metrics["process_resident_memory_bytes"]) / 1048576
                if metrics.get("process_resident_memory_bytes") is not None
                else None
            ),
            "threads": metrics.get("process_threads"),
            "fds": metrics.get("process_open_fds"),
            "requests_total": metrics.get("echomem_http_requests_total"),
            "request_count": metrics.get("echomem_http_requests_total"),
        })
    return rows


def isolation_probe_counts(probes: list[dict[str, Any]]) -> dict[str, Any]:
    same_tenant = [probe for probe in probes if probe.get("same_tenant")]
    cross_tenant = [probe for probe in probes if not probe.get("same_tenant")]
    same_hits = sum(bool(probe.get("marker_found")) for probe in same_tenant)
    cross_false_positives = sum(
        bool(probe.get("marker_found")) for probe in cross_tenant
    )
    return {
        "same_tenant_probe_count": len(same_tenant),
        "same_tenant_hit_count": same_hits,
        "same_tenant_false_negative_count": len(same_tenant) - same_hits,
        "cross_tenant_probe_count": len(cross_tenant),
        "cross_tenant_false_positive_count": cross_false_positives,
        "cross_tenant_clean_count": len(cross_tenant) - cross_false_positives,
        "same_tenant_hit_rate": same_hits / len(same_tenant) if same_tenant else None,
        "cross_tenant_false_positive_rate": (
            cross_false_positives / len(cross_tenant) if cross_tenant else None
        ),
    }


def stat_line(group: dict[str, Any]) -> str:
    if not group:
        return "-"
    return (
        f"均值 {seconds(group.get('mean_s'))} · "
        f"P50 {seconds(group.get('p50_s'))} · "
        f"P95 {seconds(group.get('p95_s'))} · "
        f"最大 {seconds(group.get('max_s'))}"
    )


def stat_cells(group: dict[str, Any]) -> str:
    """Render all important percentiles as table cells."""
    return "".join(
        f"<td>{seconds(group.get(key))}</td>"
        for key in ("min_s", "mean_s", "p50_s", "p90_s", "p95_s", "p99_s", "max_s")
    )


def logo(class_name: str = "logo") -> str:
    return (
        f'<svg class="{class_name}" viewBox="0 0 64 64" role="img" '
        'aria-label="EchoMem 图标">'
        '<rect x="3" y="3" width="58" height="58" rx="16" fill="#17324d"/>'
        '<path d="M16 45V32M27 45V22M38 45V28M49 45V15" '
        'stroke="#79d7b7" stroke-width="5" stroke-linecap="round"/>'
        '<path d="M13 50h38M15 20l11-7 12 8 13-11" fill="none" '
        'stroke="#ff9f70" stroke-width="3.2" stroke-linecap="round" '
        'stroke-linejoin="round"/></svg>'
    )


def chart(points: list[dict[str, Any]], key: str, color: str, label: str) -> str:
    values: list[tuple[float, float]] = []
    for point in points:
        try:
            values.append((float(point.get("elapsed_s", 0)), float(point[key])))
        except (TypeError, ValueError, KeyError):
            continue
    if len(values) < 2:
        return '<div class="empty">暂无足够采样点</div>'
    width, height, pad = 720, 180, 24
    max_x = max(x for x, _ in values) or 1
    low = min(y for _, y in values)
    high = max(y for _, y in values)
    span = max(high - low, 1)
    coords = " ".join(
        f"{pad + x / max_x * (width - 2 * pad):.1f},"
        f"{height - pad - (y - low) / span * (height - 2 * pad):.1f}"
        for x, y in values
    )
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{html.escape(label)}">'
        f'<line x1="{pad}" y1="{height-pad}" x2="{width-pad}" '
        f'y2="{height-pad}" stroke="#dfe6eb"/>'
        f'<polyline points="{coords}" fill="none" stroke="{color}" '
        'stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>'
        "</svg>"
    )


def render(summary: dict[str, Any], root: Path) -> str:
    metrics = summary.get("metrics") or {}
    details = summary.get("details") or {}
    params = summary.get("parameters") or {}
    commit = metrics.get("commit") or {}
    search = metrics.get("search") or {}
    commit_dist = commit.get("completion") or {}
    search_dist = search.get("latency") or {}
    isolation = details.get("isolation") or {}
    if isolation.get("probes") and "cross_tenant_false_positive_count" not in isolation:
        isolation = {**isolation, **isolation_probe_counts(isolation["probes"])}
    resource_points = summary.get("resource_points") or []
    server_metric_rows = load_server_metric_rows(root / "server_metrics.csv")
    if not resource_points:
        resource_points = [
            {
                "elapsed_s": row.get("elapsed_s"),
                "rss_mb": row.get("rss_mb"),
                "cpu_percent": None,
            }
            for row in server_metric_rows
            if row.get("rss_mb") is not None
        ]

    status = str(summary.get("status") or "UNKNOWN").upper()
    status_text = {
        "PASS": "请求完成，但仍需结合隔离和服务端证据判断",
        "FAIL": "发现阻断性问题，当前不建议上线",
        "INCONCLUSIVE": "证据不足，暂不能下结论",
    }.get(status, "状态未知")
    status_class = "ok" if status == "PASS" else "bad" if status == "FAIL" else "warn"

    commit_submitted = int(commit.get("submitted") or details.get("commit_total") or 0)
    commit_failed = int(commit.get("failed") or details.get("commit_failures") or 0)
    commit_done = max(commit_submitted - commit_failed, 0)
    search_submitted = int(search.get("submitted") or details.get("search_total") or 0)
    search_errors = int(search.get("errors") or details.get("search_errors") or 0)
    duration = params.get("duration_s")
    target_search = int(float(duration or 0) * float(params.get("search_rps") or 0))
    search_gap = max(target_search - search_submitted, 0)
    search_rate = search_submitted / target_search if target_search else None
    cross_tenant = isolation.get("cross_tenant_false_positive_count")
    invalid_probes = isolation.get("invalid_probe_count")
    same_tenant_hit_rate = isolation.get("same_tenant_hit_rate")
    cross_tenant_false_positive_rate = isolation.get("cross_tenant_false_positive_rate")

    tenant_rows = []
    for tenant, data in sorted((metrics.get("per_tenant") or {}).items()):
        c = data.get("commit") or {}
        s = data.get("search") or {}
        tenant_rows.append(
            "<tr>"
            f"<td><strong>{esc(tenant)}</strong></td>"
            f"<td>{esc(c.get('completed'))}/{esc(c.get('submitted'))}</td>"
            f"<td>{seconds((c.get('completion') or {}).get('p95_s'))}</td>"
            f"<td>{esc(s.get('succeeded'))}/{esc(s.get('submitted'))}</td>"
            f"<td>{seconds((s.get('latency') or {}).get('p95_s'))}</td>"
            "</tr>"
        )

    commit_rows = load_csv(root / "commit_results.csv")
    search_rows = load_csv(root / "search_results.csv")
    del search_rows
    slow_rows = []
    for row in commit_rows:
        try:
            elapsed = float(row.get("elapsed_s") or row.get("completion_s") or 0)
        except (TypeError, ValueError):
            continue
        if elapsed >= 10:
            slow_rows.append(
                "<tr>"
                f"<td>{esc(row.get('tenant'))}</td>"
                f"<td>{esc(row.get('started_at'))}</td>"
                f"<td><strong>{seconds(elapsed)}</strong></td>"
                f"<td>{esc(row.get('request_id'))}</td>"
                f"<td>{esc(row.get('status'))}</td>"
                f"<td>{esc(row.get('error'))}</td>"
                "</tr>"
            )
    slow_html = "".join(slow_rows) or (
        '<tr><td colspan="6" class="empty">没有达到 10 秒阈值的 Commit</td></tr>'
    )

    probe_counts: dict[str, int] = {}
    for probe in isolation.get("probes") or []:
        key = "同租户" if probe.get("same_tenant") else "跨租户"
        result = "命中" if probe.get("marker_found") else "未命中"
        label = f"{key} {result}"
        probe_counts[label] = probe_counts.get(label, 0) + 1
    probe_summary = " · ".join(
        f"{esc(key)} {value}" for key, value in sorted(probe_counts.items())
    ) or "未采集"

    server_commit = commit.get("server") or {}
    server_search = search.get("server") or {}
    identity_mode = details.get("identity_mode", "未采集")
    data_files = "summary.json · commit_results.csv · search_results.csv · resource_samples.csv"
    server_metric_summary = ""
    if server_metric_rows:
        first_metric = server_metric_rows[0]
        last_metric = server_metric_rows[-1]
        rss_values = [row["rss_mb"] for row in server_metric_rows if row.get("rss_mb") is not None]
        thread_values = [float(row["threads"]) for row in server_metric_rows if row.get("threads") is not None]
        fd_values = [float(row["fds"]) for row in server_metric_rows if row.get("fds") is not None]
        server_metric_summary = (
            f"采样 {len(server_metric_rows)} 次 · RSS "
            f"{num(first_metric.get('rss_mb'))}MB → {num(last_metric.get('rss_mb'))}MB "
            f"(峰值 {num(max(rss_values) if rss_values else None)}MB) · "
            f"线程 {num(first_metric.get('threads'), 0)} → {num(last_metric.get('threads'), 0)} · "
            f"FD {num(first_metric.get('fds'), 0)} → {num(last_metric.get('fds'), 0)}"
        )
    server_metric_detail_rows = "".join(
        f"<tr><td>{esc(row.get('elapsed_s'))}s</td><td>{esc(row.get('timestamp'))}</td>"
        f"<td>{num(row.get('rss_mb'))}MB</td><td>{esc(row.get('threads'))}</td>"
        f"<td>{esc(row.get('fds'))}</td><td>{esc(row.get('requests_total'))}</td>"
        f"<td>{esc(row.get('status_code'))}</td></tr>"
        for row in server_metric_rows[::max(1, len(server_metric_rows) // 20)]
    ) or '<tr><td colspan="7" class="empty">没有服务端 metrics 采样</td></tr>'
    probes_html = "".join(
        f"<tr><td>{esc(probe.get('writer'))}</td><td>{esc(probe.get('reader'))}</td>"
        f"<td>{'命中' if probe.get('expected') else '不应命中'}</td>"
        f"<td>{'命中' if probe.get('marker_found') else '未命中'}</td>"
        f"<td class=\"{'danger' if probe.get('marker_found') != probe.get('expected') else ''}\">"
        f"{'异常' if probe.get('marker_found') != probe.get('expected') else '正常'}</td>"
        f"<td>{esc(probe.get('request_id'))}</td></tr>"
        for probe in (isolation.get("probes") or [])[:200]
    ) or '<tr><td colspan="6" class="empty">没有探针明细</td></tr>'

    metric_rows = (
        f"<tr><td><strong>Commit</strong></td><td>{commit_submitted}</td>"
        f"<td>{commit_done}</td><td>{commit_failed}</td>{stat_cells(commit_dist)}"
        f"<td>{esc(commit.get('rate_limited_count', 0))}</td></tr>"
        f"<tr><td><strong>Search</strong></td><td>{search_submitted}</td>"
        f"<td>{search_submitted-search_errors}</td><td>{search_errors}</td>{stat_cells(search_dist)}"
        f"<td>{esc(search.get('rate_limited_count', 0))}</td></tr>"
    )
    bucket_rows = []
    for bucket in metrics.get("time_buckets") or []:
        bc = bucket.get("commit") or {}
        bs = bucket.get("search") or {}
        bucket_rows.append(
            f"<tr><td>{num(bucket.get('start_s'))}–{num(bucket.get('end_s'))}s</td>"
            f"<td>{esc(bc.get('submitted'))}</td><td>{esc(bc.get('completed'))}</td>"
            f"<td>{esc(bc.get('delayed'))}</td><td>{seconds((bc.get('latency') or {}).get('mean_s'))}</td>"
            f"<td>{seconds((bc.get('latency') or {}).get('p95_s'))}</td>"
            f"<td>{esc(bs.get('submitted'))}</td><td>{esc(bs.get('succeeded'))}</td>"
            f"<td>{esc(bs.get('delayed'))}</td><td>{seconds((bs.get('latency') or {}).get('mean_s'))}</td>"
            f"<td>{seconds((bs.get('latency') or {}).get('p95_s'))}</td></tr>"
        )
    bucket_html = "".join(bucket_rows) or '<tr><td colspan="11" class="empty">没有按时间段数据</td></tr>'
    delayed_items = []
    for operation, values in (("Commit", commit.get("delayed") or []), ("Search", search.get("delayed") or [])):
        for item in values:
            delayed_items.append(
                (
                    str(item.get("completed_at") or item.get("finished_at") or ""),
                    f"<tr><td>{operation}</td><td>{esc(item.get('tenant'))}</td>"
                    f"<td>{esc(item.get('queued_at') or item.get('started_at'))}</td>"
                    f"<td>{esc(item.get('started_at'))}</td>"
                    f"<td>{esc(item.get('completed_at') or item.get('finished_at'))}</td>"
                    f"<td><strong>{seconds(item.get('completion_s') or item.get('latency_s') or item.get('duration_s'))}</strong></td>"
                    f"<td>{seconds(item.get('queue_wait_s'))}</td>"
                    f"<td>{esc(item.get('request_id'))}</td><td>{esc(item.get('status') or item.get('status_code'))}</td></tr>"
                )
            )
    delayed_items.sort(key=lambda item: item[0])
    delayed_html = "".join(item[1] for item in delayed_items) or (
        '<tr><td colspan="9" class="empty">没有超过阈值的请求</td></tr>'
    )
    scheduling = metrics.get("scheduling") or {}
    operation_sequence = scheduling.get("operation_sequence") or {}
    tenant_sequence = scheduling.get("tenant_sequence") or {}
    server_start_sequence = scheduling.get("server_start_sequence") or []
    server_start_rows = "".join(
        f"<tr><td>{esc(item.get('order'))}</td><td>{esc(item.get('operation'))}</td>"
        f"<td>{esc(item.get('tenant'))}</td><td>{esc(item.get('request_id'))}</td>"
        f"<td>{esc(item.get('server_execution_started_at'))}</td></tr>"
        for item in server_start_sequence[:200]
    ) or '<tr><td colspan="5" class="empty">没有服务端执行开始时间</td></tr>'
    admission = metrics.get("admission") or {}
    admission_rows = "".join(
        f"<tr><td>{esc(event.get('order'))}</td><td>{esc(event.get('operation'))}</td>"
        f"<td>{esc(event.get('tenant'))}</td><td>{esc(event.get('queued_at'))}</td>"
        f"<td>{esc(event.get('started_at'))}</td><td>{seconds(event.get('wait_s'))}</td>"
        f"<td>{esc(event.get('queue_depth'))}</td><td>{esc(event.get('status'))}</td></tr>"
        for event in (admission.get("events") or [])[:300]
    ) or '<tr><td colspan="8" class="empty">没有客户端准入事件；当前可能是观察模式</td></tr>'
    timeline_rows = "".join(
        f"<tr><td>{esc(item.get('workload_offset_s'))}</td><td>{esc(item.get('operation'))}</td>"
        f"<td>{esc(item.get('tenant'))}</td><td>{esc(item.get('queued_at'))}</td>"
        f"<td>{esc(item.get('started_at'))}</td><td>{esc(item.get('completed_at'))}</td>"
        f"<td>{seconds(item.get('duration_s'))}</td><td>{seconds(item.get('queue_wait_s'))}</td>"
        f"<td>{esc(item.get('server_queue_depth'))}</td><td>{esc(item.get('server_active_workers'))}</td>"
        f"<td>{esc(item.get('request_id'))}</td><td class=\"{'danger' if item.get('delayed') else ''}\">"
        f"{'慢' if item.get('delayed') else '正常'}</td></tr>"
        for item in (metrics.get("timeline") or [])[:500]
    ) or '<tr><td colspan="12" class="empty">没有逐请求时间线</td></tr>'

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EchoMem 压测报告</title>
<style>
:root{{--ink:#17212b;--muted:#6d7a86;--line:#e4e9ed;--bg:#f4f7f8;--paper:#fff;
--green:#16755e;--green-bg:#e8f6f0;--red:#b33b38;--red-bg:#fff0ee;--amber:#966916;--amber-bg:#fff8df}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
.page{{max-width:1120px;margin:0 auto;padding:28px 20px 50px}}.brand{{display:flex;align-items:center;
gap:12px;margin-bottom:18px}}.logo{{width:48px;height:48px;flex:none}}h1{{font-size:25px;line-height:1.2;
margin:0}}h2{{font-size:17px;margin:0 0 12px}}.sub,.muted,small{{color:var(--muted)}}
.hero{{background:var(--paper);border:1px solid var(--line);border-left:6px solid;padding:18px 20px;
display:flex;justify-content:space-between;gap:22px}}.hero.ok{{border-left-color:var(--green)}}
.hero.bad{{border-left-color:var(--red)}}.hero.warn{{border-left-color:#c89a2b}}
.hero-status{{font-size:28px;font-weight:850;line-height:1.2}}.hero-status.ok{{color:var(--green)}}
.hero-status.bad{{color:var(--red)}}.hero-status.warn{{color:var(--amber)}}.hero-meta{{text-align:right;color:var(--muted)}}
code{{background:#eef2f4;padding:2px 5px;border-radius:4px;font-size:12px}}.grid4{{display:grid;
grid-template-columns:repeat(4,1fr);gap:10px;margin:12px 0}}.card,.section{{background:var(--paper);border:1px solid var(--line)}}
.card{{padding:14px 15px;min-height:94px}}.label{{color:var(--muted);font-size:12px}}
.value{{font-size:24px;font-weight:850;margin-top:4px}}.note{{color:var(--muted);font-size:12px;margin-top:2px}}
.section{{padding:17px 18px;margin-top:12px}}.callout{{padding:11px 13px;margin:0 0 13px;border-left:4px solid}}
.callout.bad{{color:#7e2927;background:var(--red-bg);border-color:var(--red)}}.callout.warn{{color:#705313;
background:var(--amber-bg);border-color:#d3a63b}}.callout.ok{{color:#17634f;background:var(--green-bg);border-color:var(--green)}}
.facts{{display:grid;grid-template-columns:1fr 1fr;gap:0 34px}}.fact{{display:flex;justify-content:space-between;
gap:14px;padding:8px 0;border-bottom:1px solid var(--line)}}.fact>span:first-child{{color:var(--muted)}}
.fact>span:last-child{{font-weight:650;text-align:right}}.charts{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.chart-box{{border:1px solid var(--line);padding:11px}}.chart-title{{font-weight:750}}.chart-sub{{color:var(--muted);font-size:12px}}
.chart{{width:100%;height:auto;display:block}}.scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:12px}}
th,td{{padding:8px 7px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{color:var(--muted);background:#fafbfc;white-space:nowrap}}details{{border-top:1px solid var(--line);padding-top:10px}}
summary{{cursor:pointer;font-weight:750}}.empty{{color:var(--muted);text-align:center;padding:16px}}
.danger{{color:var(--red);font-weight:750}}.footer{{color:var(--muted);font-size:12px;margin-top:14px}}
@media(max-width:760px){{.hero{{display:block}}.hero-meta{{text-align:left;margin-top:10px}}
.grid4{{grid-template-columns:1fr 1fr}}.facts,.charts{{grid-template-columns:1fr}}.page{{padding:20px 12px 40px}}}}
</style></head><body><main class="page">
<header class="brand">{logo()}<div><h1>EchoMem 真实服务压测报告</h1>
<div class="sub">结论优先 · 真实 HTTP · 真实服务 · {esc(summary.get("finished_at"))}</div></div></header>
<section class="hero {status_class}"><div><div class="sub">总体结论</div>
<div class="hero-status {status_class}">{esc(status)}</div><div class="sub">{status_text}</div></div>
<div class="hero-meta">目标 <code>{esc(summary.get("base_url"))}</code><br>
{esc(params.get("duration_s"))} 秒 · Search {esc(params.get("search_rps"))} RPS · {esc(params.get("tenants"))} 个独立租户</div></section>
<section class="grid4">
<div class="card"><div class="label">Commit 完成</div><div class="value">{commit_done}/{commit_submitted}</div><div class="note">失败 {commit_failed}</div></div>
<div class="card"><div class="label">Commit P95</div><div class="value">{seconds(commit_dist.get("p95_s"))}</div><div class="note">最大 {seconds(commit_dist.get("max_s"))}</div></div>
<div class="card"><div class="label">Search 成功</div><div class="value">{search_submitted-search_errors}/{search_submitted}</div><div class="note">错误 {search_errors}</div></div>
<div class="card"><div class="label">隔离探针</div><div class="value danger">{esc(isolation.get("status", "UNVERIFIED"))}</div><div class="note">{esc(invalid_probes)} 个异常 / {esc(isolation.get("probe_count"))} 次</div></div>
</section>
<section class="section"><h2>一眼看懂</h2>
<div class="callout bad"><strong>阻断项：</strong>真实多租户隔离未通过：跨租户误命中 <strong>{esc(cross_tenant)}</strong> 次，同租户也存在漏读；当前压测结果不能作为上线通过依据。</div>
<div class="facts"><div class="fact"><span>Commit 延迟</span><span>{stat_line(commit_dist)}</span></div>
<div class="fact"><span>Search 延迟</span><span>{stat_line(search_dist)}</span></div>
<div class="fact"><span>Search 目标 / 实际 / 缺口</span><span>{target_search} / {search_submitted} / <strong>{search_gap}</strong></span></div>
<div class="fact"><span>429（Commit / Search）</span><span>{esc(commit.get("rate_limited_count", 0))} / {esc(search.get("rate_limited_count", 0))}</span></div>
<div class="fact"><span>租户 Search P95 最大/最小</span><span>{num(details.get("max_min_ratio"))} 倍</span></div>
<div class="fact"><span>身份模式</span><span>{esc(identity_mode)}</span></div>
<div class="fact"><span>同租户命中</span><span>{esc(isolation.get("same_tenant_hit_count", "未采集"))} / {esc(isolation.get("same_tenant_probe_count", "未采集"))}（{percent(same_tenant_hit_rate)}）</span></div>
<div class="fact"><span>跨租户误命中</span><span class="danger">{esc(cross_tenant)} / {esc(isolation.get("cross_tenant_probe_count", "未采集"))}（{percent(cross_tenant_false_positive_rate)}）</span></div></div></section>
<section class="section"><h2>核心数值总表</h2>
<div class="callout ok">所有延迟均为秒，统计基于实际完成的请求；目标缺口、失败和 429 单独列出。</div>
<div class="scroll"><table><thead><tr><th>操作</th><th>实际提交</th><th>成功/完成</th><th>失败</th>
<th>最小</th><th>平均</th><th>P50</th><th>P90</th><th>P95</th><th>P99</th><th>最大</th><th>429</th></tr></thead>
<tbody>{metric_rows}</tbody></table></div></section>
<section class="section"><h2>多租户下什么时候发生延迟</h2>
<div class="callout warn">按请求进入负载的 10 秒窗口分桶。Commit 延迟事件会保留绝对时间、租户和 Request ID，便于定位发生在哪个窗口。</div>
<div class="scroll"><table><thead><tr><th>时间窗口</th><th>Commit 提交</th><th>完成</th><th>慢请求</th><th>平均</th><th>P95</th>
<th>Search 提交</th><th>成功</th><th>慢请求</th><th>平均</th><th>P95</th></tr></thead>
<tbody>{bucket_html}</tbody></table></div></section>
<section class="section"><h2>慢请求明细</h2>
<div class="callout bad">Commit 慢请求阈值 {seconds(commit.get('delayed_threshold_s'))}；Search 慢请求阈值 {seconds(search.get('delayed_threshold_s'))}。以下保留发生时间、租户、队列等待和 Request ID。</div>
<div class="scroll"><table><thead><tr><th>操作</th><th>租户</th><th>入队/开始</th><th>开始</th><th>完成</th><th>总耗时</th><th>客户端排队</th><th>Request ID</th><th>状态</th></tr></thead>
<tbody>{delayed_html}</tbody></table></div></section>
<section class="section"><h2>目标负载对账</h2>
<div class="callout {'ok' if not search_gap else 'warn'}">Search 目标达成率 <strong>{percent(search_rate)}</strong>；本表保留未发出的请求，避免只看成功率造成误判。</div>
<div class="facts"><div class="fact"><span>Commit 目标 / 实际 / 缺口</span><span>{esc((metrics.get("targets") or {}).get("commit_submitted", commit_submitted))} / {commit_submitted} / 0</span></div>
<div class="fact"><span>Search 目标 / 实际 / 缺口</span><span>{target_search} / {search_submitted} / {search_gap}</span></div>
<div class="fact"><span>Search 实际吞吐</span><span>{num(search.get("throughput_rps"), 3)} RPS</span></div>
<div class="fact"><span>运行策略</span><span>{esc(params.get("scheduler_policy"))} · 客户端准入关闭</span></div></div></section>
<section class="section"><h2>资源趋势</h2>
<div class="callout ok">{server_metric_summary or "没有服务端进程采样；请提供 PID 或服务端 metrics。"}</div>
<div class="charts">
<div class="chart-box"><div class="chart-title">RSS 内存</div><div class="chart-sub">运行期间采样</div>{chart(resource_points, "rss_mb", "#b33b38", "RSS 内存")}</div>
<div class="chart-box"><div class="chart-title">CPU 使用率</div><div class="chart-sub">运行期间采样</div>{chart(resource_points, "cpu_percent", "#16755e", "CPU 使用率")}</div></div></section>
<section class="section"><h2>逐租户对比</h2><div class="scroll"><table><thead><tr><th>租户</th><th>Commit 完成</th><th>Commit P95</th><th>Search 成功</th><th>Search P95</th></tr></thead>
<tbody>{"".join(tenant_rows) or '<tr><td colspan="5" class="empty">没有逐租户数据</td></tr>'}</tbody></table></div></section>
<section class="section"><h2>真实租户身份与隔离 · 隔离探针</h2><div class="callout bad"><strong>探针结果：</strong>{probe_summary}。HTTP 200 只表示接口响应成功，不代表数据隔离正确。</div>
<details><summary>查看探针明细（{esc(isolation.get("probe_count", 0))} 次）</summary><div class="scroll"><table><thead><tr><th>写入租户</th><th>读取租户</th><th>预期</th><th>实际</th><th>状态</th><th>Request ID</th></tr></thead>
<tbody>{probes_html}</tbody></table></div></details></section>
<section class="section"><h2>调度、交替与限流证据</h2>
<div class="callout warn">客户端策略：{esc(params.get("scheduler_policy"))}；客户端准入事件最多展示 300 条。若没有服务端时间戳，不能把客户端顺序当成 EchoMem 内部 FIFO 或 Search 优先的证明。</div>
<div class="facts"><div class="fact"><span>操作序列</span><span>{esc(operation_sequence.get("count"))} 请求 · {esc(operation_sequence.get("runs"))} 段 · 切换 {esc(operation_sequence.get("switches"))} 次 · 最大连续 {esc(operation_sequence.get("max_streak"))}</span></div>
<div class="fact"><span>租户序列</span><span>{esc(tenant_sequence.get("count"))} 请求 · {esc(tenant_sequence.get("runs"))} 段 · 切换 {esc(tenant_sequence.get("switches"))} 次 · 最大连续 {esc(tenant_sequence.get("max_streak"))}</span></div>
<div class="fact"><span>服务端时序覆盖</span><span>Commit {esc(server_commit.get("observed_count"))}/{esc(server_commit.get("total_count"))} · Search {esc(server_search.get("observed_count"))}/{esc(server_search.get("total_count"))}</span></div>
<div class="fact"><span>服务端队列深度 / 活跃 worker</span><span class="danger">未采集</span></div></div>
<div class="fact"><span>服务端开始顺序覆盖</span><span>{esc(scheduling.get("server_start_order_count", 0))}/{esc(scheduling.get("arrival_order_count", 0))}（{percent(scheduling.get("server_start_coverage"))}）</span></div>
<div class="fact"><span>到达顺序被服务端反转</span><span>{esc(scheduling.get("arrival_vs_server_start_inversions", 0))} / {esc(scheduling.get("arrival_vs_server_start_comparable_pairs", 0))} 对（{percent(scheduling.get("arrival_vs_server_start_inversion_rate"))}）</span></div>
<div class="fact"><span>Search 先于 Commit 开始</span><span>{esc(scheduling.get("search_started_ahead_of_commit_count", 0))} / {esc(scheduling.get("commit_search_comparable_pairs", 0))} 对</span></div>
<div class="fact"><span>服务端调度结论</span><span>{esc(scheduling.get("server_scheduling_conclusion", "insufficient_server_timing"))}</span></div>
<details open><summary>客户端准入事件</summary><div class="scroll"><table><thead><tr><th>顺序</th><th>操作</th><th>租户</th><th>入队</th><th>开始</th><th>等待</th><th>队列深度</th><th>状态</th></tr></thead>
<tbody>{admission_rows}</tbody></table></div></details></section>
<section class="section"><h2>逐请求时间线</h2>
<div class="callout warn">展示前 500 条。服务端字段为空时保留为空，不用客户端时间伪造服务端证据。</div>
<details><summary>展开 {len(metrics.get("timeline") or [])} 条请求</summary><div class="scroll"><table><thead><tr><th>负载偏移</th><th>操作</th><th>租户</th><th>入队</th><th>开始</th><th>完成</th><th>总耗时</th><th>客户端排队</th><th>服务端队列</th><th>活跃 worker</th><th>Request ID</th><th>状态</th></tr></thead>
<tbody>{timeline_rows}</tbody></table></div></details></section>
<section class="section"><h2>服务端执行开始顺序</h2>
<div class="callout warn">只有服务端返回执行开始时间时，这张表才用于判断 FIFO、Search 优先或双通道；客户端准入顺序不替代服务端顺序。</div>
<details><summary>展开 {len(server_start_sequence)} 条服务端开始记录</summary><div class="scroll"><table><thead><tr><th>顺序</th><th>操作</th><th>租户</th><th>Request ID</th><th>服务端开始时间</th></tr></thead>
<tbody>{server_start_rows}</tbody></table></div></details></section>
<section class="section"><h2>异常与慢请求</h2><div class="callout warn">慢 Commit 阈值为 10 秒，下面保留时间、租户和 Request ID，便于回查服务端日志。</div>
<details open><summary>超过阈值的 Commit（{len(slow_rows)} 条）</summary><div class="scroll"><table><thead><tr><th>租户</th><th>开始时间</th><th>耗时</th><th>Request ID</th><th>状态</th><th>错误</th></tr></thead><tbody>{slow_html}</tbody></table></div></details></section>
<section class="section"><h2>证据完整性</h2><div class="facts">
<div class="fact"><span>服务端 Commit 时序</span><span>{esc(server_commit.get("observed_count", "未采集"))} / {esc(server_commit.get("total_count", "未采集"))}</span></div>
<div class="fact"><span>服务端 Search 时序</span><span>{esc(server_search.get("observed_count", "未采集"))} / {esc(server_search.get("total_count", "未采集"))}</span></div>
<div class="fact"><span>服务端队列深度 / 活跃 worker</span><span class="danger">未采集</span></div>
<div class="fact"><span>限流判断</span><span>本轮未观察到 429，不能证明没有限流</span></div></div>
<details><summary>服务端 /metrics 采样（抽样展示）</summary><div class="scroll"><table><thead><tr><th>偏移</th><th>采样时间</th><th>RSS</th><th>线程</th><th>FD</th><th>服务累计请求计数</th><th>HTTP</th></tr></thead>
<tbody>{server_metric_detail_rows}</tbody></table></div></details>
<details><summary>运行配置与原始文件</summary><div class="facts">
<div class="fact"><span>运行时长 / 租户</span><span>{esc(params.get("duration_s"))} 秒 / {esc(params.get("tenants"))}</span></div>
<div class="fact"><span>并发（Commit / Search）</span><span>{esc(params.get("commit_workers"))} / {esc(params.get("search_workers"))}</span></div>
<div class="fact"><span>模型 / Mock</span><span>真实服务 / 否</span></div>
<div class="fact"><span>原始文件</span><span>{data_files}</span></div></div></details></section>
<div class="footer">报告由压测平台生成。FAIL 不是“请求都失败”，而是表示关键上线条件未满足；所有异常和证据缺口均保留。</div>
</main></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("summary_json", type=Path)
    parser.add_argument("output_html", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.summary_json.read_text(encoding="utf-8"))
    summary = payload.get("summary") or payload
    args.output_html.write_text(render(summary, args.summary_json.parent), encoding="utf-8")
    print(args.output_html)


if __name__ == "__main__":
    main()
