#!/usr/bin/env python3
"""Render a low-density, conclusion-first EchoMem stress dashboard."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any


def esc(value: Any) -> str:
    return html.escape("-" if value is None or value == "" else str(value))


def sec(value: Any) -> str:
    try:
        return f"{float(value):.2f}s"
    except (TypeError, ValueError):
        return "-"


def num(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def load_payload(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "summaries" in payload:
        summaries = payload.get("summaries") or []
        primary = next(
            (item for item in summaries if (item.get("parameters") or {}).get("scheduler_policy") == "search-priority"),
            summaries[0] if summaries else {},
        )
        return primary, payload
    return payload.get("summary") or payload, None


def policy_row(summary: dict[str, Any]) -> str:
    params = summary.get("parameters") or {}
    metrics = summary.get("metrics") or {}
    commit = metrics.get("commit") or {}
    search = metrics.get("search") or {}
    completion = commit.get("completion") or {}
    latency = search.get("latency") or {}
    details = summary.get("details") or {}
    tenants = params.get("tenants")
    warning = " ⚠" if tenants not in (None, 4) else ""
    return (
        f"<tr><td><b>{esc(params.get('scheduler_policy'))}</b>{warning}</td>"
        f"<td>{esc(summary.get('status'))}</td><td>{esc(tenants)}</td>"
        f"<td>{esc(commit.get('completed'))}/{esc(commit.get('submitted'))}</td>"
        f"<td>{sec(completion.get('p95_s'))}</td><td>{sec(latency.get('p95_s'))}</td>"
        f"<td>{num(search.get('throughput_rps'), 2)} RPS</td>"
        f"<td>{esc(details.get('matrix_validation_error', ''))}</td></tr>"
    )


def render(primary: dict[str, Any], matrix: dict[str, Any] | None) -> str:
    details = primary.get("details") or {}
    metrics = primary.get("metrics") or {}
    params = primary.get("parameters") or {}
    commit = metrics.get("commit") or {}
    search = metrics.get("search") or {}
    completion = commit.get("completion") or {}
    latency = search.get("latency") or {}
    tenants = metrics.get("per_tenant") or {}
    status = str(primary.get("status") or "UNKNOWN").upper()
    status_class = {"PASS": "good", "INCONCLUSIVE": "warn", "FAIL": "bad"}.get(status, "warn")
    status_text = {
        "PASS": "核心请求完成，但隔离结论仍需独立认证身份",
        "INCONCLUSIVE": "证据不足，不能下完整结论",
        "FAIL": "压测失败，需要先处理运行异常",
    }.get(status, "状态未知")
    commit_total = commit.get("submitted", 0)
    commit_done = commit.get("completed", 0)
    search_total = search.get("submitted", 0)
    search_done = search.get("succeeded", 0)
    tenants_count = params.get("tenants") or len(tenants) or "-"
    matrix_rows = ""
    matrix_note = ""
    if matrix is not None:
        summaries = matrix.get("summaries") or []
        matrix_rows = "".join(policy_row(item) for item in summaries)
        bad = [item for item in summaries if (item.get("parameters") or {}).get("tenants") != 4]
        if bad:
            matrix_note = "矩阵里有旧轮次没有按 4 个租户执行；这些轮次只保留作历史记录，不用于多租户公平性结论。"
    tenant_rows = "".join(
        f"<tr><td>{esc(name)}</td>"
        f"<td>{esc((data.get('commit') or {}).get('completed'))}</td>"
        f"<td>{sec(((data.get('commit') or {}).get('completion') or {}).get('p95_s'))}</td>"
        f"<td>{sec(((data.get('search') or {}).get('latency') or {}).get('p95_s'))}</td></tr>"
        for name, data in sorted(tenants.items())
    )
    favicon = (
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E"
        "%3Crect width='64' height='64' rx='14' fill='%2317324d'/%3E"
        "%3Cpath d='M14 48V35M25 48V24M36 48V30M47 48V16' "
        "stroke='%2379d7b7' stroke-width='5' stroke-linecap='round'/%3E"
        "%3Cpath d='M10 53h44M12 20l10-7 10 8 16-12' fill='none' "
        "stroke='%23ff9f70' stroke-width='3' stroke-linecap='round' "
        "stroke-linejoin='round'/%3E%3C/svg%3E"
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="{favicon}">
<title>EchoMem 压测看板</title>
<style>
:root{{--bg:#f4f6f8;--paper:#fff;--ink:#18232d;--muted:#6f7b86;--line:#e4e9ed;
--green:#178064;--green-bg:#e8f6f0;--amber:#9a6b16;--amber-bg:#fff7df;--red:#b6403b}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
main{{max-width:1120px;margin:auto;padding:28px 18px 56px}} .head{{display:flex;gap:14px;align-items:center;margin-bottom:18px}}
.icon{{width:54px;height:54px;flex:none}} h1{{font-size:25px;line-height:1.15;margin:0}} h2{{font-size:17px;margin:0 0 12px}}
.muted,small{{color:var(--muted)}} .sub{{margin-top:4px;color:var(--muted)}} section,.metric{{background:var(--paper);
border:1px solid var(--line);border-radius:8px}} section{{padding:18px 20px;margin-top:14px}}
.hero{{display:flex;justify-content:space-between;gap:18px;align-items:center;border-left:5px solid var(--green)}}
.hero.warn{{border-left-color:var(--amber)}} .hero.bad{{border-left-color:var(--red)}} .label{{font-size:12px;color:var(--muted)}}
.status{{font-size:29px;font-weight:850;line-height:1.1;color:var(--green)}} .warn .status{{color:var(--amber)}} .bad .status{{color:var(--red)}}
.hero-meta{{text-align:right;color:var(--muted)}} code{{background:#eef2f4;padding:2px 5px;border-radius:4px;font-size:12px}}
.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:14px}} .metric{{padding:15px 16px}}
.metric .label{{display:block}} .value{{font-size:24px;font-weight:800;margin-top:4px}} .note{{font-size:12px;color:var(--muted);margin-top:3px}}
.callout{{padding:11px 13px;background:var(--amber-bg);border-left:4px solid var(--amber);color:#6c5117;margin-bottom:12px}}
.facts{{display:grid;grid-template-columns:1fr 1fr;gap:0 36px}} .fact{{display:flex;justify-content:space-between;gap:18px;
padding:8px 0;border-bottom:1px solid var(--line)}} .fact span:first-child{{color:var(--muted)}} .fact span:last-child{{font-weight:650;text-align:right}}
.scroll{{overflow:auto}} table{{width:100%;border-collapse:collapse;white-space:nowrap;font-size:13px}} th,td{{padding:9px 8px;text-align:left;border-bottom:1px solid var(--line)}}
th{{color:var(--muted);font-weight:700;background:#fafbfc}} .good{{color:var(--green)}} .bad{{color:var(--red)}} .warn{{color:var(--amber)}}
details{{margin-top:10px;border-top:1px solid var(--line);padding-top:11px}} summary{{cursor:pointer;font-weight:750}}
.footer{{margin-top:14px;font-size:12px;color:var(--muted)}} @media(max-width:720px){{.hero{{display:block}}.hero-meta{{text-align:left;margin-top:12px}}
.metrics{{grid-template-columns:1fr 1fr}}.facts{{grid-template-columns:1fr}}main{{padding:20px 12px 42px}}}}
</style></head><body><main>
<header class="head">
<svg class="icon" viewBox="0 0 56 56" role="img" aria-label="EchoMem 压测">
<rect x="3" y="3" width="50" height="50" rx="12" fill="#e8f6f0" stroke="#178064" stroke-width="2.5"/>
<path d="M13 39V29M22 39V20M31 39V25M40 39V13" fill="none" stroke="#178064" stroke-width="4" stroke-linecap="round"/>
<path d="M11 43h34" fill="none" stroke="#178064" stroke-width="2.5" stroke-linecap="round"/>
<path d="m12 17 8-5 8 6 12-9" fill="none" stroke="#b6403b" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
</svg><div><h1>EchoMem 压测看板</h1><div class="sub">结论优先 · 真实 HTTP / 真实模型 · {esc(primary.get('finished_at'))}</div></div>
</header>
<section class="hero {status_class}"><div><div class="label">本轮判定</div><div class="status">{esc(status)}</div><div class="muted">{esc(status_text)}</div></div>
<div class="hero-meta">目标 <code>{esc(primary.get('base_url'))}</code><br>{esc(params.get('duration_s'))} 秒 · {esc(params.get('search_rps'))} RPS · {esc(params.get('tenants'))} 个租户</div></section>
<div class="metrics">
<div class="metric"><span class="label">Commit</span><div class="value">{esc(commit_done)}/{esc(commit_total)}</div><div class="note">完成 / 提交</div></div>
<div class="metric"><span class="label">Commit P95</span><div class="value">{sec(completion.get('p95_s'))}</div><div class="note">最大 {sec(completion.get('max_s'))}</div></div>
<div class="metric"><span class="label">Search</span><div class="value">{esc(search_done)}/{esc(search_total)}</div><div class="note">成功 / 提交</div></div>
<div class="metric"><span class="label">Search P95</span><div class="value">{sec(latency.get('p95_s'))}</div><div class="note">吞吐 {num(search.get('throughput_rps'), 2)} RPS</div></div>
</div>
<section><h2>先看结论</h2><div class="callout">本轮请求成功率为 {num((commit.get('success_rate', 0) + search.get('success_rate', 0)) / 2 * 100, 1)}%，但 Commit P95 为 {sec(completion.get('p95_s'))}，明显慢于 Search；{esc(tenants_count)} 个租户使用当前认证方式时，只能评价请求标签分布，不能证明真正的租户隔离。</div>
<div class="facts"><div class="fact"><span>Commit 平均</span><span>{sec(completion.get('mean_s'))}</span></div>
<div class="fact"><span>Search 平均</span><span>{sec(latency.get('mean_s'))}</span></div>
<div class="fact"><span>Commit 超过 10 秒</span><span>{esc(commit.get('delayed_count', 0))} 个</span></div>
<div class="fact"><span>RSS 增长斜率</span><span>{num(details.get('rss_slope_mb_min'))} MB/min</span></div></div></section>
{f'<section><h2>策略矩阵</h2><div class="callout">{esc(matrix_note)}</div><div class="scroll"><table><thead><tr><th>策略</th><th>状态</th><th>租户数</th><th>Commit</th><th>Commit P95</th><th>Search P95</th><th>吞吐</th><th>校验</th></tr></thead><tbody>{matrix_rows}</tbody></table></div></section>' if matrix is not None else ''}
<section><h2>逐租户摘要</h2><div class="scroll"><table><thead><tr><th>租户</th><th>Commit 完成</th><th>Commit P95</th><th>Search P95</th></tr></thead><tbody>{tenant_rows or '<tr><td colspan="4">没有逐租户数据</td></tr>'}</tbody></table></div></section>
<section><h2>运行配置</h2><div class="facts"><div class="fact"><span>调度策略</span><span>{esc(params.get('scheduler_policy'))}</span></div>
<div class="fact"><span>准入容量</span><span>{esc(params.get('admission_capacity'))}</span></div>
<div class="fact"><span>Commit 并发</span><span>{esc(params.get('commit_workers'))}</span></div>
<div class="fact"><span>Search 并发</span><span>{esc(params.get('search_workers'))}</span></div>
<div class="fact"><span>租户 / 每租户 Session</span><span>{esc(params.get('tenants'))} / {esc(params.get('sessions_per_tenant'))}</span></div>
<div class="fact"><span>Mock</span><span>否，真实服务</span></div></div></section>
<section><h2>原始证据</h2><div class="facts">
<div class="fact"><span>摘要</span><span><a href="summary.json" download>下载 summary.json</a></span></div>
<div class="fact"><span>请求明细</span><span><a href="commit_results.csv" download>Commit CSV</a> · <a href="search_results.csv" download>Search CSV</a></span></div>
<div class="fact"><span>服务端采样</span><span><a href="server_metrics.csv" download>metrics CSV</a> · <a href="server_metrics.jsonl" download>metrics JSONL</a></span></div>
</div></section>
<div class="footer">报告由压测原始 JSON 生成。完整证据文件与本 HTML 位于同一目录，点击上方链接即可下载。</div>
</main></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_json", type=Path)
    parser.add_argument("output_html", type=Path)
    args = parser.parse_args()
    primary, matrix = load_payload(args.input_json)
    args.output_html.write_text(render(primary, matrix), encoding="utf-8")
    print(args.output_html)


if __name__ == "__main__":
    main()
