#!/usr/bin/env python3
"""Render a compact, readable report for one formal EchoMem stress run."""

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
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    return html.escape(str(value))


def seconds(value: Any) -> str:
    try:
        return f"{float(value):.2f}s"
    except (TypeError, ValueError):
        return "-"


def percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "-"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def metric(summary: dict[str, Any], *path: str) -> Any:
    current: Any = summary
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def render(summary_path: Path, output_path: Path) -> None:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    root = summary_path.parent
    details = summary.get("details") or {}
    metrics = summary.get("metrics") or {}
    params = summary.get("parameters") or {}
    isolation = details.get("isolation") or {}
    probes = isolation.get("probes") or []
    tenants = metrics.get("per_tenant") or {}
    targets = metrics.get("targets") or {}
    fairness = metrics.get("fairness") or {}
    commits = read_csv(root / "commit_results.csv")
    searches = read_csv(root / "search_results.csv")
    status = str(summary.get("status") or "UNKNOWN").upper()
    status_class = status.lower().replace("_", "-")

    commit = metrics.get("commit") or {}
    search = metrics.get("search") or {}
    commit_completion = commit.get("completion") or {}
    search_latency = search.get("latency") or {}
    cross_hits = sum(1 for item in probes if not item.get("same_tenant") and item.get("marker_found"))
    same_total = sum(1 for item in probes if item.get("same_tenant"))
    same_hits = sum(1 for item in probes if item.get("same_tenant") and item.get("marker_found"))
    isolation_failures = int(isolation.get("invalid_probe_count") or 0)
    commit_done = int(commit.get("completed") or 0)
    commit_submitted = int(commit.get("submitted") or len(commits))
    search_done = int(search.get("succeeded") or 0)
    search_submitted = int(search.get("submitted") or len(searches))
    search_target = int(targets.get("search_submitted") or 0)
    search_gap = int(targets.get("search_gap") or max(search_target - search_submitted, 0))
    server_metrics_samples = int(details.get("server_metrics_samples") or 0)
    server_queue_available = bool(details.get("server_queue_metrics_available"))

    icon = """<svg class="brand-icon" viewBox="0 0 64 64" role="img" aria-label="EchoMem">
      <rect x="3" y="3" width="58" height="58" rx="14" fill="#123047"/>
      <path d="M16 46V31M27 46V22M38 46V27M49 46V15" stroke="#61d1b1" stroke-width="5" stroke-linecap="round"/>
      <path d="M12 51h40M14 23l11-8 11 9 14-12" fill="none" stroke="#ff996d" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>"""
    favicon = (
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E"
        "%3Crect x='3' y='3' width='58' height='58' rx='14' fill='%23123047'/%3E"
        "%3Cpath d='M16 46V31M27 46V22M38 46V27M49 46V15' stroke='%2361d1b1' stroke-width='5' stroke-linecap='round'/%3E"
        "%3Cpath d='M12 51h40M14 23l11-8 11 9 14-12' fill='none' stroke='%23ff996d' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'/%3E"
        "%3C/svg%3E"
    )

    tenant_rows = []
    for tenant, data in sorted(tenants.items()):
        c = data.get("commit") or {}
        s = data.get("search") or {}
        cc = c.get("completion") or {}
        sl = s.get("latency") or {}
        tenant_rows.append(
            "<tr>"
            f"<td><b>{esc(tenant)}</b></td>"
            f"<td>{esc(c.get('submitted', 0))} / {esc(c.get('completed', 0))}</td>"
            f"<td>{seconds(cc.get('mean_s'))}</td><td>{seconds(cc.get('p95_s'))}</td>"
            f"<td>{esc(s.get('submitted', 0))} / {esc(s.get('succeeded', 0))}</td>"
            f"<td>{seconds(sl.get('p95_s'))}</td>"
            "</tr>"
        )

    failed_probe_rows = []
    for item in probes:
        failed = item.get("marker_found") != item.get("expected")
        if not failed:
            continue
        failed_probe_rows.append(
            "<tr class='bad'>"
            f"<td>{esc(item.get('writer'))}</td><td>{esc(item.get('reader'))}</td>"
            f"<td>{esc(item.get('marker_index'))}</td><td>{'是' if item.get('expected') else '否'}</td>"
            f"<td>{'是' if item.get('marker_found') else '否'}</td>"
            f"<td>{esc(item.get('status_code'))}</td><td>{seconds(item.get('latency_s'))}</td>"
            "</tr>"
        )

    timeline_rows = []
    for row in commits + searches:
        operation = "Commit" if row in commits else "Search"
        status_value = row.get("status") or row.get("status_code") or row.get("error")
        timeline_rows.append(
            "<tr>"
            f"<td><span class='op {operation.lower()}'>{operation}</span></td>"
            f"<td>{esc(row.get('tenant'))}</td><td>{esc(row.get('request_id'))}</td>"
            f"<td>{esc(row.get('started_at') or row.get('queued_at'))}</td>"
            f"<td>{esc(row.get('completed_at') or row.get('finished_at'))}</td>"
            f"<td>{seconds(row.get('end_to_end_s') or row.get('elapsed_s'))}</td>"
            f"<td>{esc(status_value)}</td>"
            "</tr>"
        )

    recommendations = [
        "先修复租户路由或 workspace 隔离：本轮 80 条探针有 "
        f"<b class='red'>{isolation_failures}</b> 条异常，其中跨租户误命中 "
        f"<b class='red'>{cross_hits}</b> 条。",
        "本轮 Search 负载没有打满：目标 "
        f"<b class='red'>{search_target}</b> 次，实际提交 <b class='red'>{search_submitted}</b> 次，"
        f"缺口 <b class='red'>{search_gap}</b> 次；本轮 Search 延迟不能作为容量结论。",
        "服务端采集到 "
        f"<b>{server_metrics_samples}</b> 个 metrics 样本，但逐请求服务端队列字段"
        f" <b class='red'>{'已覆盖' if server_queue_available else '未覆盖'}</b>，需要补齐服务端时序后再判断内部排队。",
    ]
    recommendation_html = "".join(f"<li>{item}</li>" for item in recommendations)

    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="{favicon}">
<title>EchoMem 压测报告</title>
<style>
:root{{--bg:#f4f7f8;--paper:#fff;--ink:#17212b;--muted:#6b7a86;--line:#dfe7ea;
--green:#177b63;--green-bg:#e8f6f0;--red:#b6403b;--red-bg:#fff0ee;--amber:#966917;--amber-bg:#fff7df;--blue:#2d6da3}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
.page{{max-width:1180px;margin:auto;padding:28px 18px 56px}}.top{{display:flex;align-items:center;gap:13px;margin-bottom:18px}}
.brand-icon{{width:52px;height:52px;flex:none}}h1{{margin:0;font-size:25px;line-height:1.15}}h2{{margin:0 0 12px;font-size:17px}}
h3{{margin:17px 0 8px;font-size:14px}}small,.muted{{color:var(--muted)}}.top small{{display:block;margin-top:3px}}
.hero,.section,.kpi{{background:var(--paper);border:1px solid var(--line);border-radius:8px}}
.hero{{display:flex;justify-content:space-between;gap:20px;padding:19px 21px;border-left:5px solid var(--red);margin-bottom:12px}}
.hero.inconclusive{{border-left-color:var(--amber)}}.hero.pass{{border-left-color:var(--green)}}.hero-status{{font-size:27px;font-weight:850;line-height:1.1;color:var(--red)}}
.hero-status.inconclusive{{color:var(--amber)}}.hero-status.pass{{color:var(--green)}}.hero-meta{{text-align:right;color:var(--muted)}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:12px 0}}.kpi{{padding:14px 15px}}
.kpi-label{{font-size:12px;color:var(--muted)}}.kpi-value{{font-size:24px;font-weight:850;margin-top:3px}}.kpi-note{{font-size:12px;color:var(--muted)}}
.section{{padding:18px 19px;margin-top:12px}}.notice{{padding:12px 14px;background:var(--amber-bg);border-left:4px solid var(--amber);color:#6d5117;margin-bottom:12px}}
.danger{{background:var(--red-bg);border-left-color:var(--red);color:#78312d}}.summary-list{{margin:8px 0 0;padding-left:21px}}.summary-list li{{margin:7px 0}}
.red{{color:var(--red)}}.green{{color:var(--green)}}.facts{{display:grid;grid-template-columns:1fr 1fr;column-gap:38px}}
.fact{{display:flex;justify-content:space-between;gap:18px;padding:8px 0;border-bottom:1px solid var(--line)}}.fact span:first-child{{color:var(--muted)}}.fact span:last-child{{font-weight:650;text-align:right;overflow-wrap:anywhere}}
.scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap}}th,td{{padding:8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:#fafbfc;color:var(--muted);font-weight:700}}tr.bad td{{background:#fff9f8}}
.op{{font-weight:750}}.op.commit{{color:var(--red)}}.op.search{{color:var(--blue)}}details{{border-top:1px solid var(--line);padding:11px 0}}details:first-of-type{{border-top:0}}summary{{cursor:pointer;font-weight:750;color:#344454}}
.footer{{margin-top:14px;font-size:12px;color:var(--muted)}}code{{background:#eef2f4;padding:2px 5px;border-radius:4px;font-size:12px}}
@media(max-width:760px){{.hero{{display:block}}.hero-meta{{text-align:left;margin-top:11px}}.kpis{{grid-template-columns:1fr 1fr}}.facts{{grid-template-columns:1fr}}.page{{padding:20px 12px 42px}}}}
</style>
</head>
<body><main class="page">
<header class="top">{icon}<div><h1>EchoMem 真实服务压测报告</h1>
<small>PR397 压测方案 · {esc(summary.get("finished_at"))}</small></div></header>
<section class="hero {status_class}"><div><small>总体判定</small><div class="hero-status {status_class}">{esc(status)}</div>
<div class="muted">本报告先给结论，再展开证据；不把缺失数据当作通过。</div></div>
<div class="hero-meta">目标 <code>{esc(summary.get("base_url"))}</code><br>
{esc(params.get("duration_s"))} 秒 · {esc(params.get("tenants"))} 个租户 · Search {esc(params.get("search_rps"))} RPS</div></section>
<section class="kpis">
<div class="kpi"><div class="kpi-label">Commit 完成</div><div class="kpi-value">{commit_done}/{commit_submitted}</div><div class="kpi-note">全部请求完成</div></div>
<div class="kpi"><div class="kpi-label">Commit P95</div><div class="kpi-value">{seconds(commit_completion.get("p95_s"))}</div><div class="kpi-note">端到端完成时间</div></div>
<div class="kpi"><div class="kpi-label">Search 实际 / 目标</div><div class="kpi-value red">{search_submitted}/{search_target}</div><div class="kpi-note">缺口 {search_gap} 次</div></div>
<div class="kpi"><div class="kpi-label">隔离异常</div><div class="kpi-value red">{isolation_failures}/{len(probes)}</div><div class="kpi-note">跨租户误命中 {cross_hits} 条</div></div>
</section>
<section class="section"><h2>一眼看懂</h2>
<div class="notice danger"><b>当前不能作为上线通过依据：</b>租户隔离失败，且 Search 目标负载没有实际打满。</div>
<ul class="summary-list">{recommendation_html}</ul></section>
<section class="section"><h2>关键结论</h2><div class="facts">
<div class="fact"><span>隔离探针</span><span class="red">{esc(isolation.get("status"))} · {isolation_failures} 条异常</span></div>
<div class="fact"><span>同租户命中</span><span class="green">{same_hits} / {same_total}</span></div>
<div class="fact"><span>跨租户误命中</span><span class="red">{cross_hits} 条</span></div>
<div class="fact"><span>Commit P95 最大 / 最小</span><span>{esc(fairness.get("commit_completion_p95_max_min_ratio"))} 倍</span></div>
<div class="fact"><span>服务端 metrics 样本</span><span>{server_metrics_samples}</span></div>
<div class="fact"><span>逐请求服务端队列字段</span><span class="red">{'已提供' if server_queue_available else '未提供'}</span></div></div></section>
<section class="section"><h2>逐租户对比</h2><div class="scroll"><table><thead><tr><th>租户</th><th>Commit 提交 / 完成</th><th>Commit 平均</th><th>Commit P95</th><th>Search 提交 / 成功</th><th>Search P95</th></tr></thead>
<tbody>{''.join(tenant_rows) or '<tr><td colspan="6">没有租户数据</td></tr>'}</tbody></table></div></section>
<section class="section"><h2>运行配置</h2><div class="facts">
<div class="fact"><span>运行时长</span><span>{esc(params.get("duration_s"))} 秒</span></div>
<div class="fact"><span>租户 / Session</span><span>{esc(params.get("tenants"))} / {esc(params.get("sessions_per_tenant"))}</span></div>
<div class="fact"><span>Commit / Search 并发</span><span>{esc(params.get("commit_workers"))} / {esc(params.get("search_workers"))}</span></div>
<div class="fact"><span>调度策略</span><span>{esc(params.get("scheduler_policy"))}</span></div>
<div class="fact"><span>客户端准入</span><span>{'关闭' if params.get('no_client_admission') else '开启'}</span></div>
<div class="fact"><span>测试类型</span><span>真实 HTTP，不使用 Mock</span></div></div></section>
<section class="section"><h2>详细证据</h2>
<details open><summary>隔离失败探针（{len(failed_probe_rows)} 条）</summary><div class="scroll"><table><thead><tr><th>写入租户</th><th>读取租户</th><th>Marker</th><th>期望命中</th><th>实际命中</th><th>HTTP</th><th>耗时</th></tr></thead>
<tbody>{''.join(failed_probe_rows) or '<tr><td colspan="7">没有失败探针</td></tr>'}</tbody></table></div></details>
<details><summary>逐请求时间线（{len(timeline_rows)} 条）</summary><div class="scroll"><table><thead><tr><th>操作</th><th>租户</th><th>Request ID</th><th>开始时间</th><th>完成时间</th><th>端到端</th><th>状态</th></tr></thead>
<tbody>{''.join(timeline_rows) or '<tr><td colspan="7">没有请求数据</td></tr>'}</tbody></table></div></details>
<details><summary>原始文件</summary><p class="muted"><a href="summary.json">summary.json</a> · <a href="commit_results.csv">commit_results.csv</a> · <a href="search_results.csv">search_results.csv</a> · <a href="server_metrics.csv">server_metrics.csv</a> · <a href="server_metrics.jsonl">server_metrics.jsonl</a></p></details>
</section>
<div class="footer">报告生成器：<code>readable_stress_report.py</code> · 数据源：<code>{esc(summary_path)}</code></div>
</main></body></html>"""
    output_path.write_text(document, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("summary_json", type=Path)
    parser.add_argument("output_html", type=Path)
    args = parser.parse_args()
    render(args.summary_json, args.output_html)
    print(args.output_html)


if __name__ == "__main__":
    main()
