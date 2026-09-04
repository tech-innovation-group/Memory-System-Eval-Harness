#!/usr/bin/env python3
"""Render a compact, readable HTML report for an EchoMem stress matrix."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any


def esc(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return html.escape(str(value))


def sec(value: Any) -> str:
    try:
        return f"{float(value):.2f}s"
    except (TypeError, ValueError):
        return "-"


def metric(summary: dict[str, Any], operation: str, group: str, key: str) -> Any:
    return (
        (((summary.get("metrics") or {}).get(operation) or {}).get(group) or {}).get(key)
    )


def pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "-"


def policy_label(policy: str) -> str:
    return {
        "fifo": "FIFO",
        "search-priority": "Search 优先",
        "dual-lane": "双通道",
        "tenant-fair": "租户公平",
        "dual-lane-tenant-fair": "双通道 + 租户公平",
    }.get(policy, policy)


def policy_note(policy: str) -> str:
    return {
        "fifo": "所有请求进入同一条队列，按到达顺序处理。",
        "search-priority": "Search 优先处理，观察 Commit 是否被延迟。",
        "dual-lane": "Search 与 Commit 使用独立准入通道。",
        "tenant-fair": "不同租户轮询进入队列，避免单租户长期占用。",
        "dual-lane-tenant-fair": "双通道并叠加租户公平轮询。",
    }.get(policy, "压测调度策略。")


def status_class(status: Any) -> str:
    return str(status or "UNKNOWN").lower().replace("_", "-")


def bar(value: Any, maximum: float, color: str) -> str:
    try:
        width = min(100.0, max(1.0, float(value) / maximum * 100))
    except (TypeError, ValueError, ZeroDivisionError):
        width = 1.0
    return f"<span class='bar {color}' style='width:{width:.1f}%'></span>"


def render(matrix_path: Path, output_path: Path) -> None:
    payload = json.loads(matrix_path.read_text(encoding="utf-8"))
    summaries = payload.get("summaries") or []
    if not summaries:
        raise SystemExit(f"matrix has no summaries: {matrix_path}")

    first = summaries[0]
    first_params = first.get("parameters") or {}
    first_details = first.get("details") or {}
    identity_mode = first_details.get("identity_mode", "unknown")
    tenant_count = int(first_params.get("tenants") or 0)
    real_multitenant = identity_mode == "independent_auth_keys" and tenant_count > 1
    sample_count = sum(
        int((s.get("metrics") or {}).get("commit", {}).get("completed") or 0)
        + int((s.get("metrics") or {}).get("search", {}).get("succeeded") or 0)
        for s in summaries
    )
    commit_p95s = [
        float(metric(s, "commit", "completion", "p95_s") or 0) for s in summaries
    ]
    search_p95s = [
        float(metric(s, "search", "latency", "p95_s") or 0) for s in summaries
    ]
    max_latency = max(commit_p95s + search_p95s + [1.0])

    policy_cards: list[str] = []
    detail_blocks: list[str] = []
    for summary in summaries:
        params = summary.get("parameters") or {}
        details = summary.get("details") or {}
        metrics = summary.get("metrics") or {}
        policy = str(params.get("scheduler_policy") or "unknown")
        status = str(summary.get("status") or "UNKNOWN")
        commit = metrics.get("commit") or {}
        search = metrics.get("search") or {}
        c_completion = commit.get("completion") or {}
        s_latency = search.get("latency") or {}
        admission = metrics.get("admission") or {}
        isolation = details.get("isolation") or {}
        policy_dir = matrix_path.parent / policy
        links = []
        for name in ("report.html", "summary.json", "commit_results.csv", "search_results.csv"):
            if (policy_dir / name).exists():
                links.append(f"<a href='{esc(policy + '/' + name)}'>{esc(name)}</a>")

        policy_cards.append(
            f"<article class='policy-card'>"
            f"<div class='policy-head'><div><h3>{esc(policy_label(policy))}</h3>"
            f"<p>{esc(policy_note(policy))}</p></div>"
            f"<span class='badge {status_class(status)}'>{esc(status)}</span></div>"
            f"<div class='latency'><div class='latency-row'><span>Commit P95</span>"
            f"<div class='track'>{bar(c_completion.get('p95_s'), max_latency, 'commit')}</div>"
            f"<b>{sec(c_completion.get('p95_s'))}</b></div>"
            f"<div class='latency-row'><span>Search P95</span>"
            f"<div class='track'>{bar(s_latency.get('p95_s'), max_latency, 'search')}</div>"
            f"<b>{sec(s_latency.get('p95_s'))}</b></div></div>"
            f"<div class='mini-stats'><span>Commit <b>{esc(commit.get('completed', 0))}/{esc(commit.get('submitted', 0))}</b></span>"
            f"<span>Search <b>{esc(search.get('succeeded', 0))}/{esc(search.get('submitted', 0))}</b></span>"
            f"<span>最大队列 <b>{esc(admission.get('max_queue_depth', 0))}</b></span></div></article>"
        )

        tenant_rows = []
        for tenant, data in sorted((metrics.get("per_tenant") or {}).items()):
            tc = data.get("commit") or {}
            ts = data.get("search") or {}
            tenant_rows.append(
                f"<tr><td>{esc(tenant)}</td><td>{esc(tc.get('completed', 0))}/{esc(tc.get('submitted', 0))}</td>"
                f"<td>{sec((tc.get('completion') or {}).get('p95_s'))}</td>"
                f"<td>{esc(ts.get('succeeded', 0))}/{esc(ts.get('submitted', 0))}</td>"
                f"<td>{sec((ts.get('latency') or {}).get('p95_s'))}</td></tr>"
            )
        detail_blocks.append(
            f"<details><summary>{esc(policy_label(policy))} · 逐租户和文件</summary>"
            f"<div class='detail-body'><div class='table-wrap'><table><thead><tr>"
            f"<th>租户</th><th>Commit 完成</th><th>Commit P95</th><th>Search 成功</th><th>Search P95</th>"
            f"</tr></thead><tbody>{''.join(tenant_rows) or '<tr><td colspan=5>没有逐租户数据</td></tr>'}</tbody></table></div>"
            f"<p class='isolation'>隔离探针：<b>{esc(isolation.get('status', '未提供'))}</b>，"
            f"{esc(isolation.get('probe_count', 0))}/{esc(isolation.get('expected_probe_count', 0))} 条，"
            f"异常 {esc(isolation.get('invalid_probe_count', 0))} 条。</p>"
            f"<p class='links'>{' · '.join(links) or '原始文件不存在'}</p></div></details>"
        )

    statuses = [str(s.get("status") or "UNKNOWN") for s in summaries]
    overall = "INCONCLUSIVE" if "INCONCLUSIVE" in statuses else (
        "FAIL" if "FAIL" in statuses else "PASS"
    )
    conclusion = (
        "样本量太小，只能确认流程和报告链路正常，不能据此判断上线性能。"
        if sample_count < 50
        else "本轮已完成服务端观测，可结合逐租户和异常事件继续判断瓶颈。"
    )
    evidence = (
        "独立认证身份，可用于隔离分析。"
        if real_multitenant
        else "未确认使用独立认证身份，不能把本报告当作真实租户隔离结论。"
    )
    favicon = (
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E"
        "%3Crect width='64' height='64' rx='14' fill='%2317324d'/%3E"
        "%3Cpath d='M14 49V35M25 49V24M36 49V30M47 49V16' stroke='%2372d5b7' stroke-width='5' stroke-linecap='round'/%3E"
        "%3Cpath d='M10 53h44M12 20l10-7 10 8 16-12' fill='none' stroke='%23ff9d6e' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'/%3E"
        "%3C/svg%3E"
    )
    doc = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="{favicon}"><title>EchoMem 压测报告</title>
<style>
:root{{--bg:#f4f6f7;--paper:#fff;--ink:#18232d;--muted:#71808b;--line:#dfe6ea;--navy:#17324d;--green:#168266;--green-bg:#e8f6f0;--orange:#e77f56;--amber:#9a6b16;--amber-bg:#fff7df;--red:#b6403b}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
.page{{max-width:1040px;margin:auto;padding:28px 18px 56px}}.header{{display:flex;align-items:center;gap:13px;margin-bottom:18px}}.logo{{width:52px;height:52px;flex:none}}
h1{{font-size:25px;line-height:1.15;margin:0}}h2{{font-size:17px;margin:0 0 10px}}h3{{font-size:16px;margin:0}}p{{margin:4px 0;color:var(--muted)}}small,.muted{{color:var(--muted)}}
.hero,.section,.policy-card{{background:var(--paper);border:1px solid var(--line);border-radius:8px}}.hero{{padding:18px 20px;border-left:5px solid var(--amber);display:flex;justify-content:space-between;gap:18px}}.hero.good{{border-left-color:var(--green)}}.hero strong{{font-size:18px}}
.hero-meta{{text-align:right;color:var(--muted)}}code{{background:#eef2f4;border-radius:4px;padding:2px 5px;font-size:12px}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:12px 0}}.kpi{{background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:13px 15px}}.kpi-label{{font-size:12px;color:var(--muted)}}.kpi-value{{font-size:23px;font-weight:800;margin-top:2px}}.kpi-note{{font-size:12px;color:var(--muted)}}
.section{{padding:17px 19px;margin-top:12px}}.section-head{{display:flex;justify-content:space-between;gap:12px;align-items:baseline;margin-bottom:12px}}.notice{{background:var(--amber-bg);border-left:4px solid var(--amber);padding:10px 12px;color:#6c5117;margin-bottom:12px}}
.policies{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}}.policy-card{{padding:14px 15px}}.policy-head{{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}}.policy-head p{{font-size:12px;max-width:530px}}
.badge{{display:inline-block;white-space:nowrap;padding:3px 8px;border-radius:999px;font-size:11px;font-weight:750;background:#edf1f3;color:var(--muted)}}.badge.pass{{background:var(--green-bg);color:var(--green)}}.badge.inconclusive{{background:var(--amber-bg);color:var(--amber)}}.badge.fail{{background:#fff0ee;color:var(--red)}}
.latency{{margin-top:13px}}.latency-row{{display:grid;grid-template-columns:88px 1fr 58px;align-items:center;gap:8px;margin:8px 0;font-size:12px}}.latency-row b{{text-align:right}}.track{{height:9px;background:#edf1f3;border-radius:3px;overflow:hidden}}.bar{{display:block;height:100%;border-radius:3px;min-width:2px}}.bar.commit{{background:var(--green)}}.bar.search{{background:var(--orange)}}
.mini-stats{{display:flex;flex-wrap:wrap;gap:12px;margin-top:13px;padding-top:10px;border-top:1px solid var(--line);font-size:12px;color:var(--muted)}}.mini-stats b{{color:var(--ink);margin-left:3px}}
.table-wrap{{overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}th{{color:var(--muted);background:#fafbfc}}details{{border-top:1px solid var(--line);padding:10px 0}}details:first-of-type{{border-top:0}}summary{{cursor:pointer;font-weight:700}}.detail-body{{padding-top:10px}}.isolation{{color:var(--ink)}}.links{{line-height:2}}a{{color:#286aa6}}.footer{{margin-top:14px;color:var(--muted);font-size:12px}}
@media(max-width:700px){{.page{{padding:20px 12px 42px}}.hero{{display:block}}.hero-meta{{text-align:left;margin-top:10px}}.kpis{{grid-template-columns:1fr 1fr}}.policies{{grid-template-columns:1fr}}}}
</style></head><body><main class="page">
<header class="header"><svg class="logo" viewBox="0 0 56 56" role="img" aria-label="EchoMem 压测报告"><rect x="3" y="3" width="50" height="50" rx="13" fill="#17324d"/><path d="M13 40V29M22 40V20M31 40V25M40 40V13" stroke="#72d5b7" stroke-width="4" stroke-linecap="round"/><path d="M11 44h34M12 17l8-5 8 6 12-9" fill="none" stroke="#ff9d6e" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg><div><h1>EchoMem 压测报告</h1><div class="muted">服务端观测 · 结论优先 · 详情默认收起</div></div></header>
<section class="hero {'good' if overall == 'PASS' else ''}"><div><div class="muted">本轮状态</div><strong>{esc(overall)}</strong><p>{esc(conclusion)}</p><p>{esc(evidence)}</p></div><div class="hero-meta">目标 <code>{esc(first.get('base_url'))}</code><br>{esc(first_params.get('tenants', '-'))} 个租户 · {esc(first_params.get('duration_s', '-'))} 秒</div></section>
<div class="kpis"><div class="kpi"><div class="kpi-label">运行结果</div><div class="kpi-value">{len(summaries)}</div><div class="kpi-note">正式运行统一为服务端观测</div></div><div class="kpi"><div class="kpi-label">有效样本</div><div class="kpi-value">{sample_count}</div><div class="kpi-note">Commit 完成 + Search 成功</div></div><div class="kpi"><div class="kpi-label">Commit P95</div><div class="kpi-value">{sec(min(commit_p95s))}</div><div class="kpi-note">当前运行结果最低值</div></div><div class="kpi"><div class="kpi-label">Search P95</div><div class="kpi-value">{sec(min(search_p95s))}</div><div class="kpi-note">当前运行结果最低值</div></div></div>
<section class="section"><div class="section-head"><h2>先看结论</h2><span class="muted">不要只看 PASS/FAIL</span></div><div class="notice">{esc(conclusion)} 报告同时保留完成数、P95、队列和逐租户证据，方便判断是服务性能问题还是样本不足。</div><div class="policies">{''.join(policy_cards)}</div></section>
<section class="section"><div class="section-head"><h2>逐租户与原始证据</h2><span class="muted">点击策略展开</span></div>{''.join(detail_blocks)}</section>
<div class="footer">数据源：<code>{esc(matrix_path)}</code> · 真实压测结果请使用正式套件，smoke 结果仅用于验证流程。</div>
</main></body></html>"""
    output_path.write_text(doc, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("matrix_json", type=Path)
    parser.add_argument("output_html", type=Path)
    args = parser.parse_args()
    render(args.matrix_json, args.output_html)
    print(args.output_html)


if __name__ == "__main__":
    main()
