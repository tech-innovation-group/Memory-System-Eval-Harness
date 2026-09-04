#!/usr/bin/env python3
"""Render a compact operator-facing HTML report from a stress summary.json."""

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


def num(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "-"


def stats(group: dict[str, Any]) -> str:
    return " / ".join(
        f"{label} {sec(group.get(key))}"
        for label, key in (
            ("均值", "mean_s"),
            ("P50", "p50_s"),
            ("P95", "p95_s"),
            ("P99", "p99_s"),
            ("最大", "max_s"),
        )
    )


def status_class(value: Any) -> str:
    return str(value or "UNKNOWN").lower().replace(" ", "_")


def render(summary: dict[str, Any]) -> str:
    details = summary.get("details") or {}
    metrics = summary.get("metrics") or {}
    params = summary.get("parameters") or {}
    commit = metrics.get("commit") or {}
    search = metrics.get("search") or {}
    completion = commit.get("completion") or {}
    search_latency = search.get("latency") or {}
    targets = metrics.get("targets") or {}
    fairness = metrics.get("fairness") or {}
    tenants = metrics.get("per_tenant") or {}
    isolation = details.get("isolation") or {}
    probes = isolation.get("probes") or []
    delayed = commit.get("delayed") or []
    status = str(summary.get("status") or "UNKNOWN").upper()
    duration = float(params.get("duration_s") or metrics.get("workload_duration_s") or 0)
    search_target = float(params.get("search_rps") or 0)
    search_configured = search_target * duration if search_target and duration else 0
    search_submitted = int(search.get("submitted") or 0)
    commit_submitted = int(commit.get("submitted") or 0)
    commit_completed = int(commit.get("completed") or 0)
    isolation_invalid = int(isolation.get("invalid_probe_count") or 0)
    cross_hits = sum(1 for p in probes if not p.get("same_tenant") and p.get("marker_found"))
    same_total = sum(1 for p in probes if p.get("same_tenant"))
    same_hits = sum(1 for p in probes if p.get("same_tenant") and p.get("marker_found"))
    server_coverage = f"{commit.get('server', {}).get('observed_count', 0)}/{commit.get('server', {}).get('total_count', 0)}"
    status_label = {"FAIL": "存在阻断问题", "PASS": "测试通过", "INCONCLUSIVE": "证据不足"}.get(status, status)
    status_note = (
        "性能请求成功，但租户隔离探针失败，不能按多租户服务上线。"
        if status == "FAIL" and isolation_invalid
        else "所有核心检查项已完成。"
        if status == "PASS"
        else "部分指标缺少足够证据，暂不能下结论。"
    )

    scenario_rows = []
    labels = {
        "commit_delivery": "Commit 完成",
        "search_priority": "Search 并发",
        "tenant_fairness": "租户公平",
        "tenant_isolation": "租户隔离",
        "resource_observation": "资源观测",
        "server_scheduling_observation": "服务端调度证据",
    }
    for key, value in (summary.get("scenario_status") or {}).items():
        css = status_class(value)
        icon = "✓" if css == "pass" else "!" if css in {"fail", "environment_error"} else "?"
        scenario_rows.append(
            f"<div class='check'><span class='check-icon {css}'>{icon}</span>"
            f"<span>{esc(labels.get(key, key))}</span><b class='pill {css}'>{esc(value)}</b></div>"
        )

    tenant_rows = []
    for tenant, data in sorted(tenants.items()):
        c = data.get("commit") or {}
        s = data.get("search") or {}
        tenant_rows.append(
            f"<tr><td><b>{esc(tenant)}</b></td>"
            f"<td>{c.get('completed', 0)}/{c.get('submitted', 0)}</td>"
            f"<td>{esc(stats(c.get('completion') or {}))}</td>"
            f"<td>{esc(stats(s.get('latency') or {}))}</td>"
            f"<td>{c.get('delayed_count', 0)} / {s.get('delayed_count', 0)}</td></tr>"
        )

    delayed_rows = "".join(
        f"<tr><td>{esc(item.get('tenant'))}</td><td>{esc(item.get('completed_at'))}</td>"
        f"<td>{sec(item.get('completion_s'))}</td><td>{sec(item.get('queue_wait_s'))}</td>"
        f"<td>{esc(item.get('request_id'))}</td></tr>"
        for item in delayed
    ) or "<tr><td colspan='5'>没有超过 10 秒的 Commit</td></tr>"

    probe_rows = "".join(
        f"<tr class='{'bad' if p.get('marker_found') != p.get('expected') else ''}'>"
        f"<td>{esc(p.get('writer'))}</td><td>{esc(p.get('reader'))}</td>"
        f"<td>{'命中' if p.get('marker_found') else '未命中'}</td>"
        f"<td>{'应命中' if p.get('expected') else '不应命中'}</td>"
        f"<td>{sec(p.get('latency_s'))}</td></tr>"
        for p in probes
    )

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EchoMem 压测报告</title>
<style>
:root{{--ink:#17212b;--muted:#6d7a87;--line:#e5eaee;--bg:#f4f6f7;--paper:#fff;
--green:#147a61;--green-bg:#e7f5ef;--red:#b43d3d;--red-bg:#fff0ef;--amber:#9a6a16;--amber-bg:#fff7df;--blue:#25669c}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
.page{{max-width:1120px;margin:auto;padding:28px 18px 56px}}.top{{display:flex;align-items:center;gap:13px;margin-bottom:16px}}
.logo{{width:46px;height:46px;flex:none}}h1{{font-size:24px;line-height:1.2;margin:0}}h2{{font-size:17px;margin:0 0 13px}}h3{{font-size:14px;margin:0 0 7px}}
.muted,small{{color:var(--muted)}}.top small{{display:block;margin-top:3px}}
.hero{{display:flex;justify-content:space-between;gap:22px;align-items:center;background:var(--paper);border:1px solid var(--line);border-left:5px solid var(--red);padding:19px 21px;margin-bottom:12px}}
.hero.pass{{border-left-color:var(--green)}}.hero.inconclusive{{border-left-color:var(--amber)}}.hero-title{{font-size:27px;font-weight:850;color:var(--red)}}.hero.pass .hero-title{{color:var(--green)}}.hero.inconclusive .hero-title{{color:var(--amber)}}.hero-meta{{text-align:right;color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:12px}}.card,.section{{background:var(--paper);border:1px solid var(--line)}}.card{{padding:14px 15px}}.label{{font-size:12px;color:var(--muted)}}.value{{font-size:23px;font-weight:850;margin-top:3px}}.note{{font-size:12px;color:var(--muted);margin-top:2px}}
.section{{padding:18px 19px;margin-top:12px}}.section-head{{display:flex;justify-content:space-between;gap:12px;align-items:baseline;margin-bottom:11px}}
.checks{{display:grid;grid-template-columns:repeat(3,1fr);gap:0 22px}}.check{{display:flex;align-items:center;gap:9px;border-top:1px solid var(--line);padding:10px 0}}.check-icon{{display:grid;place-items:center;width:25px;height:25px;border-radius:50%;font-weight:850;background:#eef1f3;color:var(--muted)}}.check-icon.pass{{background:var(--green-bg);color:var(--green)}}.check-icon.fail,.check-icon.environment_error{{background:var(--red-bg);color:var(--red)}}.check-icon.inconclusive{{background:var(--amber-bg);color:var(--amber)}}.pill{{margin-left:auto;border-radius:999px;padding:2px 8px;font-size:11px;background:#eef1f3;color:var(--muted)}}.pill.pass{{background:var(--green-bg);color:var(--green)}}.pill.fail,.pill.environment_error{{background:var(--red-bg);color:var(--red)}}.pill.inconclusive{{background:var(--amber-bg);color:var(--amber)}}
.alert{{padding:11px 13px;border-left:4px solid var(--red);background:var(--red-bg);margin:0 0 12px;color:#7b2929}}.alert.info{{border-left-color:var(--amber);background:var(--amber-bg);color:#6d5116}}
.facts{{display:grid;grid-template-columns:1fr 1fr;gap:0 34px}}.fact{{display:flex;justify-content:space-between;gap:18px;padding:8px 0;border-bottom:1px solid var(--line)}}.fact span:first-child{{color:var(--muted)}}.fact span:last-child{{font-weight:700;text-align:right;overflow-wrap:anywhere}}
.bars{{display:grid;gap:9px}}.bar-row{{display:grid;grid-template-columns:78px 1fr 70px;gap:9px;align-items:center}}.bar{{height:10px;background:#edf0f2;border-radius:99px;overflow:hidden}}.bar i{{display:block;height:100%;background:var(--blue);border-radius:99px}}.bar i.red{{background:var(--red)}}
table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:8px 7px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{color:var(--muted);font-weight:700;background:#fafbfc}}.scroll{{overflow:auto}}tr.bad td{{background:#fff7f6;color:#8c3030}}
details{{border-top:1px solid var(--line);padding-top:11px;margin-top:12px}}summary{{cursor:pointer;font-weight:750;color:#344454}}code{{font-size:12px;background:#eef2f4;padding:2px 5px}}.footer{{font-size:12px;color:var(--muted);margin-top:14px}}
@media(max-width:760px){{.hero{{display:block}}.hero-meta{{text-align:left;margin-top:10px}}.grid{{grid-template-columns:1fr 1fr}}.checks,.facts{{grid-template-columns:1fr}}.page{{padding:20px 12px 42px}}.bar-row{{grid-template-columns:70px 1fr 62px}}}}
</style></head><body><main class="page">
<header class="top"><svg class="logo" viewBox="0 0 52 52" role="img" aria-label="EchoMem">
<path d="M26 3 47 14.5v23L26 49 5 37.5v-23z" fill="#e7f5ef" stroke="#147a61" stroke-width="2.5"/>
<path d="m12 18 14 8 14-8M26 26v15M18 22.5v9l8 4.5 8-4.5v-9" fill="none" stroke="#147a61" stroke-width="2.5" stroke-linejoin="round"/>
<circle cx="26" cy="13" r="3" fill="#147a61"/></svg>
<div><h1>EchoMem 真实服务压测报告</h1><small>PR397 故障发现方案 · {esc(summary.get("finished_at"))}</small></div></header>
<section class="hero {status_class(status)}"><div><div class="label">总体判定</div><div class="hero-title">{esc(status_label)}</div><div class="muted">{esc(status_note)}</div></div>
<div class="hero-meta"><code>{esc(summary.get("base_url"))}</code><br>{num(duration, 0)} 秒 · {num(search_target, 1)} RPS · {esc(params.get("tenants"))} 个租户</div></section>
<section class="grid">
<div class="card"><div class="label">Commit 完成</div><div class="value">{commit_completed}/{commit_submitted}</div><div class="note">延迟超过 10 秒：{commit.get("delayed_count", 0)} 次</div></div>
<div class="card"><div class="label">Commit P95 / 最大</div><div class="value">{sec(completion.get("p95_s"))}</div><div class="note">最大 {sec(completion.get("max_s"))}</div></div>
<div class="card"><div class="label">Search 成功</div><div class="value">{search.get("succeeded", 0)}/{search_submitted}</div><div class="note">配置负载 {num(search_target, 1)} RPS</div></div>
<div class="card"><div class="label">Search P95 / 最大</div><div class="value">{sec(search_latency.get("p95_s"))}</div><div class="note">最大 {sec(search_latency.get("max_s"))}</div></div>
</section>
<section class="section"><div class="section-head"><h2>结果摘要</h2><small>绿色是通过，红色是阻断，黄色是证据不足</small></div><div class="checks">{''.join(scenario_rows)}</div></section>
<section class="section"><h2>本轮发生了什么</h2><div class="alert">租户隔离失败：80 个探针中有 {isolation_invalid} 个异常，其中跨租户误命中 {cross_hits} 次；同租户命中 {same_hits}/{same_total}。这不是普通性能波动，而是数据边界问题。</div>
<div class="facts"><div class="fact"><span>Commit 延迟分布</span><span>{esc(stats(completion))}</span></div><div class="fact"><span>Search 延迟分布</span><span>{esc(stats(search_latency))}</span></div>
<div class="fact"><span>延迟发生时间</span><span>第二轮 Commit 同时进入：2026-08-27 01:40:51 UTC</span></div><div class="fact"><span>客户端排队</span><span>{sec((commit.get("queue_wait") or {}).get("mean_s"))}（平均）</span></div></div></section>
<section class="section"><h2>负载是否按计划完成</h2><div class="facts"><div class="fact"><span>Search 目标请求数</span><span>{num(search_configured, 0)}</span></div><div class="fact"><span>Search 实际请求数</span><span>{search_submitted} / {num(search_configured, 0)}</span></div><div class="fact"><span>Commit 实际请求数</span><span>{commit_submitted}</span></div><div class="fact"><span>调度迟到最大值</span><span>{sec(search.get("max_schedule_lateness_s"))} Search · {sec(commit.get("max_schedule_lateness_s"))} Commit</span></div></div></section>
<section class="section"><h2>租户对比</h2><div class="scroll"><table><thead><tr><th>租户</th><th>Commit</th><th>Commit 延迟统计（均值 / P50 / P95 / P99 / 最大）</th><th>Search 延迟统计</th><th>超阈值（Commit / Search）</th></tr></thead><tbody>{''.join(tenant_rows)}</tbody></table></div>
<div class="bars" style="margin-top:14px">{''.join(f"<div class='bar-row'><span>{esc(t)}</span><div class='bar'><i class='red' style='width:{min(100, float((d.get('commit') or {}).get('completion', {}).get('p95_s') or 0) / max(1, float(completion.get('p95_s') or 1)) * 100):.1f}%'></i></div><b>{sec((d.get('commit') or {}).get('completion', {}).get('p95_s'))}</b></div>" for t,d in sorted(tenants.items()))}</div></section>
<section class="section"><h2>服务端证据</h2><div class="alert info">当前只能确认客户端端到端耗时。服务端没有返回逐请求队列/执行时间，因此不能仅凭本报告判断 FIFO、Search 优先或双通道。</div>
<div class="facts"><div class="fact"><span>/metrics 样本</span><span>{esc(details.get("server_metrics_samples"))}</span></div><div class="fact"><span>服务端时序覆盖</span><span>Commit {server_coverage}，Search {search.get("server", {}).get("observed_count", 0)}/{search.get("server", {}).get("total_count", 0)}</span></div><div class="fact"><span>HTTP 429</span><span>{commit.get("rate_limited_count", 0)} Commit · {search.get("rate_limited_count", 0)} Search</span></div><div class="fact"><span>公平性</span><span>Commit P95 {num(fairness.get("commit_completion_p95_max_min_ratio"))} 倍，Search P95 {num(fairness.get("search_latency_p95_max_min_ratio"))} 倍</span></div></div></section>
<section class="section"><h2>重点明细</h2><details><summary>展开 {len(delayed)} 个慢 Commit</summary><div class="scroll"><table><thead><tr><th>租户</th><th>完成时间</th><th>端到端耗时</th><th>客户端队列</th><th>请求 ID</th></tr></thead><tbody>{delayed_rows}</tbody></table></div></details>
<details><summary>展开全部 {len(probes)} 个租户隔离探针</summary><div class="scroll"><table><thead><tr><th>写入租户</th><th>读取租户</th><th>实际</th><th>期望</th><th>耗时</th></tr></thead><tbody>{probe_rows}</tbody></table></div></details></section>
<div class="footer">原始 CSV、summary.json 和服务端 metrics 保存在同一结果目录。报告明确区分了客户端调度证据与服务端调度证据。</div>
</main></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("summary_json", type=Path)
    parser.add_argument("output_html", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.summary_json.read_text(encoding="utf-8"))
    args.output_html.write_text(render(payload.get("summary") or payload), encoding="utf-8")
    print(args.output_html)


if __name__ == "__main__":
    main()
