"""Publish an allowlisted, shareable first report from private main-metric samples."""

from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path

from performance.targets.echomem.acceptance.capacity_observation_report import render_observation


def recovery_counts(value: dict) -> dict:
    checks = value.get("checks", [])
    details = {}
    for check in checks:
        try:
            details[check["name"]] = json.loads(check.get("detail") or "{}")
        except (TypeError, ValueError):
            details[check["name"]] = {}
    operation = details.get("commit-recovery", {})
    messages = details.get("message-reconciliation", {})
    replay = details.get("idempotency-replay", {})
    expected = messages.get("expected_server_message_ids", [])
    return {"checks": [{"name": c["name"], "status": c["status"]} for c in checks],
            "elapsed_s": value.get("elapsed_s"), "status": value.get("status"),
            "accepted_202": operation.get("accepted_202"),
            "autonomous_completed": operation.get("autonomous_recovery_observed"),
            "expected_messages": len(expected) if expected else None,
            "missing_messages": len(messages.get("missing_server_message_ids", [])) if expected else None,
            "complete_sources": messages.get("complete_sources", []),
            "same_archive": replay.get("same_archive"), "replayed": replay.get("replayed")}


def redacted_report(source: dict, capacity: dict) -> dict:
    metrics = source.get("metrics", {})
    observability = metrics.get("M6", {})
    tenants = observability.get("expected_tenants", [])
    labels = {tenant: f"T{i + 1}" for i, tenant in enumerate(tenants)}
    fields = ("lane", "queued", "wait_seconds_total", "exec_seconds_total", "rejected_total",
              "accepted_total", "completed_total", "failed_total", "accepted_delta", "queued_peak_during_load")
    m6 = {"status": observability.get("status"), "sample_count": observability.get("sample_count"),
          "expected_cells": len(tenants) * len(observability.get("expected_lanes", [])),
          "expected_lanes": observability.get("expected_lanes", []),
          "rows": [{"tenant": labels.get(r.get("tenant_id"), "unknown"),
                    **{k: r.get(k) for k in fields}} for r in observability.get("rows", [])],
          "missing_cells": len(observability.get("missing", [])),
          "invalid_cells": len(observability.get("invalid", []))}
    levels = []
    for level in capacity.get("levels", []):
        if "search" in level:
            levels.append(level)
        else:
            for key in ("pure_aggregate", "mixed_aggregate"):
                if level.get(key):
                    levels.append({**level[key], "hot_users": level["hot_users"]})
    return {"status": "INITIAL_OBSERVATIONS", "performance_requirements_applied": False,
            "publication": {"redacted": True, "raw_requests_exported": False, "private_identities_exported": False},
            "platform_base_pr": 31, "current_phase": source.get("current"),
            "duration_s": source.get("duration_s"), "per_tenant_search_rps": source.get("per_tenant_search_rps"),
            "environment": capacity.get("manifest", {}),
            "M1": {"levels": levels, "max_hot_users": None, "max_dau": None,
                   "operational_boundary": capacity.get("operational_boundary")},
            "M2": metrics.get("M2", {}), "M3_M4": metrics.get("M3_M4", {}),
            "M5": recovery_counts(metrics.get("M5", {})), "M6": m6}


def render(report: dict) -> str:
    def fmt(value):
        if value is None:
            return "未采集"
        if isinstance(value, bool):
            return "是" if value else "否"
        return f"{value:.3f}" if isinstance(value, float) else escape(str(value))

    def table(headers, rows):
        return '<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{escape(h)}</th>' for h in headers) + '</tr></thead><tbody>' + (
            ''.join('<tr>' + ''.join(f'<td>{fmt(v)}</td>' for v in row) + '</tr>' for row in rows)
            or f'<tr><td colspan="{len(headers)}">尚未采集到数据</td></tr>') + '</tbody></table></div>'

    def bar(label, value, maximum, color):
        width = 100 * max(0, value or 0) / max(maximum, .001)
        return f'<div class="bar"><span>{escape(label)}</span><div><i style="width:{min(100,width):.2f}%;background:{color}"></i></div><b>{fmt(value)}</b></div>'

    levels = report["M1"]["levels"]
    highest = max((r.get("hot_users", 0) for r in levels), default=None)
    m2, joint, m5, m6 = (report[k] for k in ("M2", "M3_M4", "M5", "M6"))
    bystanders = [p for p in m2.get("pairs", []) if p["identity_index"] != m2.get("target_index")]
    degradation = [p["p95_degradation_percent"] for p in bystanders if p.get("p95_degradation_percent") is not None]
    worst = max(degradation, default=None)
    p95 = joint.get("overlap_search", {}).get("p95_s")
    cards = [("M1 最高已测热用户", highest), ("M2 最差旁观 P95 劣化 %", worst),
             ("M3 Commit Jain", joint.get("commit_jain")), ("M4 积压重叠 Search P95 / s", p95),
             ("M5 202 自主恢复", m5.get("autonomous_completed")),
             ("M6 四元组单元", f"{len(m6['rows'])}/{m6['expected_cells']}")]
    capacity_rows = [[r.get("hot_users"), "混合" if r.get("mixed") else "纯召回", r["search"].get("p95_s"),
        r.get("effective_search_rps"), r["search"].get("transport_or_http_errors"),
        f"{r['search']['success']}/{r['search']['sent']}",
        r.get("resource_summary", {}).get("cpu_peak_percent_one_core_100"),
        (r.get("resource_summary", {}).get("rss_peak_bytes") or 0) / 1048576
        if r.get("resource_summary", {}).get("rss_peak_bytes") is not None else None] for r in levels]
    fault_rows = [[f"T{p['identity_index']+1}", p["before"].get("sent"), p["during"].get("sent"),
        p["before"].get("p95_s"), p["during"].get("p95_s"), p.get("p95_degradation_percent"),
        p["during"].get("transport_or_http_errors")] for p in m2.get("pairs", [])]
    fairness_rows = [[f"T{t['identity_index']+1}", t["commit_completed_in_search_window"], t["commit_rps"],
        t["search"].get("sent"), t["search"].get("p95_s"), t["search"].get("quality_rate")]
        for t in joint.get("tenants", [])]
    priority_rows = [[f"T{p['identity_index']+1}", p["before"].get("p95_s"), p["during"].get("p95_s"),
        p.get("p95_degradation_percent"), p["during"].get("sent"), p["during"].get("transport_or_http_errors")]
        for p in joint.get("paired", [])]
    observable_rows = [[r["tenant"],r["lane"],r["queued_peak_during_load"],r["queued"],r["wait_seconds_total"],
        r["exec_seconds_total"],r["rejected_total"],r["accepted_delta"]] for r in m6["rows"]]
    curves = ''.join(bar(f"H={r.get('hot_users')} {'混合' if r.get('mixed') else '召回'}", r["search"].get("p95_s"),
        max((x["search"].get("p95_s") or 0 for x in levels), default=1), "#327d9d") for r in levels)
    env = report.get("environment", {})
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>4U8G 六项指标 · 初版实测</title><style>*{{box-sizing:border-box}}body{{margin:0;color:#24323a;background:#f6f8f9;font:15px/1.7 system-ui,"PingFang SC",sans-serif;letter-spacing:0}}main{{max-width:1260px;margin:auto;padding:28px}}h1{{font-size:28px}}h2{{font-size:21px}}h3{{font-size:17px}}header,section{{padding:20px 0;border-bottom:1px solid #ccd8dc}}.muted{{color:#576b74}}.notice{{border-left:4px solid #ad4637;padding:8px 16px;background:#fff1ec}}.stats{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px;margin:24px 0}}.stats div{{border-top:3px solid #278575;padding-top:12px}}.stats b{{display:block;font-size:27px}}.scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;background:white;font-size:13px}}th,td{{padding:10px;border-bottom:1px solid #d7e0e5;text-align:left}}th{{background:#e6eef1;white-space:nowrap}}td{{overflow-wrap:anywhere}}code{{overflow-wrap:anywhere}}.bar{{display:grid;grid-template-columns:120px minmax(0,1fr) 60px;gap:12px;align-items:center;margin:10px 0}}.bar>div{{height:14px;background:#dae3e7}}.bar i{{display:block;height:100%}}.bar b{{text-align:right}}a{{color:#16699b}}@media(max-width:700px){{main{{padding:14px}}.stats{{grid-template-columns:repeat(2,minmax(0,1fr))}}h1{{font-size:24px}}.bar{{grid-template-columns:92px minmax(0,1fr) 56px;font-size:13px}}}}</style></head><body><main>
<header><p class="muted">服务器真实 HTTP / 真实模型 / 4 CPU · 8 GiB / PR31 基线</p><h1>六项性能指标：初版关键数据</h1>
<p>先给出已测数值，不设性能合格线。此页是短测观察，不代表完整故障矩阵、全天 DAU 或长期可靠性验收。</p>
<p>EchoMem <code>{fmt(env.get('echomem_commit'))}</code>；平台基线 <code>{fmt(env.get('platform_base_commit'))}</code>，另加本次未提交压测修改。</p>
<p class="notice">版本口径：容量详情保留各档 platform_base_pr / platform_base_commit；缺失表示历史快照未记录该字段，不能称所有档位均已重跑本版本。容器4U8G是资源限额，不代表宿主机资源独占。</p></header>
<div class="stats">{''.join('<div>'+escape(label)+'<b>'+fmt(value)+'</b></div>' for label,value in cards)}</div>
<section><h2>M1 · 热用户与 DAU</h2>{curves}{table(['H','负载','P95 s','有效 Search/s','HTTP/传输错误','严格有效/发出','CPU峰值 %','RSS峰值 MiB'],capacity_rows)}
<p>100% CPU 表示一个 CPU 核。最高已测档位不是最大用户量；HTTP 429、超时、召回降级全部保留。最大 DAU 尚未验证，画像换算及每题数据见 <a href="capacity-report.html">容量详细报告</a>。</p></section>
<section><h2>M2 · 单租户故障隔离</h2><p>只对 T1 注入 reject，T2–T4 使用同样的真实召回流量；每组 {fmt(report.get('duration_s'))} 秒，劣化=(故障中P95/基线P95−1)×100%。</p>
{table(['租户','基线样本','故障中样本','基线P95 s','故障中P95 s','变化 %','故障中HTTP错误'],fault_rows)}
<p>基线全部严格有效：{fmt(m2.get('baseline_strict_valid'))}；故障窗口覆盖：{fmt(m2.get('fault_window_covered'))}。这里只覆盖一个租户的一次 reject，不代表任意租户与慢模型故障均已验证。</p></section>
<section><h2>M3 · 等权租户公平性</h2><p>四个不同租户，各准备 8 个独立写会话，同一窗口并发 Search 和 Commit；Search Jain 使用各租户 P95 的倒数，Commit Jain 使用窗口内 completed/秒。</p>
{table(['租户','窗口内Commit完成','Commit/s','Search样本','Search P95 s','严格有效率'],fairness_rows)}
{bar('Commit Jain',joint.get('commit_jain'),1,'#258875')}{bar('Search Jain',joint.get('search_inverse_p95_jain'),1,'#327d9d')}
<p>Jain 接近 1 表示相对均匀，不代表快或可靠；零完成租户保留，全部零完成时指数留空。窗口后的排空完成不混入窗口吞吐；此为洪泛窗口观察，不是长时间稳态公平性结论。</p></section>
<section><h2>M4 · Commit 洪泛下的 Search</h2><p>受理 202：{fmt(joint.get('accepted_202'))}/{fmt(joint.get('commit_planned'))}；含排空完成：{fmt(joint.get('completed_including_drain'))}；排空后仍未终态：{fmt(joint.get('pending_after_drain'))}。</p>
{table(['租户','无Commit P95 s','洪泛窗口P95 s','变化 %','洪泛Search样本','HTTP错误'],priority_rows)}
<p>真实 Commit 积压重叠 Search：{fmt(joint.get('overlap_search',{}).get('sent'))} 个，P95={fmt(p95)} 秒。全窗口与积压重叠窗口分开，不将没有后台任务的快速样本充作优先级证据；内部严格调度顺序尚未证明。</p></section>
<section><h2>M5 · 202 后崩溃恢复</h2>{table(['观测项','结果'],[['崩溃前已受理202',m5.get('accepted_202')],['原任务自主completed',m5.get('autonomous_completed')],['源消息数',m5.get('expected_messages')],['未对账消息数',m5.get('missing_messages')],['重试是否同archive',m5.get('same_archive')],['总耗时 s',m5.get('elapsed_s')]])}
{table(['行为检查','结果'],[[c['name'],c['status']] for c in m5.get('checks',[])])}
<p>对专用容器执行一次真实 kill-9/start，等待原任务恢复后才做幂等重试；小样本即使通过，也不能推导所有崩溃时机均 100% 可靠。因未受理、已提前完成或其他任务未排空而没有重启时，不冒充恢复成功。</p></section>
<section><h2>M6 · 每租户四元组</h2><p>负载中采样 {fmt(m6.get('sample_count'))} 次；本轮期望 {fmt(m6['expected_cells'])} 个 tenant×lane 单元，观察到 {len(m6['rows'])} 个。模块：{escape(', '.join(m6['expected_lanes']))}。</p>
{table(['租户','层/模块','队列峰值','最终队列','累计等待 s','累计执行 s','累计拒绝','受理增量'],observable_rows)}
<p>累计时间不是单次请求延迟；缺失不能填零。这里只证明上表模块的观测，其他启用层和完整调度顺序仍需补充。</p></section>
<section><h2>下一步缺口</h2>{table(['归属','待迭代'],[['测试平台','更高用户档和实际故障边界；同租户多用户、更多记忆量与业务DAU画像。'],['测试平台','轮换故障租户与delay依赖、重复样本；更长稳态公平性；多崩溃时机。'],['EchoMem / 观测接口','核对所有启用层是否具备逐租户四元组和可证明调度先后的事件；仅凭延迟不推断内部实现。'],['模块归因','429、模型降级、请求超时、Commit失败分别保留证据；不能仅凭CPU/内存未满判断代码没有瓶颈。']])}</section>
<p><a href="report.json">脱敏统计 JSON</a> · 原始请求、身份凭据和故障详情保留执行机，不包含于分享文件。</p></main></body></html>'''


def publish(source: Path, capacity: Path, output: Path, recovery: Path | None = None) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    raw = json.loads(source.read_text(encoding="utf-8"))
    if recovery:
        raw.setdefault("metrics", {})["M5"] = json.loads(recovery.read_text(encoding="utf-8"))
    capacity_data = json.loads(capacity.read_text(encoding="utf-8"))
    public = redacted_report(raw, capacity_data)
    (output / "report.json").write_text(json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "report.html").write_text(render(public), encoding="utf-8")
    (output / "capacity-report.html").write_text(render_observation(capacity_data).replace('href="report.json"', 'href="capacity-report.json"'), encoding="utf-8")
    (output / "capacity-report.json").write_text(json.dumps(capacity_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return public


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--capacity", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--recovery", type=Path, help="Optional separate crash sample; original run remains immutable")
    args = parser.parse_args()
    result = publish(args.source, args.capacity, args.output, recovery=args.recovery)
    print(json.dumps({"status": result["status"], "redacted": True}))


if __name__ == "__main__":
    main()
