#!/usr/bin/env python3
"""Render a compact, conclusion-first HTML report for a stress matrix."""

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


def status_class(value: Any) -> str:
    return str(value or "UNKNOWN").lower().replace(" ", "_")


def metric(summary: dict[str, Any], operation: str, group: str, key: str) -> Any:
    return (
        (((summary.get("metrics") or {}).get(operation) or {}).get(group) or {}).get(key)
    )


def render(matrix_path: Path, output_path: Path) -> None:
    payload = json.loads(matrix_path.read_text(encoding="utf-8"))
    summaries = payload.get("summaries") or []
    if not summaries:
        raise SystemExit(f"no summaries in {matrix_path}")

    first = summaries[0]
    first_params = first.get("parameters") or {}
    first_details = first.get("details") or {}
    identity_mode = first_details.get("identity_mode", "unknown")
    real_multi = identity_mode == "independent_auth_keys" and int(first_params.get("tenants") or 0) > 1
    pass_count = sum(str(s.get("status")) == "PASS" for s in summaries)
    inconclusive_count = sum(str(s.get("status")) == "INCONCLUSIVE" for s in summaries)

    chart_max = max(
        [
            float(metric(s, "commit", "completion", "p95_s") or 0)
            for s in summaries
        ]
        + [
            float(metric(s, "search", "latency", "p95_s") or 0)
            for s in summaries
        ]
        + [1.0]
    )
    descriptions = {
        "fifo": "单一队列，按到达顺序处理",
        "search-priority": "Search 优先，观察 Commit 是否被挤压",
        "dual-lane": "Search 与 Commit 分离通道",
        "tenant-fair": "按租户轮询，租户内保持 FIFO",
        "dual-lane-tenant-fair": "双通道 + 租户公平轮询",
    }

    rows: list[str] = []
    charts: list[str] = []
    details: list[str] = []
    for summary in summaries:
        params = summary.get("parameters") or {}
        policy = str(params.get("scheduler_policy") or "unknown")
        commit = (summary.get("metrics") or {}).get("commit") or {}
        search = (summary.get("metrics") or {}).get("search") or {}
        admission = (summary.get("metrics") or {}).get("admission") or {}
        c_p95 = float(metric(summary, "commit", "completion", "p95_s") or 0)
        s_p95 = float(metric(summary, "search", "latency", "p95_s") or 0)
        commit_ok = f"{commit.get('completed', 0)}/{commit.get('submitted', 0)}"
        search_ok = f"{search.get('succeeded', 0)}/{search.get('submitted', 0)}"
        status = str(summary.get("status") or "UNKNOWN")
        status_css = status_class(status)
        rows.append(
            f"<tr><td><b>{esc(policy)}</b><small>{esc(descriptions.get(policy, ''))}</small></td>"
            f"<td><span class='badge {status_css}'>{esc(status)}</span></td>"
            f"<td>{commit_ok}</td><td>{seconds(metric(summary, 'commit', 'completion', 'mean_s'))}</td>"
            f"<td><b>{seconds(c_p95)}</b></td><td>{search_ok}</td>"
            f"<td>{seconds(metric(summary, 'search', 'latency', 'mean_s'))}</td>"
            f"<td><b>{seconds(s_p95)}</b></td><td>{esc(admission.get('max_queue_depth', 0))}</td></tr>"
        )
        charts.append(
            f"<div class='bar-row'><span>{esc(policy)}</span><div class='bar-track'>"
            f"<i class='bar commit' style='width:{c_p95 / chart_max * 100:.1f}%'></i>"
            f"<i class='bar search' style='width:{s_p95 / chart_max * 100:.1f}%'></i></div>"
            f"<b>{c_p95:.2f}s / {s_p95:.2f}s</b></div>"
        )

        tenant_rows: list[str] = []
        for tenant, data in sorted(((summary.get("metrics") or {}).get("per_tenant") or {}).items()):
            c = (data.get("commit") or {}).get("completion") or {}
            s = (data.get("search") or {}).get("latency") or {}
            tenant_rows.append(
                f"<tr><td>{esc(tenant)}</td><td>{esc((data.get('commit') or {}).get('submitted', 0))}/"
                f"{esc((data.get('commit') or {}).get('completed', 0))}</td>"
                f"<td>{seconds(c.get('mean_s'))}</td><td>{seconds(c.get('p95_s'))}</td>"
                f"<td>{esc((data.get('search') or {}).get('submitted', 0))}/"
                f"{esc((data.get('search') or {}).get('succeeded', 0))}</td>"
                f"<td>{seconds(s.get('mean_s'))}</td><td>{seconds(s.get('p95_s'))}</td></tr>"
            )
        isolation = (summary.get("details") or {}).get("isolation") or {}
        policy_dir = matrix_path.parent / policy
        links = []
        for name in (
            "report.html",
            "summary.json",
            "commit_results.csv",
            "search_results.csv",
            "resource_samples.csv",
            "server_metrics.csv",
        ):
            if (policy_dir / name).exists():
                links.append(f"<a href='{esc(policy + '/' + name)}'>{esc(name)}</a>")
        details.append(
            f"<details><summary><b>{esc(policy)}</b> · {esc(status)} · "
            f"Commit P95 {seconds(c_p95)} · Search P95 {seconds(s_p95)}</summary>"
            f"<div class='detail-grid'><div><h3>逐租户统计</h3><div class='scroll'><table>"
            f"<thead><tr><th>租户</th><th>Commit 完成</th><th>Commit 平均</th><th>Commit P95</th>"
            f"<th>Search 成功</th><th>Search 平均</th><th>Search P95</th></tr></thead>"
            f"<tbody>{''.join(tenant_rows) or '<tr><td colspan=7>无逐租户数据</td></tr>'}</tbody></table></div></div>"
            f"<div><h3>隔离与原始文件</h3><p>隔离：<b>{esc(isolation.get('status', '未提供'))}</b>，"
            f"{esc(isolation.get('probe_count', 0))}/{esc(isolation.get('expected_probe_count', 0))} 条探针，"
            f"异常 {esc(isolation.get('invalid_probe_count', 0))} 条。</p>"
            f"<p class='links'>{' · '.join(links) or '原始文件不存在'}</p></div></div></details>"
        )

    favicon = (
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E"
        "%3Crect width='64' height='64' rx='14' fill='%2317324d'/%3E"
        "%3Cpath d='M14 48V35M25 48V24M36 48V30M47 48V16' stroke='%2372d5b7' stroke-width='5' stroke-linecap='round'/%3E"
        "%3Cpath d='M10 52h44M12 20l10-7 10 8 16-12' fill='none' stroke='%23ff9d6e' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'/%3E"
        "%3C/svg%3E"
    )
    identity_title = "真实多租户" if real_multi else "性能对照，不能作为隔离结论"
    identity_note = (
        "本次使用独立认证身份，可用于租户隔离分析。"
        if real_multi
        else "本次租户共用认证身份，只能观察压测端性能。"
    )
    html_doc = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="{favicon}"><title>EchoMem 压测报告</title>
<style>
:root{{--bg:#f3f6f7;--paper:#fff;--ink:#17212b;--muted:#6d7b87;--line:#dce4e8;--green:#177b63;--green-bg:#e8f6f0;--orange:#e77f56;--amber:#9b6b16;--amber-bg:#fff7df;--red:#b6403b}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
.page{{max-width:1180px;margin:auto;padding:28px 20px 56px}}.top{{display:flex;align-items:center;gap:13px;margin-bottom:18px}}
.logo{{width:50px;height:50px;flex:none}}h1{{margin:0;font-size:25px;line-height:1.15}}h2{{margin:0;font-size:18px}}h3{{font-size:14px;margin:0 0 8px}}small,.muted{{color:var(--muted);display:block}}
.hero,.section,.card{{background:var(--paper);border:1px solid var(--line)}}.hero{{padding:18px 20px;border-left:5px solid var(--green);display:flex;justify-content:space-between;gap:20px}}
.hero.warning{{border-left-color:var(--amber);background:var(--amber-bg)}}.hero strong{{font-size:18px}}.hero-meta{{text-align:right;color:var(--muted)}}
.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:12px 0}}.card{{padding:14px 15px}}.label{{color:var(--muted);font-size:12px}}.value{{font-size:23px;font-weight:800;margin-top:3px}}
.section{{padding:18px 19px;margin-top:12px}}.section-head{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:12px}}
.chart-legend{{color:var(--muted);font-size:12px;margin-bottom:12px}}.dot{{display:inline-block;width:9px;height:9px;border-radius:2px;margin:0 4px 0 12px}}.dot:first-child{{margin-left:0}}.dot.commit{{background:var(--green)}}.dot.search{{background:var(--orange)}}
.bar-row{{display:grid;grid-template-columns:180px 1fr 100px;align-items:center;gap:10px;margin:11px 0}}.bar-row>span{{font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.bar-track{{height:17px;background:#edf1f3;border-radius:3px;position:relative;overflow:hidden}}.bar{{display:block;height:8px;position:absolute;left:0;border-radius:3px}}.bar.commit{{top:1px;background:var(--green)}}.bar.search{{top:9px;background:var(--orange)}}.bar-row>b{{font-size:12px;text-align:right}}
.scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:9px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;white-space:nowrap}}th{{background:#fafbfc;color:var(--muted)}}td small{{white-space:normal;max-width:230px;margin-top:2px}}
.badge{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700;background:#eef1f3;color:var(--muted)}}.badge.pass{{background:var(--green-bg);color:var(--green)}}.badge.inconclusive{{background:var(--amber-bg);color:var(--amber)}}.badge.fail,.badge.environment_error{{background:#fff0ee;color:var(--red)}}
details{{border-top:1px solid var(--line);padding:11px 0}}details:first-of-type{{border-top:0}}summary{{cursor:pointer;color:#344454}}.detail-grid{{display:grid;grid-template-columns:1.3fr 1fr;gap:20px;padding:12px 0 4px}}.links{{line-height:2}}a{{color:#286aa6}}code{{background:#eef2f4;padding:2px 5px;font-size:12px}}.footer{{color:var(--muted);font-size:12px;margin-top:14px}}
@media(max-width:760px){{.page{{padding:20px 12px 42px}}.hero{{display:block}}.hero-meta{{text-align:left;margin-top:10px}}.cards{{grid-template-columns:1fr 1fr}}.bar-row{{grid-template-columns:120px 1fr 82px}}.detail-grid{{grid-template-columns:1fr}}}}
</style></head><body><main class="page">
<header class="top"><svg class="logo" viewBox="0 0 56 56" role="img" aria-label="EchoMem 压测报告">
<rect x="3" y="3" width="50" height="50" rx="13" fill="#17324d"/><path d="M13 40V29M22 40V20M31 40V25M40 40V13" stroke="#72d5b7" stroke-width="4" stroke-linecap="round"/><path d="M11 44h34M12 17l8-5 8 6 12-9" fill="none" stroke="#ff9d6e" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
<div><h1>EchoMem 压测报告</h1><small>策略矩阵 · 结论优先，详细证据可展开 · {esc(first.get('finished_at'))}</small></div></header>
<section class="hero {' ' if real_multi else 'warning'}"><div><div class="label">证据范围</div><strong>{identity_title}</strong><div>{identity_note}</div></div>
<div class="hero-meta">目标 <code>{esc(first.get('base_url'))}</code><br>{esc(first_params.get('tenants'))} 个租户 · {esc(first_params.get('duration_s'))} 秒 · Search {esc(first_params.get('search_rps'))} RPS</div></section>
<section class="cards">
<div class="card"><div class="label">策略数</div><div class="value">{len(summaries)}</div><small>{pass_count} 个通过 · {inconclusive_count} 个样本不足</small></div>
<div class="card"><div class="label">隔离探针</div><div class="value">{'PASS' if real_multi and (first_details.get('isolation') or {}).get('status') == 'PASS' else '需谨慎'}</div><small>{esc((first_details.get('isolation') or {}).get('probe_count', 0))} / {esc((first_details.get('isolation') or {}).get('expected_probe_count', 0))} 条</small></div>
<div class="card"><div class="label">最佳 Commit P95</div><div class="value">{seconds(min(float(metric(s, 'commit', 'completion', 'p95_s') or 1e9) for s in summaries))}</div><small>策略矩阵最低值</small></div>
<div class="card"><div class="label">最佳 Search P95</div><div class="value">{seconds(min(float(metric(s, 'search', 'latency', 'p95_s') or 1e9) for s in summaries))}</div><small>策略矩阵最低值</small></div>
</section>
<section class="section"><div class="section-head"><h2>延迟对比</h2><span class="muted">每行：Commit P95 / Search P95，越低越好</span></div>
<div class="chart-legend"><i class="dot commit"></i>Commit P95 <i class="dot search"></i>Search P95</div>{''.join(charts)}</section>
<section class="section"><div class="section-head"><h2>策略总览</h2><span class="muted">成功数 / 提交数 · P95 · 最大准入队列</span></div>
<div class="scroll"><table><thead><tr><th>策略 / 含义</th><th>状态</th><th>Commit 完成</th><th>Commit 平均</th><th>Commit P95</th><th>Search 成功</th><th>Search 平均</th><th>Search P95</th><th>最大队列</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>
<section class="section"><div class="section-head"><h2>详细证据</h2><span class="muted">默认收起，避免首页信息过载</span></div>{''.join(details)}</section>
<div class="footer">原始矩阵：<code>{esc(matrix_path)}</code> · 本报告不隐藏失败请求，完整请求记录位于各策略目录 CSV。</div>
</main></body></html>"""
    output_path.write_text(html_doc, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("matrix_json", type=Path)
    parser.add_argument("output_html", type=Path)
    args = parser.parse_args()
    render(args.matrix_json, args.output_html)
    print(args.output_html)


if __name__ == "__main__":
    main()
