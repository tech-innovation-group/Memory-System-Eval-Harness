#!/usr/bin/env python3
"""Render a compact, readable HTML report from an EchoMem stress status.json."""

from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path
from typing import Any


def esc(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    return html.escape(str(value))


def seconds(value: Any) -> str:
    try:
        return f"{float(value):.2f}s"
    except (TypeError, ValueError):
        return "-"


def number(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def percent(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "-"


def distribution(group: dict[str, Any]) -> str:
    """Show the complete latency distribution in a compact, readable form."""
    return (
        f"均值 {seconds(group.get('mean_s'))} · "
        f"P50 {seconds(group.get('p50_s'))} · "
        f"P90 {seconds(group.get('p90_s'))} · "
        f"P95 {seconds(group.get('p95_s'))} · "
        f"P99 {seconds(group.get('p99_s'))} · "
        f"最大 {seconds(group.get('max_s'))}"
    )


def status_class(value: Any) -> str:
    return str(value or "UNKNOWN").lower().replace(" ", "_")


def make_chart(points: list[dict[str, Any]], metric: str, color: str, label: str) -> str:
    values = [
        (float(point.get("elapsed_s", 0)), float(point[metric]))
        for point in points
        if point.get(metric) is not None
    ]
    if len(values) < 2:
        return "<div class='empty'>采样点不足，无法绘制趋势</div>"
    width, height, pad = 760, 220, 34
    max_x = max(x for x, _ in values) or 1
    low = min(y for _, y in values)
    high = max(y for _, y in values)
    span = max(high - low, 1.0)
    coords = " ".join(
        f"{pad + x / max_x * (width - 2 * pad):.1f},"
        f"{height - pad - (y - low) / span * (height - 2 * pad):.1f}"
        for x, y in values
    )
    return (
        f"<svg class='chart' viewBox='0 0 {width} {height}' role='img' "
        f"aria-label='{html.escape(label)}'>"
        f"<line x1='{pad}' y1='{height-pad}' x2='{width-pad}' y2='{height-pad}' "
        "class='axis'/>"
        f"<polyline points='{coords}' fill='none' stroke='{color}' "
        "stroke-width='4' stroke-linecap='round' stroke-linejoin='round'/>"
        "</svg>"
    )


def render(summary: dict[str, Any]) -> str:
    details = summary.get("details") or {}
    metrics = summary.get("metrics") or {}
    commit = metrics.get("commit") or {}
    search = metrics.get("search") or {}
    completion = commit.get("completion") or {}
    commit_queue = commit.get("queue_wait") or {}
    commit_server = commit.get("server") or {}
    latency = search.get("latency") or {}
    search_server = search.get("server") or {}
    params = summary.get("parameters") or {}
    resource_points = summary.get("resource_points") or []
    tenants = metrics.get("per_tenant") or {}
    admission = metrics.get("admission") or {}
    isolation = details.get("isolation") or {}
    fairness = metrics.get("fairness") or {}
    scheduling = metrics.get("scheduling") or {}
    targets = metrics.get("targets") or {}
    request_rows = summary.get("_request_rows") or []
    isolation_probes = isolation.get("probes") or []
    identity_observations = isolation.get("identity_observations") or {}
    delayed_commits = commit.get("delayed") or []
    delayed_searches = search.get("delayed") or []
    time_buckets = metrics.get("time_buckets") or []
    admission_events = admission.get("events") or []
    status = str(summary.get("status") or "UNKNOWN").upper()
    commit_total = int(details.get("commit_total") or commit.get("submitted") or 0)
    commit_failures = int(details.get("commit_failures") or commit.get("failed") or 0)
    commit_done = commit_total - commit_failures
    search_total = int(details.get("search_total") or search.get("submitted") or 0)
    search_errors = int(details.get("search_errors") or search.get("errors") or 0)
    configured_duration_s = params.get("duration_s") or metrics.get("arrival_window_s") or metrics.get("workload_duration_s")
    configured_search_rps = params.get("search_rps")
    try:
        configured_search_rps = float(configured_search_rps or 0)
    except (TypeError, ValueError):
        configured_search_rps = 0.0
    try:
        configured_duration_s = float(configured_duration_s or 0)
    except (TypeError, ValueError):
        configured_duration_s = 0.0
    configured_search_target = (
        math.ceil(configured_duration_s * configured_search_rps)
        if configured_duration_s > 0 and configured_search_rps > 0
        else 0
    )
    observed_search_rate = (
        search_total / configured_duration_s
        if configured_duration_s > 0
        else None
    )
    status_text = {
        "PASS": "完成，核心请求均成功",
        "FAIL": "失败，需要查看异常",
        "ENVIRONMENT_ERROR": "环境异常，结果不可用",
        "INCONCLUSIVE": "证据不足，不能下结论",
    }.get(status, "状态未知")

    operation_sequence = scheduling.get("operation_sequence") or {}
    tenant_sequence = scheduling.get("tenant_sequence") or {}
    identity_rows = "".join(
        f"<tr><td><b>{esc(tenant)}</b></td>"
        f"<td>{esc(json.dumps(observation, ensure_ascii=False, sort_keys=True))}</td></tr>"
        for tenant, observation in sorted(identity_observations.items())
    )

    commit_status_counts = commit.get("http_status_counts") or {}
    search_status_counts = search.get("http_status_counts") or {}
    commit_rate_limited = int(commit.get("rate_limited_count") or 0)
    search_rate_limited = int(search.get("rate_limited_count") or 0)
    rate_limit_note = (
        "服务端返回过 HTTP 429，报告已记录限流次数和 Retry-After。"
        if commit_rate_limited or search_rate_limited
        else "本轮未观察到 HTTP 429；这只能说明本轮没有返回显式限流响应，不能证明服务端没有内部排队。"
    )
    tenant_note = (
        "本次使用独立认证身份，并执行了交叉搜索探针。"
        if details.get("identity_mode") == "independent_auth_keys"
        else "当前租户共用一个 X-Auth-Key，只能观察标签级公平性，不能证明真实租户隔离。"
    )

    scenario_labels = {
        "commit_delivery": ("Commit 最终完成", "已受理的 Commit 是否最终完成"),
        "search_priority": ("Search 与 Commit 并发", "并发期间 Search 是否成功且延迟达标"),
        "tenant_fairness": ("租户公平性", "不同租户的延迟是否明显失衡"),
        "tenant_isolation": ("租户隔离探针", "同租户可读，跨租户不可读"),
        "resource_observation": ("资源观测", "内存、CPU、线程、文件描述符趋势"),
        "server_scheduling_observation": (
            "服务端调度证据",
            "每条请求是否具备服务端队列和执行时序",
        ),
        "environment": ("运行环境", "服务启动、网络和鉴权是否正常"),
    }
    scenario_html = []
    for key, value in (summary.get("scenario_status") or {}).items():
        label, note = scenario_labels.get(key, (key, "测试项结果"))
        css = status_class(value)
        symbol = "✓" if str(value) == "PASS" else "!" if css in {"fail", "environment_error"} else "?"
        scenario_html.append(
            f"<div class='scenario'><span class='scenario-mark {css}'>{symbol}</span>"
            f"<div><b>{esc(label)}</b><small>{esc(note)}</small></div>"
            f"<span class='badge {css}'>{esc(value)}</span></div>"
        )

    tenant_rows = []
    for tenant, data in sorted(tenants.items()):
        c = data.get("commit") or {}
        s = data.get("search") or {}
        cc = c.get("completion") or {}
        sq = s.get("latency") or {}
        c_status = c.get("http_status_counts") or {}
        s_status = s.get("http_status_counts") or {}
        tenant_rows.append(
            "<tr>"
            f"<td><b>{esc(tenant)}</b></td>"
            f"<td>{esc(c.get('submitted'))} / {esc(c.get('completed'))}</td>"
            f"<td>{esc(distribution(cc))}</td>"
            f"<td>{esc(s.get('submitted', 0))} / {esc(s.get('succeeded', 0))}</td>"
            f"<td>{esc(distribution(sq))}</td>"
            f"<td>{esc(distribution(c.get('queue_wait') or {}))}</td>"
            f"<td>{esc(c.get('delayed_count', 0))} / {esc(s.get('delayed_count', 0))}</td>"
            f"<td>{esc(c.get('rate_limited_count', 0))} / {esc(s.get('rate_limited_count', 0))}</td>"
            f"<td>{esc(c_status)} / {esc(s_status)}</td>"
            "</tr>"
        )

    delayed_rows = []
    for item in delayed_commits:
        delayed_rows.append(
            "<tr>"
            "<td><span class='type commit'>Commit</span></td>"
            f"<td>{esc(item.get('tenant'))}</td>"
            f"<td>{esc(item.get('started_at'))}</td>"
            f"<td>{esc(item.get('completed_at'))}</td>"
            f"<td>{seconds(item.get('completion_s'))}</td>"
            f"<td>{seconds(item.get('queue_wait_s'))}</td>"
            f"<td>{seconds(item.get('admission_wait_s'))}</td>"
            f"<td>{esc(item.get('status'))}<small>HTTP {esc(item.get('status_code'))} · "
            f"Retry-After {seconds(item.get('retry_after_s'))}</small></td>"
            "</tr>"
        )
    for item in delayed_searches:
        delayed_rows.append(
            "<tr>"
            "<td><span class='type search'>Search</span></td>"
            f"<td>{esc(item.get('tenant'))}</td>"
            f"<td>{esc(item.get('started_at'))}</td>"
            f"<td>{esc(item.get('finished_at'))}</td>"
            f"<td>{seconds(item.get('latency_s'))}</td>"
            "<td>-</td>"
            f"<td>{seconds(item.get('admission_wait_s'))}</td>"
            f"<td>{esc(item.get('status_code') or item.get('error'))}<small>"
            f"Retry-After {seconds(item.get('retry_after_s'))}</small></td>"
            "</tr>"
        )

    timeline_rows = "".join(
        "<tr>"
        f"<td>{esc(item.get('operation'))}</td>"
        f"<td>{esc(item.get('tenant'))}</td>"
        f"<td>{esc(item.get('session_id'))}</td>"
        f"<td>{esc(item.get('scheduled_at'))}</td>"
        f"<td>{esc(item.get('queued_at') or item.get('started_at'))}</td>"
        f"<td>{esc(item.get('finished_at') or item.get('completed_at'))}</td>"
        f"<td>{seconds(item.get('queue_wait_s'))}</td>"
        f"<td>{seconds(item.get('service_s') or item.get('elapsed_s'))}</td>"
        f"<td>{seconds(item.get('end_to_end_s'))}</td>"
        f"<td>{esc(item.get('admission_queue_depth') or item.get('queue_depth_at_enqueue', 0))}</td>"
        f"<td>{esc(item.get('status') or item.get('status_code'))}</td>"
        "</tr>"
        for item in request_rows[:500]
    )
    bucket_rows = "".join(
        "<tr>"
        f"<td>{esc(bucket.get('bucket', 0) + 1)}</td>"
        f"<td>{seconds(bucket.get('start_s'))} - {seconds(bucket.get('end_s'))}</td>"
        f"<td>{esc(bucket.get('requests', 0))}</td>"
        f"<td>{esc((bucket.get('commit') or {}).get('submitted', 0))} / "
        f"{esc((bucket.get('commit') or {}).get('completed', 0))}</td>"
        f"<td>{esc((bucket.get('commit') or {}).get('delayed', 0))}</td>"
        f"<td>{esc((bucket.get('search') or {}).get('submitted', 0))} / "
        f"{esc((bucket.get('search') or {}).get('succeeded', 0))}</td>"
        f"<td>{esc((bucket.get('search') or {}).get('delayed', 0))}</td>"
        f"<td>{esc(', '.join(bucket.get('tenants') or []))}</td>"
        "</tr>"
        for bucket in time_buckets
    )
    admission_rows = "".join(
        "<tr>"
        f"<td>{esc(event.get('order'))}</td><td>{esc(event.get('operation'))}</td>"
        f"<td>{esc(event.get('tenant'))}</td><td>{esc(event.get('queued_at'))}</td>"
        f"<td>{esc(event.get('started_at'))}</td><td>{seconds(event.get('wait_s'))}</td>"
        f"<td>{esc(event.get('queue_depth'))}</td><td>{esc(event.get('status'))}</td>"
        "</tr>"
        for event in admission_events[:500]
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EchoMem 压测报告</title>
<style>
:root{{--ink:#17212b;--muted:#6a7785;--line:#e6ebef;--paper:#fff;--bg:#f5f7f8;
--green:#177b63;--green-bg:#e8f6f0;--red:#b6403b;--red-bg:#fff0ee;--amber:#9b6b16;
--amber-bg:#fff7df;--blue:#286aa6;--blue-bg:#edf5ff}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
.page{{max-width:1180px;margin:auto;padding:28px 20px 56px}} .top{{display:flex;align-items:center;
gap:14px;margin-bottom:20px}} .logo{{width:48px;height:48px;flex:none}} h1{{font-size:25px;margin:0;
letter-spacing:0}} h2{{font-size:17px;margin:0 0 14px}} h3{{font-size:14px;margin:0 0 8px}}
.muted,small{{color:var(--muted)}} .top small{{display:block;margin-top:3px}}
.hero{{display:flex;justify-content:space-between;align-items:center;gap:20px;background:var(--paper);
border:1px solid var(--line);border-left:5px solid var(--green);padding:20px 22px;margin-bottom:14px}}
.hero.environment_error,.hero.fail{{border-left-color:var(--red)}} .hero.inconclusive{{border-left-color:var(--amber)}}
.hero-label{{font-size:12px;color:var(--muted)}} .hero-status{{font-size:28px;font-weight:850;line-height:1.1}}
.hero-status.pass{{color:var(--green)}} .hero-status.fail,.hero-status.environment_error{{color:var(--red)}}
.hero-status.inconclusive{{color:var(--amber)}} .hero-meta{{text-align:right;color:var(--muted);font-size:13px}}
.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px}}
.card,.section{{background:var(--paper);border:1px solid var(--line)}} .card{{padding:15px 16px}}
.card-label{{color:var(--muted);font-size:12px}} .card-value{{font-size:24px;font-weight:800;margin-top:4px}}
.card-note{{font-size:12px;color:var(--muted);margin-top:3px}} .section{{padding:19px 20px;margin-top:14px}}
.section-head{{display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:12px}}
.scenario{{display:flex;align-items:center;gap:11px;padding:11px 0;border-top:1px solid var(--line)}}
.scenario:first-of-type{{border-top:0}} .scenario-mark{{width:27px;height:27px;display:grid;place-items:center;
border-radius:50%;font-weight:800;background:#eef1f3;color:var(--muted);flex:none}}
.scenario-mark.pass{{background:var(--green-bg);color:var(--green)}} .scenario-mark.fail,.scenario-mark.environment_error{{background:var(--red-bg);color:var(--red)}}
.scenario-mark.inconclusive{{background:var(--amber-bg);color:var(--amber)}} .scenario small{{display:block;margin-top:1px}}
.badge{{margin-left:auto;padding:3px 8px;font-size:11px;font-weight:700;border-radius:999px;background:#eef1f3;color:var(--muted)}}
.badge.pass{{background:var(--green-bg);color:var(--green)}} .badge.fail,.badge.environment_error{{background:var(--red-bg);color:var(--red)}}
.badge.inconclusive{{background:var(--amber-bg);color:var(--amber)}} .facts{{display:grid;grid-template-columns:1fr 1fr;
column-gap:38px}} .fact{{display:flex;justify-content:space-between;gap:18px;padding:8px 0;border-bottom:1px solid var(--line)}}
.fact span:first-child{{color:var(--muted)}} .fact span:last-child{{font-weight:650;text-align:right;overflow-wrap:anywhere}}
.notice{{padding:12px 14px;border-left:4px solid var(--amber);background:var(--amber-bg);color:#6d5116;margin-bottom:12px}}
.charts{{display:grid;grid-template-columns:1fr 1fr;gap:12px}} .chart-box{{border:1px solid var(--line);padding:13px}}
.chart-title{{font-weight:750}} .chart-subtitle{{font-size:12px;color:var(--muted);margin:2px 0 5px}} .chart{{width:100%;height:auto}}
.axis{{stroke:#d9e0e5;stroke-width:1}} table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:9px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}} th{{font-weight:700;color:var(--muted);background:#fafbfc}}
.scroll{{overflow:auto}} .type{{font-weight:700}} .type.commit{{color:var(--red)}} .type.search{{color:var(--blue)}}
details summary{{cursor:pointer;font-weight:750;color:#344454}} details summary::marker{{color:var(--green)}}
details{{border-top:1px solid var(--line);padding-top:12px}} details+details{{margin-top:12px}}
.footer{{font-size:12px;color:var(--muted);margin-top:15px}} code{{font-size:12px;background:#eef2f4;padding:2px 5px}}
.empty{{color:var(--muted);padding:25px 0;text-align:center}}
@media(max-width:760px){{.hero{{display:block}}.hero-meta{{text-align:left;margin-top:12px}}.cards{{grid-template-columns:1fr 1fr}}
.charts,.facts{{grid-template-columns:1fr}}.page{{padding:20px 12px 42px}}}}
</style>
</head>
<body><main class="page">
<header class="top">
<svg class="logo" viewBox="0 0 52 52" role="img" aria-label="EchoMem">
<path d="M26 3 47 14.5v23L26 49 5 37.5v-23z" fill="#e8f6f0" stroke="#177b63" stroke-width="2.5"/>
<path d="m12 18 14 8 14-8M26 26v15M18 22.5v9l8 4.5 8-4.5v-9" fill="none" stroke="#177b63" stroke-width="2.5" stroke-linejoin="round"/>
<circle cx="26" cy="13" r="3" fill="#177b63"/>
</svg>
<div><h1>EchoMem 真实服务压测报告</h1>
<small>PR397 压测方案 · {esc(summary.get("finished_at"))}</small></div>
</header>
<section class="hero {status_class(status)}">
<div><div class="hero-label">总体判定</div><div class="hero-status {status_class(status)}">{esc(status)}</div>
<div class="muted">{esc(status_text)}</div></div>
<div class="hero-meta">目标 <code>{esc(summary.get("base_url"))}</code><br>
{esc(params.get("duration_s"))} 秒 · {esc(params.get("search_rps"))} RPS · {esc(params.get("tenants"))} 个租户</div>
</section>
<section class="cards">
<div class="card"><div class="card-label">Commit 完成</div><div class="card-value">{commit_done}/{commit_total}</div><div class="card-note">失败 {commit_failures} 次</div></div>
<div class="card"><div class="card-label">Commit P95</div><div class="card-value">{seconds(completion.get("p95_s"))}</div><div class="card-note">最大 {seconds(completion.get("max_s"))}</div></div>
<div class="card"><div class="card-label">Search 成功</div><div class="card-value">{search_total-search_errors}/{search_total}</div><div class="card-note">错误 {search_errors} 次</div></div>
<div class="card"><div class="card-label">Search P95</div><div class="card-value">{seconds(latency.get("p95_s"))}</div><div class="card-note">配置 {number(configured_search_rps, 3)} RPS · 实际 {number(observed_search_rate, 3)} RPS</div></div>
</section>
<section class="section"><div class="section-head"><h2>一眼看懂</h2><small>先看结论，再展开证据</small></div>
{''.join(scenario_html) or '<div class="empty">没有场景结果</div>'}</section>
<section class="section"><h2>租户隔离证据</h2><div class="notice">{esc(tenant_note)}</div>
<div class="facts"><div class="fact"><span>隔离探针状态</span><span>{esc(isolation.get("status"))}</span></div>
<div class="fact"><span>服务端身份映射</span><span>{esc(isolation.get("identity_mapping_status", "UNVERIFIED"))}</span></div>
<div class="fact"><span>隔离探针覆盖</span><span>{esc(isolation.get("probe_count", 0))} / {esc(isolation.get("expected_probe_count", 0))}</span></div>
<div class="fact"><span>隔离异常数</span><span>{esc(isolation.get("invalid_probe_count", 0))}</span></div>
<div class="fact"><span>同租户命中</span><span>{esc(sum(1 for p in isolation_probes if p.get("same_tenant") and p.get("marker_found")))} / {esc(sum(1 for p in isolation_probes if p.get("same_tenant")))}</span></div>
<div class="fact"><span>跨租户误命中</span><span>{esc(sum(1 for p in isolation_probes if not p.get("same_tenant") and p.get("marker_found")))}</span></div></div>
<details><summary>展开全部隔离探针</summary><div class="scroll"><table><thead><tr><th>写入租户</th><th>读取租户</th><th>期望命中</th><th>实际命中</th><th>HTTP</th><th>耗时</th><th>结果</th></tr></thead>
<tbody>{''.join(f"<tr><td>{esc(p.get('writer'))}</td><td>{esc(p.get('reader'))}</td><td>{'是' if p.get('expected') else '否'}</td><td>{'是' if p.get('marker_found') else '否'}</td><td>{esc(p.get('status_code'))}</td><td>{seconds(p.get('latency_s'))}</td><td>{'PASS' if p.get('marker_found') == p.get('expected') else 'FAIL'}</td></tr>" for p in isolation_probes) or '<tr><td colspan="7">没有隔离探针数据</td></tr>'}</tbody></table></div></details></section>
<section class="section"><h2>服务端身份映射</h2>
<div class="notice">只有服务端返回的稳定身份字段才能证明 API Key 确实映射到不同租户。Session ID、Request ID 等运行时 ID 不计入隔离证明。</div>
<div class="facts"><div class="fact"><span>身份映射判定</span><span>{esc(isolation.get("identity_mapping_status", "UNVERIFIED"))}</span></div>
<div class="fact"><span>观测租户数</span><span>{esc(len(identity_observations))} / {esc(params.get("tenants", len(identity_observations)))}</span></div></div>
<div class="scroll"><table><thead><tr><th>压测租户</th><th>服务端稳定身份字段</th></tr></thead>
<tbody>{identity_rows or '<tr><td colspan="2">服务端未返回 tenant/account/workspace/user/organization 等稳定身份字段</td></tr>'}</tbody></table></div></section>
<section class="section"><h2>调度交替与限流数值</h2>
<div class="notice">这里的“交替”按请求到达/客户端准入顺序统计。若客户端准入已关闭，它只能说明发送流顺序，不能替代 EchoMem 内部调度证据。</div>
<div class="facts">
<div class="fact"><span>操作序列</span><span>{esc(operation_sequence.get("count", 0))} 请求 · {esc(operation_sequence.get("runs", 0))} 段 · 切换 {esc(operation_sequence.get("switches", 0))} 次</span></div>
<div class="fact"><span>连续同类操作最大段长</span><span>{esc(operation_sequence.get("max_streak", 0))}</span></div>
<div class="fact"><span>首个 / 最后操作</span><span>{esc(operation_sequence.get("first"))} / {esc(operation_sequence.get("last"))}</span></div>
<div class="fact"><span>租户序列</span><span>{esc(tenant_sequence.get("count", 0))} 请求 · {esc(tenant_sequence.get("runs", 0))} 段 · 切换 {esc(tenant_sequence.get("switches", 0))} 次</span></div>
<div class="fact"><span>连续同租户最大段长</span><span>{esc(tenant_sequence.get("max_streak", 0))}</span></div>
<div class="fact"><span>Commit 429 / Retry-After</span><span>{commit_rate_limited} / {seconds((commit.get("retry_after") or {}).get("mean_s"))} 均值</span></div>
<div class="fact"><span>Search 429 / Retry-After</span><span>{search_rate_limited} / {seconds((search.get("retry_after") or {}).get("mean_s"))} 均值</span></div>
<div class="fact"><span>证据口径</span><span>{esc(scheduling.get("interpretation", "未记录"))}</span></div>
</div></section>
<section class="section"><h2>公平性指标</h2><div class="facts">
<div class="fact"><span>Search P95 最大 / 最小</span><span>{number(fairness.get("search_latency_p95_max_min_ratio"))} 倍</span></div>
<div class="fact"><span>Commit P95 最大 / 最小</span><span>{number(fairness.get("commit_completion_p95_max_min_ratio"))} 倍</span></div>
<div class="fact"><span>Search P95 Jain 指数</span><span>{number(fairness.get("search_latency_p95_jain"), 4)}</span></div>
<div class="fact"><span>Commit P95 Jain 指数</span><span>{number(fairness.get("commit_completion_p95_jain"), 4)}</span></div></div></section>
<section class="section"><h2>重要数值</h2>
<div class="facts"><div class="fact"><span>Commit 平均完成</span><span>{seconds(completion.get("mean_s"))}</span></div>
<div class="fact"><span>Commit P50 / P90 / P95 / P99 / 最大</span><span>{seconds(completion.get("p50_s"))} / {seconds(completion.get("p90_s"))} / {seconds(completion.get("p95_s"))} / {seconds(completion.get("p99_s"))} / {seconds(completion.get("max_s"))}</span></div>
<div class="fact"><span>Commit 平均排队</span><span>{seconds(commit_queue.get("mean_s"))}</span></div>
<div class="fact"><span>超过 10 秒的 Commit</span><span>{esc(commit.get("delayed_count", 0))} 个</span></div>
<div class="fact"><span>Search 平均延迟</span><span>{seconds(latency.get("mean_s"))}</span></div>
<div class="fact"><span>Search P50 / P90 / P95 / P99 / 最大</span><span>{seconds(latency.get("p50_s"))} / {seconds(latency.get("p90_s"))} / {seconds(latency.get("p95_s"))} / {seconds(latency.get("p99_s"))} / {seconds(latency.get("max_s"))}</span></div>
<div class="fact"><span>Search 配置窗口 / 排空窗口 / Wall clock</span><span>{seconds(configured_duration_s)} / {seconds(metrics.get("completion_window_s"))} / {seconds(metrics.get("wall_elapsed_s"))}</span></div>
<div class="fact"><span>Commit HTTP 状态分布</span><span>{esc(commit_status_counts)}</span></div>
<div class="fact"><span>Search HTTP 状态分布</span><span>{esc(search_status_counts)}</span></div>
<div class="fact"><span>HTTP 429 / Retry-After</span><span>{commit_rate_limited} / {search_rate_limited}</span></div></div></section>
<section class="section"><h2>目标负载对账</h2><div class="facts">
<div class="fact"><span>Commit 目标 / 实际 / 缺口</span><span>{esc(targets.get("commit_submitted", commit.get("submitted", 0)))} / {esc(commit.get("submitted", 0))} / {esc(targets.get("commit_gap", 0))}</span></div>
<div class="fact"><span>Search 目标 / 实际 / 缺口</span><span>{esc(targets.get("search_submitted", configured_search_target))} / {esc(search.get("submitted", 0))} / {esc(targets.get("search_gap", configured_search_target - search.get("submitted", 0)))}</span></div>
<div class="fact"><span>Commit 缺口率</span><span>{percent(targets.get("commit_gap_rate"))}</span></div>
<div class="fact"><span>Search 缺口率</span><span>{percent(targets.get("search_gap_rate"))}</span></div></div></section>
<section class="section"><h2>服务端限流证据</h2>
<div class="notice">{esc(rate_limit_note)}</div>
<div class="facts"><div class="fact"><span>Commit Retry-After 平均 / 最大</span><span>{seconds((commit.get("retry_after") or {}).get("mean_s"))} / {seconds((commit.get("retry_after") or {}).get("max_s"))}</span></div>
<div class="fact"><span>Search Retry-After 平均 / 最大</span><span>{seconds((search.get("retry_after") or {}).get("mean_s"))} / {seconds((search.get("retry_after") or {}).get("max_s"))}</span></div>
<div class="fact"><span>/metrics 样本数</span><span>{esc(details.get("server_metrics_samples", 0))}</span></div>
<div class="fact"><span>服务端队列字段</span><span>{'已提供' if details.get('server_queue_metrics_available') else '未提供'}</span></div>
<div class="fact"><span>Commit 服务端时序覆盖</span><span>{esc(commit_server.get("observed_count", 0))} / {esc(commit_server.get("total_count", 0))}</span></div>
<div class="fact"><span>Search 服务端时序覆盖</span><span>{esc(search_server.get("observed_count", 0))} / {esc(search_server.get("total_count", 0))}</span></div>
<div class="fact"><span>Commit 服务端排队平均 / P95</span><span>{seconds((commit_server.get("queue_wait") or {}).get("mean_s"))} / {seconds((commit_server.get("queue_wait") or {}).get("p95_s"))}</span></div>
<div class="fact"><span>Search 服务端排队平均 / P95</span><span>{seconds((search_server.get("queue_wait") or {}).get("mean_s"))} / {seconds((search_server.get("queue_wait") or {}).get("p95_s"))}</span></div></div></section>
<section class="section"><h2>准入与调度证据</h2>
<div class="facts"><div class="fact"><span>策略</span><span>{esc(params.get("scheduler_policy"))}</span></div>
<div class="fact"><span>准入容量</span><span>{esc(params.get("admission_capacity"))}</span></div>
<div class="fact"><span>最大准入队列</span><span>{esc(admission.get("max_queue_depth", 0))}</span></div>
<div class="fact"><span>准入等待平均 / P95</span><span>{seconds((admission.get("wait") or {}).get("mean_s"))} / {seconds((admission.get("wait") or {}).get("p95_s"))}</span></div>
<div class="fact"><span>记录的调度事件</span><span>{esc(len(admission.get("events") or []))}</span></div></div>
<details><summary>前 80 个准入事件</summary><div class="scroll"><table><thead><tr><th>顺序</th><th>操作</th><th>租户</th><th>等待</th><th>入队深度</th></tr></thead>
<tbody>{''.join(f"<tr><td>{esc(item.get('order'))}</td><td>{esc(item.get('operation'))}</td><td>{esc(item.get('tenant'))}</td><td>{seconds(item.get('wait_s'))}</td><td>{esc(item.get('queue_depth'))}</td></tr>" for item in (admission.get("events") or [])[:80]) or '<tr><td colspan="5">没有准入事件</td></tr>'}</tbody></table></div></details></section>
<section class="section"><h2>时间窗口与延迟发生时段</h2>
<div class="notice">按请求计划/入队时间归档。慢请求仍归入实际到达的窗口，能够看出延迟是在什么时段集中发生的。</div>
<div class="scroll"><table><thead><tr><th>窗口</th><th>相对时间</th><th>请求数</th><th>Commit 提交 / 完成</th><th>Commit 慢请求</th><th>Commit P95</th><th>Search 提交 / 成功</th><th>Search 慢请求</th><th>Search P95</th><th>涉及租户</th></tr></thead>
<tbody>{bucket_rows or '<tr><td colspan="10">没有时间窗口数据</td></tr>'}</tbody></table></div></section>
<section class="section"><h2>资源趋势</h2><div class="charts">
<div class="chart-box"><div class="chart-title">RSS 内存</div><div class="chart-subtitle">斜率 {number(details.get("rss_slope_mb_min"), 2)} MB/min · {esc(details.get("samples"))} 个采样点</div>
{make_chart(resource_points, "rss_mb", "#b6403b", "RSS 内存")}</div>
<div class="chart-box"><div class="chart-title">CPU 使用率</div><div class="chart-subtitle">运行期间采样</div>
{make_chart(resource_points, "cpu_percent", "#177b63", "CPU 使用率")}</div></div></section>
<section class="section"><h2>运行配置</h2><div class="facts">
<div class="fact"><span>运行时长</span><span>{esc(params.get("duration_s"))} 秒</span></div>
<div class="fact"><span>Search 目标频率</span><span>{esc(params.get("search_rps"))} RPS</span></div>
<div class="fact"><span>租户 / Session</span><span>{esc(params.get("tenants"))} / {esc(params.get("sessions_per_tenant"))}</span></div>
<div class="fact"><span>Commit 并发</span><span>{esc(params.get("commit_workers"))}</span></div>
<div class="fact"><span>Search 并发</span><span>{esc(params.get("search_workers"))}</span></div>
<div class="fact"><span>Worker 容量校验</span><span>{esc(params.get("worker_sizing") or "未记录")}</span></div>
<div class="fact"><span>调度策略</span><span>{esc(params.get("scheduler_policy"))}</span></div>
<div class="fact"><span>Mock</span><span>否，真实 HTTP / 真实模型</span></div></div></section>
<section class="section"><h2>详细证据</h2>
<details open><summary>逐租户延迟、限流与状态（完整分位数）</summary><div class="scroll"><table><thead><tr><th>租户</th><th>Commit 提交 / 完成</th><th>Commit 均值 / P50 / P90 / P95 / P99 / 最大</th><th>Search 提交 / 成功</th><th>Search 均值 / P50 / P90 / P95 / P99 / 最大</th><th>客户端排队均值 / P95</th><th>超阈值 Commit / Search</th><th>429 Commit / Search</th><th>HTTP 状态 Commit / Search</th></tr></thead>
<tbody>{''.join(tenant_rows) or '<tr><td colspan="9">没有逐租户数据</td></tr>'}</tbody></table></div></details>
<details><summary>超过阈值的请求（{len(delayed_commits)+len(delayed_searches)} 条）</summary><div class="scroll"><table><thead><tr><th>类型</th><th>租户</th><th>开始时间</th><th>完成时间</th><th>耗时</th><th>排队</th><th>状态</th></tr></thead>
<tbody>{''.join(delayed_rows) or '<tr><td colspan="7">没有超过阈值的请求</td></tr>'}</tbody></table></div></details>
<details><summary>逐请求时间线（最多展示 500 条，完整数据见 CSV）</summary><div class="scroll"><table><thead><tr><th>操作</th><th>租户</th><th>Session</th><th>计划时间</th><th>入队/开始</th><th>完成</th><th>客户端排队</th><th>服务耗时</th><th>端到端</th><th>队列深度</th><th>状态</th></tr></thead>
<tbody>{timeline_rows or '<tr><td colspan="11">没有请求级数据</td></tr>'}</tbody></table></div></details>
</section>
<div class="footer">原始数据：同目录的 <code>status.json</code>、CSV 和原报告。注意：本报告的“租户公平性”仅是观测结果，不能替代独立认证身份下的真实隔离验证。</div>
</main></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("status_json", type=Path)
    parser.add_argument("output_html", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.status_json.read_text(encoding="utf-8"))
    summary = payload.get("summary") or payload
    args.output_html.write_text(render(summary), encoding="utf-8")
    print(args.output_html)


if __name__ == "__main__":
    main()
