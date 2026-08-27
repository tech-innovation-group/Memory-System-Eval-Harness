#!/usr/bin/env python3
"""Render a data-first HTML report for an EchoMem policy matrix."""

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


def seconds(value: Any) -> str:
    try:
        return f"{float(value):.3f}s"
    except (TypeError, ValueError):
        return "-"


def number(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "-"


def load_matrix(path: Path) -> list[tuple[str, dict[str, Any], Path]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    root = path.parent
    result = []
    for summary in payload.get("summaries") or []:
        policy = str((summary.get("parameters") or {}).get("scheduler_policy") or "unknown")
        result.append((policy, summary, root / policy))
    return result


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def chart(values: list[tuple[str, float | None]], color: str, label: str) -> str:
    usable = [(name, value) for name, value in values if value is not None]
    if not usable:
        return "<div class='empty'>没有可绘制数据</div>"
    width, height, pad = 720, 230, 42
    maximum = max(value for _, value in usable) or 1.0
    bar_width = min(110, (width - 2 * pad) / max(1, len(usable)) - 18)
    bars = []
    labels = []
    for index, (name, value) in enumerate(usable):
        x = pad + index * ((width - 2 * pad) / max(1, len(usable))) + 15
        bar_height = max(2, (height - 2 * pad) * value / maximum)
        y = height - pad - bar_height
        bars.append(
            f"<rect x='{x:.1f}' y='{y:.1f}' width='{bar_width:.1f}' height='{bar_height:.1f}' "
            f"rx='3' fill='{color}'/><text x='{x + bar_width / 2:.1f}' y='{y - 7:.1f}' "
            f"text-anchor='middle' class='bar-value'>{html.escape(f'{value:.2f}s')}</text>"
        )
        labels.append(
            f"<text x='{x + bar_width / 2:.1f}' y='{height - 12}' text-anchor='middle' "
            f"class='bar-label'>{html.escape(name)}</text>"
        )
    return (
        f"<svg viewBox='0 0 {width} {height}' role='img' aria-label='{html.escape(label)}'>"
        f"<line x1='{pad}' y1='{height-pad}' x2='{width-pad}' y2='{height-pad}' class='axis'/>"
        + "".join(bars)
        + "".join(labels)
        + "</svg>"
    )


def finite_values(values: list[Any]) -> list[float]:
    result = []
    for value in values:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed == parsed:
            result.append(parsed)
    return result


def policy_data(policy: str, summary: dict[str, Any], directory: Path) -> dict[str, Any]:
    metrics = summary.get("metrics") or {}
    commit = metrics.get("commit") or {}
    search = metrics.get("search") or {}
    admission = metrics.get("admission") or {}
    details = summary.get("details") or {}
    params = summary.get("parameters") or {}
    return {
        "policy": policy,
        "summary": summary,
        "directory": directory,
        "commit": commit,
        "search": search,
        "admission": admission,
        "details": details,
        "params": params,
        "commits_csv": read_csv(directory / "commit_results.csv"),
        "searches_csv": read_csv(directory / "search_results.csv"),
        "resources_csv": read_csv(directory / "resource_samples.csv"),
    }


def render(matrix_path: Path, output_path: Path) -> None:
    policies = [
        policy_data(policy, summary, directory)
        for policy, summary, directory in load_matrix(matrix_path)
    ]
    if not policies:
        raise SystemExit(f"matrix has no summaries: {matrix_path}")

    first = policies[0]
    params = first["params"]
    identity_mode = (first["details"] or {}).get("identity_mode", "unknown")
    actual_tenants = params.get("tenants", "-")
    is_real_multitenant = identity_mode == "independent_auth_keys" and int(actual_tenants or 0) > 1
    identity_title = "真实多租户" if is_real_multitenant else "当前不是有效多租户测试"
    identity_class = "good" if is_real_multitenant else "bad"

    policy_rows = []
    for item in policies:
        c = item["commit"]
        s = item["search"]
        a = item["admission"]
        cm = c.get("completion") or {}
        sm = s.get("latency") or {}
        aw = a.get("wait") or {}
        policy_rows.append(
            "<tr>"
            f"<td><strong>{esc(item['policy'])}</strong></td>"
            f"<td>{esc(item['summary'].get('status'))}</td>"
            f"<td>{esc(c.get('submitted'))} / {esc(c.get('completed'))}</td>"
            f"<td>{seconds(cm.get('mean_s'))}</td><td>{seconds(cm.get('p50_s'))}</td>"
            f"<td>{seconds(cm.get('p95_s'))}</td><td>{seconds(cm.get('p99_s'))}</td>"
            f"<td>{seconds(cm.get('max_s'))}</td>"
            f"<td>{esc(s.get('submitted'))} / {esc(s.get('succeeded'))}</td>"
            f"<td>{seconds(sm.get('mean_s'))}</td><td>{seconds(sm.get('p95_s'))}</td>"
            f"<td>{seconds(sm.get('p99_s'))}</td>"
            f"<td>{number(s.get('throughput_rps'), 3)}</td>"
            f"<td>{seconds(aw.get('mean_s'))}</td><td>{esc(a.get('max_queue_depth', 0))}</td>"
            "</tr>"
        )

    tenant_sections = []
    delayed_sections = []
    for item in policies:
        metrics = item["summary"].get("metrics") or {}
        tenant_rows = []
        for tenant, data in sorted((metrics.get("per_tenant") or {}).items()):
            c = data.get("commit") or {}
            s = data.get("search") or {}
            cc = c.get("completion") or {}
            cq = c.get("queue_wait") or {}
            sl = s.get("latency") or {}
            tenant_rows.append(
                "<tr>"
                f"<td><strong>{esc(tenant)}</strong></td>"
                f"<td>{esc(c.get('submitted'))} / {esc(c.get('completed'))}</td>"
                f"<td>{seconds(cc.get('mean_s'))}</td><td>{seconds(cc.get('p95_s'))}</td>"
                f"<td>{seconds(cc.get('p99_s'))}</td><td>{seconds(cq.get('mean_s'))}</td>"
                f"<td>{esc(c.get('delayed_count', 0))}</td>"
                f"<td>{esc(s.get('submitted'))} / {esc(s.get('succeeded'))}</td>"
                f"<td>{seconds(sl.get('mean_s'))}</td><td>{seconds(sl.get('p95_s'))}</td>"
                f"<td>{esc(s.get('delayed_count', 0))}</td>"
                "</tr>"
            )
        tenant_sections.append(
            f"<details><summary>{esc(item['policy'])} · 逐租户统计</summary>"
            "<div class='table-wrap'><table><thead><tr>"
            "<th>租户</th><th>Commit 提交/完成</th><th>Commit 平均完成</th>"
            "<th>Commit P95</th><th>Commit P99</th><th>Commit 平均排队</th>"
            "<th>Commit 延迟数</th><th>Search 提交/成功</th><th>Search 平均</th>"
            "<th>Search P95</th><th>Search 延迟数</th>"
            "</tr></thead><tbody>"
            + ("".join(tenant_rows) or "<tr><td colspan='11'>没有逐租户数据</td></tr>")
            + "</tbody></table></div></details>"
        )

        commit_rows = []
        for row in item["commits_csv"]:
            try:
                end_to_end = float(row.get("end_to_end_s") or row.get("elapsed_s") or 0)
            except ValueError:
                end_to_end = 0
            if end_to_end >= 10 or row.get("status") not in {"completed", "complete", "succeeded", "success", "transcommit"}:
                commit_rows.append(
                    "<tr>"
                    f"<td>Commit</td><td>{esc(row.get('tenant'))}</td><td>{esc(row.get('session_id'))}</td>"
                    f"<td>{esc(row.get('queued_at'))}</td><td>{esc(row.get('started_at'))}</td>"
                    f"<td>{esc(row.get('completed_at'))}</td><td>{seconds(row.get('queue_wait_s'))}</td>"
                    f"<td>{seconds(row.get('service_s'))}</td><td>{seconds(end_to_end)}</td>"
                    f"<td>{esc(row.get('admission_queue_depth'))}</td><td>{esc(row.get('status'))}</td>"
                    "</tr>"
                )
        search_rows = []
        for row in item["searches_csv"]:
            try:
                elapsed = float(row.get("service_s") or row.get("elapsed_s") or 0)
            except ValueError:
                elapsed = 0
            if elapsed >= 2.5 or (row.get("status_code") or "").startswith(("4", "5")):
                search_rows.append(
                    "<tr>"
                    f"<td>Search</td><td>{esc(row.get('tenant'))}</td><td>{esc(row.get('session_id'))}</td>"
                    f"<td>{esc(row.get('queued_at'))}</td><td>{esc(row.get('started_at'))}</td>"
                    f"<td>{esc(row.get('finished_at'))}</td><td>{seconds(row.get('queue_wait_s'))}</td>"
                    f"<td>{seconds(row.get('service_s'))}</td><td>{seconds(elapsed)}</td>"
                    f"<td>{esc(row.get('admission_queue_depth'))}</td><td>{esc(row.get('status_code') or row.get('error'))}</td>"
                    "</tr>"
                )
        rows = "".join(commit_rows + search_rows)
        delayed_sections.append(
            f"<details><summary>{esc(item['policy'])} · 延迟事件 "
            f"({len(commit_rows) + len(search_rows)} 条)</summary>"
            "<div class='table-wrap'><table><thead><tr>"
            "<th>类型</th><th>租户</th><th>Session</th><th>入队时间</th><th>开始时间</th>"
            "<th>完成时间</th><th>排队</th><th>服务</th><th>端到端</th><th>队列深度</th><th>状态</th>"
            "</tr></thead><tbody>"
            + (rows or "<tr><td colspan='11'>没有达到当前阈值的延迟事件</td></tr>")
            + "</tbody></table></div></details>"
        )

    commit_chart = chart(
        [(item["policy"], (item["commit"].get("completion") or {}).get("p95_s")) for item in policies],
        "#b33d38",
        "各策略 Commit P95",
    )
    search_chart = chart(
        [(item["policy"], (item["search"].get("latency") or {}).get("p95_s")) for item in policies],
        "#286aa6",
        "各策略 Search P95",
    )

    original_json = html.escape(json.dumps([item["summary"] for item in policies], ensure_ascii=False, indent=2))
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>EchoMem 多租户压测数据报告</title>
<style>
:root{{--bg:#f3f5f6;--surface:#fff;--ink:#1d2933;--muted:#697783;--line:#e1e7ea;
--green:#13795b;--green-bg:#e8f6ef;--amber:#936516;--amber-bg:#fff7df;--red:#b33d38;--red-bg:#fff0ee;--blue:#286aa6}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
.page{{max-width:1440px;margin:auto;padding:28px 18px 60px}}.head{{display:flex;align-items:center;gap:14px;margin-bottom:18px}}
.logo{{width:52px;height:52px;flex:none}}h1{{margin:0;font-size:26px;line-height:1.2}}h2{{margin:0;font-size:17px;line-height:1.3}}
.muted,.sub{{color:var(--muted)}}.sub{{margin-top:4px}}.panel{{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:18px 20px;margin-top:14px}}
.hero{{display:grid;grid-template-columns:1fr auto;gap:22px;align-items:center;border-left:5px solid var(--red)}}
.hero-title{{font-size:25px;font-weight:800;color:var(--red);margin:2px 0 4px}}.hero-meta{{text-align:right;color:var(--muted);font-size:13px}}
code{{padding:2px 5px;border-radius:4px;background:#eef2f4;font:12px ui-monospace,SFMono-Regular,Menlo,monospace}}
.callout{{margin-top:12px;padding:11px 13px;border-left:4px solid var(--red);background:var(--red-bg);color:#71322f}}
.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:14px}}.metric{{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:14px 15px}}
.label{{font-size:12px;color:var(--muted)}}.value{{font-size:24px;font-weight:800;margin-top:3px}}.note{{font-size:12px;color:var(--muted);margin-top:2px}}
.good{{color:var(--green)}}.bad{{color:var(--red)}}.blue{{color:var(--blue)}}.section-head{{display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:12px}}
.section-note{{font-size:12px;color:var(--muted)}}.table-wrap{{overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:12px;white-space:nowrap}}
th,td{{padding:9px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:#fafbfc;color:var(--muted);font-weight:700}}
.charts{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}.chart{{border:1px solid var(--line);padding:12px}}.chart-title{{font-weight:750}}svg{{display:block;width:100%;height:auto}}
.axis{{stroke:#d8e0e4}}.bar-value{{font-size:12px;fill:var(--ink)}}.bar-label{{font-size:12px;fill:var(--muted)}}
details{{border-top:1px solid var(--line);padding-top:11px}}details+details{{margin-top:12px}}summary{{cursor:pointer;font-weight:750}}
pre{{max-height:460px;overflow:auto;padding:12px;background:#f7f9fa;border:1px solid var(--line);border-radius:6px;font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap}}
.empty{{padding:24px;text-align:center;color:var(--muted)}}.footer{{margin-top:14px;color:var(--muted);font-size:12px}}
@media(max-width:800px){{.page{{padding:20px 12px 44px}}.hero{{display:block}}.hero-meta{{margin-top:12px;text-align:left}}.metrics{{grid-template-columns:1fr 1fr}}.charts{{grid-template-columns:1fr}}}}
</style>
</head>
<body><main class="page">
<header class="head">
<svg class="logo" viewBox="0 0 52 52" role="img" aria-label="EchoMem">
<path d="M26 3 47 14.5v23L26 49 5 37.5v-23z" fill="#e8f6ef" stroke="#13795b" stroke-width="2.5"/>
<path d="m12 18 14 8 14-8M26 26v15M18 22.5v9l8 4.5 8-4.5v-9" fill="none" stroke="#13795b" stroke-width="2.5" stroke-linejoin="round"/>
<circle cx="26" cy="13" r="3" fill="#13795b"/>
</svg>
<div><h1>EchoMem 压测数据报告</h1><div class="sub">策略矩阵 · 逐请求数据 · 真实 HTTP / 真实模型 · {esc(first['summary'].get('finished_at'))}</div></div>
</header>

<section class="panel hero">
<div><div class="label">数据有效性</div><div class="hero-title">{identity_title}</div>
<div>本报告可以用于比较当前几种压测端准入策略的数值，但不能把共享身份结果当作租户隔离或租户公平性结论。</div>
<div class="callout">当前矩阵配置：<strong>{esc(actual_tenants)} 个标记租户</strong>，认证模式为 <code>{esc(identity_mode)}</code>。要通过多租户上线门槛，必须改用每个租户独立 API Key，并完成完整 N×N 隔离探针。</div></div>
<div class="hero-meta">服务 <code>{esc(first['summary'].get('base_url'))}</code><br>
时长 <code>{esc(params.get('duration_s'))}s</code> · Search <code>{esc(params.get('search_rps'))} RPS</code><br>
策略数量 <code>{len(policies)}</code></div>
</section>

<div class="metrics">
<div class="metric"><div class="label">最快 Commit P95</div><div class="value bad">{seconds(min(finite_values([(item['commit'].get('completion') or {}).get('p95_s') for item in policies]) or [None]))}</div><div class="note">策略矩阵最小值</div></div>
<div class="metric"><div class="label">最快 Search P95</div><div class="value blue">{seconds(min(finite_values([(item['search'].get('latency') or {}).get('p95_s') for item in policies]) or [None]))}</div><div class="note">策略矩阵最小值</div></div>
<div class="metric"><div class="label">Commit 延迟事件</div><div class="value">{sum(len(item['commits_csv']) for item in policies)}</div><div class="note">原始 Commit 记录总数</div></div>
<div class="metric"><div class="label">策略对比</div><div class="value">{len(policies)}</div><div class="note">FIFO / Search 优先 / Tenant Fair</div></div>
</div>

<section class="panel"><div class="section-head"><h2>策略对比：完整数值</h2><span class="section-note">时间单位均为秒；Commit 为端到端完成时间</span></div>
<div class="table-wrap"><table><thead><tr>
<th>策略</th><th>状态</th><th>Commit 提交/完成</th><th>Commit 平均</th><th>P50</th><th>P95</th><th>P99</th><th>最大</th>
<th>Search 提交/成功</th><th>Search 平均</th><th>P95</th><th>P99</th><th>吞吐 RPS</th><th>准入平均等待</th><th>最大队列</th>
</tr></thead><tbody>{''.join(policy_rows)}</tbody></table></div></section>

<section class="panel"><div class="section-head"><h2>延迟对比图</h2><span class="section-note">图表只做策略对比，不能替代原始请求明细</span></div>
<div class="charts"><div class="chart"><div class="chart-title">Commit P95</div>{commit_chart}</div>
<div class="chart"><div class="chart-title">Search P95</div>{search_chart}</div></div></section>

<section class="panel"><div class="section-head"><h2>多租户数据</h2><span class="section-note">当前共享身份，数据仅作标签级观测</span></div>
{''.join(tenant_sections)}</section>

<section class="panel"><div class="section-head"><h2>什么时候发生了延迟</h2><span class="section-note">Commit ≥ 10s，Search ≥ 2.5s 或 HTTP 4xx/5xx</span></div>
{''.join(delayed_sections)}</section>

<section class="panel"><div class="section-head"><h2>运行配置与判定限制</h2></div>
<div class="table-wrap"><table><tbody>
<tr><th>调度策略</th><td>{esc(params.get('scheduler_policy'))}</td><th>准入容量</th><td>{esc(params.get('admission_capacity'))}</td></tr>
<tr><th>Search 准入容量</th><td>{esc(params.get('search_admission_capacity', '-'))}</td><th>Commit 准入容量</th><td>{esc(params.get('commit_admission_capacity', '-'))}</td></tr>
<tr><th>Commit 并发</th><td>{esc(params.get('commit_workers'))}</td><th>Search 并发</th><td>{esc(params.get('search_workers'))}</td></tr>
<tr><th>认证模式</th><td>{esc(identity_mode)}</td><th>隔离探针</th><td>未执行有效 N×N 独立身份探针</td></tr>
<tr><th>模型/服务</th><td>真实 HTTP / 真实模型</td><th>Mock</th><td>否</td></tr>
</tbody></table></div></section>

<section class="panel"><details><summary>展开原始 summary.json 内容</summary><pre>{original_json}</pre></details></section>
<div class="footer">数据源：每个策略目录下的 summary.json、commit_results.csv、search_results.csv、resource_samples.csv。正式上线前需要使用独立 API Key 重跑，并采集服务端 request_id、队列深度和限流事件。</div>
</main></body></html>"""
    output_path.write_text(document, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Render detailed EchoMem stress matrix HTML")
    parser.add_argument("matrix_json", type=Path)
    parser.add_argument("output_html", type=Path)
    args = parser.parse_args()
    render(args.matrix_json, args.output_html)
    print(args.output_html)


if __name__ == "__main__":
    main()
