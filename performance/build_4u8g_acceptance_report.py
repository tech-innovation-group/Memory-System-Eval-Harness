#!/usr/bin/env python3
"""Build a compact, evidence-first 4U8G six-objective HTML report.

The report intentionally keeps PASS/FAIL/INCONCLUSIVE separate from raw
observations. It consumes existing formal-suite artifacts and does not make
additional requests to EchoMem.
"""

from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path
from typing import Any


PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def esc(value: Any) -> str:
    return html.escape(str(value if value not in (None, "") else "-"))


def num(value: Any, digits: int = 2) -> str:
    if value in (None, ""):
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return esc(value)


def pct(value: Any, digits: int = 1) -> str:
    if value in (None, ""):
        return "-"
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return esc(value)


def jain(values: list[float]) -> float | None:
    positive = [float(value) for value in values if math.isfinite(float(value))]
    if not positive:
        return None
    denominator = len(positive) * sum(value * value for value in positive)
    return (sum(positive) ** 2 / denominator) if denominator else None


def link(path: Path, label: str | None = None) -> str:
    return f'<a href="{html.escape(path.as_uri())}">{esc(label or path.name)}</a>'


def chart_bars(items: list[tuple[str, float | None]], unit: str = "ms") -> str:
    usable = [(label, value) for label, value in items if value is not None]
    if not usable:
        return '<div class="empty">没有可绘制数据</div>'
    max_value = max(value for _, value in usable) or 1.0
    rows = []
    for label, value in usable:
        width = max(2.0, min(100.0, value / max_value * 100.0))
        rows.append(
            f'<div class="bar-row"><span class="bar-label">{esc(label)}</span>'
            f'<span class="bar-track"><span class="bar-fill" style="width:{width:.2f}%"></span></span>'
            f'<b>{num(value)} {esc(unit)}</b></div>'
        )
    return "".join(rows)


def load_formal_runs(suite_path: Path) -> list[dict[str, Any]]:
    suite = read_json(suite_path, {})
    runs = suite.get("runs") if isinstance(suite, dict) else []
    return [item for item in runs if isinstance(item, dict)]


def run_record(run: dict[str, Any]) -> dict[str, Any]:
    summary = run.get("summary") if isinstance(run.get("summary"), dict) else {}
    metrics = summary.get("metrics") if isinstance(summary.get("metrics"), dict) else {}
    details = summary.get("details") if isinstance(summary.get("details"), dict) else {}
    search = metrics.get("search") if isinstance(metrics.get("search"), dict) else {}
    commit = metrics.get("commit") if isinstance(metrics.get("commit"), dict) else {}
    activity = details.get("user_activity") if isinstance(details.get("user_activity"), dict) else {}
    # per_tenant is part of the metrics contract, not details.  Keeping this
    # distinction matters for Jain calculations and for the denominator shown
    # in the report.
    per_tenant = metrics.get("per_tenant") if isinstance(metrics.get("per_tenant"), dict) else {}
    coverage = (
        details.get("pr421_metric_coverage")
        if isinstance(details.get("pr421_metric_coverage"), dict)
        else {}
    )
    scenario_config = run.get("scenario_config") if isinstance(run.get("scenario_config"), dict) else {}
    # formal_suite keeps the capacity target in the scenario catalog. Accept
    # both the enriched manifest field and the older run shape.
    capacity_active_users = (
        run.get("capacity_active_users")
        or scenario_config.get("capacity_active_users")
    )
    tenant_count = (
        run.get("tenant_count")
        or scenario_config.get("tenant_count")
        or scenario_config.get("tenants")
    )
    return {
        "scenario": run.get("scenario"),
        "label": run.get("scenario_label") or run.get("scenario"),
        "status": run.get("status"),
        "output_dir": Path(str(run.get("output_dir") or "")),
        "search_submitted": search.get("submitted"),
        "search_succeeded": search.get("succeeded"),
        "search_success_rate": search.get("success_rate"),
        "search_p95_s": (search.get("latency") or {}).get("p95_s"),
        "search_p99_s": (search.get("latency") or {}).get("p99_s"),
        "quality_asserted": search.get("quality_asserted"),
        "quality_failures": search.get("quality_failures"),
        "search_errors": search.get("errors"),
        "search_rate_limited": search.get("rate_limited_count"),
        "commit_submitted": commit.get("submitted"),
        "commit_completed": commit.get("completed"),
        "commit_failed": commit.get("failed"),
        "commit_timeout": commit.get("timeout"),
        "commit_rate_limited": commit.get("rate_limited_count"),
        "commit_success_rate": commit.get("success_rate"),
        "active_users": activity.get("active_user_count"),
        "capacity_active_users": capacity_active_users,
        "tenant_count": tenant_count,
        "hot_user": (activity.get("hot_user_proxy") or {}).get("request_count"),
        "overlap": (details.get("same_window_overlap") or {}).get("overlap_proven"),
        "overlap_ms": (details.get("same_window_overlap") or {}).get("overlap_ms"),
        "per_tenant": per_tenant,
        "metric_coverage": coverage,
        "details": details,
    }


def latest_summary_path(run: dict[str, Any]) -> Path:
    output = run.get("output_dir")
    if not output:
        return Path()
    output = Path(str(output))
    direct = output / "run"
    candidates = sorted(direct.glob("*/summary.json"))
    return candidates[-1] if candidates else Path()


def resource_for(run: dict[str, Any]) -> dict[str, Any]:
    summary_path = latest_summary_path(run)
    summary = read_json(summary_path, {})
    return summary.get("resources") if isinstance(summary, dict) else {}


def metric_coverage(records: list[dict[str, Any]]) -> dict[str, Any]:
    families = [
        "echomem_lane_queued",
        "echomem_lane_wait_seconds",
        "echomem_lane_exec_seconds",
        "echomem_lane_rejected_total",
        "echomem_engine_fanout_exec_seconds",
        "echomem_engine_fanout_skipped_total",
    ]
    present: set[str] = set()
    lane_quartets: dict[str, dict[str, bool]] = {}
    fanout_engines: dict[str, dict[str, bool]] = {}
    for record in records:
        path = record["output_dir"] / "run"
        for sample in path.glob("*/metrics_samples.csv"):
            try:
                text = sample.read_text(encoding="utf-8")
            except OSError:
                continue
            for family in families:
                if family in text:
                    present.add(family)
        coverage = record.get("metric_coverage") or {}
        for lane, quartet in (coverage.get("lane_quartets") or {}).items():
            if not isinstance(quartet, dict):
                continue
            current = lane_quartets.setdefault(
                str(lane), {"queued": False, "wait": False, "exec": False, "rejected": False}
            )
            for field in current:
                current[field] = current[field] or bool(quartet.get(field))
        for engine, facts in (coverage.get("fanout_engines") or {}).items():
            if not isinstance(facts, dict):
                continue
            current = fanout_engines.setdefault(str(engine), {"exec": False, "skipped": False})
            current["exec"] = current["exec"] or bool(facts.get("exec"))
            current["skipped"] = current["skipped"] or bool(facts.get("skipped"))
    return {
        "families": families,
        "present": present,
        "missing": [x for x in families if x not in present],
        "lane_quartets": lane_quartets,
        "fanout_engines": fanout_engines,
    }


def build(args: argparse.Namespace) -> Path:
    suite_path = args.suite.resolve()
    runs = load_formal_runs(suite_path)
    records = [run_record(run) for run in runs]
    for record, run in zip(records, runs):
        record["resource"] = resource_for(run)

    old_bounded = read_json(args.bounded, {}) if args.bounded else {}
    old_fairness = read_json(args.fairness, {}) if args.fairness else {}
    recovery = read_json(args.recovery, {}) if args.recovery else {}
    coverage = metric_coverage(records)

    # O1 is reported in active-user proxies, not tenant-count labels. A
    # successful point alone is still only a lower bound.
    capacity = {
        record["scenario"]: record
        for record in records
        if str(record["scenario"] or "").startswith("capacity-")
    }
    max_observed_capacity = max(
        (
            int(record["active_users"] or 0)
            for name, record in capacity.items()
            if int(record["search_submitted"] or 0) > 0
            and int(record["active_users"] or 0) > 0
        ),
        default=0,
    )
    max_99pct_capacity = max(
        (
            int(record["active_users"] or 0)
            for name, record in capacity.items()
            if record["status"] == "completed"
            and int(record["search_submitted"] or 0) > 0
            and float(record["search_success_rate"] or 0) >= 0.99
        ),
        default=0,
    )
    capacity_active_users = {
        int(name.split("-", 1)[1]): {
            "capacity_target_active_users": record.get("capacity_active_users"),
            "tenant_count": record.get("tenant_count"),
            "active_users": record["active_users"],
            "hot_user_requests": record["hot_user"],
            "search_submitted": record["search_submitted"],
            "search_succeeded": record["search_succeeded"],
            "search_errors": record["search_errors"],
            "status": record["status"],
        }
        for name, record in capacity.items()
        if name.split("-", 1)[1].isdigit()
    }
    max_capacity_active_users = max(
        (
            int(item["active_users"] or 0)
            for item in capacity_active_users.values()
            if item["status"] == "completed"
        ),
        default=0,
    )
    max_capacity_hot_requests = max(
        (
            int(item["hot_user_requests"] or 0)
            for item in capacity_active_users.values()
            if item["status"] == "completed"
        ),
        default=0,
    )
    o1_status = INCONCLUSIVE
    o1_reason = (
        f"本轮容量场景使用活跃 session 作为用户代理，最大实测活跃用户数为 "
        f"{max_capacity_active_users}，最大热用户请求数为 {max_capacity_hot_requests}；"
        f"按 Search 成功率 >=99% 的严格口径，合格容量档位为 {max_99pct_capacity}；"
        "尚未形成可复核的“合格档+下一档明确失败”容量边界。"
    )

    # O2 has no real fault control endpoint in the current profile.
    o2_status = INCONCLUSIVE
    o2_reason = "当前 profile 未配置真实单租户故障控制端点；已有隔离/旁观数据不能等价替代故障期间前后配对 P95。"

    fairness_record = next((x for x in records if x["scenario"] == "fairness-bounded"), None)
    tenant_commit: list[float] = []
    tenant_latency_utility: list[float] = []
    if fairness_record:
        for tenant in fairness_record["per_tenant"].values():
            commit = tenant.get("commit") or {}
            search = tenant.get("search") or {}
            # Throughput fairness uses completed commits per comparable
            # window.  The bounded case has equal duration, so counts are
            # equivalent and remain easier to audit in the report.
            tenant_commit.append(float(commit.get("completed") or 0))
            p95 = (search.get("latency") or {}).get("p95_s")
            if p95 and float(p95) > 0:
                tenant_latency_utility.append(1.0 / float(p95))
    commit_jain = jain(tenant_commit)
    search_jain = jain(tenant_latency_utility)
    o3_status = INCONCLUSIVE
    o3_reason = "本轮为 bounded barrier，不是预先约定的稳态固定速率；保留 Jain 计算值作为诊断，不作为正式通过。"

    priority = next((x for x in records if x["scenario"] == "search-priority-blackbox"), None)
    baseline = next((x for x in records if x["scenario"] == "baseline"), None)
    priority_ratio = None
    if priority and baseline and priority["search_p95_s"] and baseline["search_p95_s"]:
        priority_ratio = priority["search_p95_s"] / baseline["search_p95_s"]
    o4_status = INCONCLUSIVE
    o4_reason = "有 Search/Commit 重叠窗口，但洪泛期间大量 Commit 被拒绝或超时，尚不足以证明后台 Commit 持续占满时的严格优先级。"

    accepted = recovery.get("accepted_202") is True
    messages = recovery.get("message_reconciliation") or {}
    order = recovery.get("order_reconciliation") or {}
    cursor = recovery.get("cursor_reconciliation") or {}
    idem = recovery.get("idempotency_reconciliation") or {}
    o5_status = INCONCLUSIVE
    o5_reason = "已有一次真实 kill-9 恢复后消息集合、顺序和 cursor 对账通过；幂等重放为 INCONCLUSIVE，且样本数不足以宣称 100%。"

    lane_quartets = coverage.get("lane_quartets") or {}
    complete_lanes = sorted(
        lane for lane, values in lane_quartets.items()
        if all(bool(values.get(field)) for field in ("queued", "wait", "exec", "rejected"))
    )
    o6_status = PASS if not coverage["missing"] and complete_lanes else INCONCLUSIVE
    o6_reason = (
        "所有声明的指标族和至少一个实际 lane 四元组均在原始 metrics 中找到。"
        if o6_status == PASS
        else (
            "指标族已部分采集，但缺少完整 lane 四元组或 engine fan-out 指标，"
            "不能宣称每层可观测性完整。"
        )
    )

    objectives = [
        ("O1", "最大 DAU / 最大热用户量", o1_status, o1_reason,
         f"容量场景 active users 最大={max_capacity_active_users}；"
         f"热用户请求最大={max_capacity_hot_requests}；"
         f"99% 成功率合格容量档={max_99pct_capacity}"),
        ("O2", "单租户故障对旁观租户 Search P95 劣化", o2_status, o2_reason, "fault control 未配置"),
        ("O3", "Commit 吞吐与 Search 延迟 Jain 公平指数", o3_status, o3_reason,
         f"Commit Jain={num(commit_jain, 4)}；Search latency Jain={num(search_jain, 4)}"),
        ("O4", "Commit 洪泛时 Search 严格优先", o4_status, o4_reason,
         f"priority/base Search P95 ratio={num(priority_ratio, 3)}；overlap={esc(priority['overlap_ms'] if priority else None)} ms"),
        ("O5", "202 Commit 崩溃恢复、不丢序", o5_status, o5_reason,
         f"accepted_202={accepted}；message={messages.get('status', '-')}; order={order.get('status', '-')}; cursor={cursor.get('status', '-')}; idempotency={idem.get('status', '-')}"),
        ("O6", "每层/每租户队列四元组可观测性", o6_status, o6_reason,
         f"families={len(coverage['present'])}/{len(coverage['families'])}；"
         f"完整 lane={len(complete_lanes)}"),
    ]

    status_class = lambda value: value.lower().replace("_", "-")
    objective_cards = "".join(
        f'<article class="objective {status_class(status)}">'
        f'<div class="eyebrow">{esc(oid)}</div><h3>{esc(name)}</h3>'
        f'<div class="status">{esc(status)}</div><p>{esc(reason)}</p>'
        f'<code>{esc(observed)}</code></article>'
        for oid, name, status, reason, observed in objectives
    )

    scenario_rows = []
    for record in records:
        scenario_rows.append(
            "<tr>"
            f"<td>{esc(record['scenario'])}</td><td>{esc(record['label'])}</td>"
            f"<td>{esc(record['status'])}</td><td>{esc(record['search_submitted'])}</td>"
            f"<td>{esc(record['search_succeeded'])}/{esc(record['search_submitted']) if record['search_submitted'] is not None else '-'}</td>"
            f"<td>{pct(record['search_success_rate'])}</td>"
            f"<td>{esc(record['search_errors'] or 0)}</td>"
            f"<td>{esc(record['search_rate_limited'] or 0)}</td>"
            f"<td>{esc(record['quality_failures'] or 0)}</td>"
            f"<td>{num((record['search_p95_s'] or 0) * 1000) if record['search_p95_s'] is not None else '-'}</td>"
            f"<td>{esc(record['commit_submitted'] or 0)}</td><td>{esc(record['commit_completed'] or 0)}</td>"
            f"<td>{esc(record['commit_failed'] or 0)}</td><td>{esc(record['commit_timeout'] or 0)}</td>"
            f"<td>{num((record['resource'].get('cpu_util_mean_percent') if record['resource'] else None))}%</td>"
            f"<td>{num((record['resource'].get('rss_peak_mb') if record['resource'] else None))}</td>"
            f"<td>{link(record['output_dir'], '打开场景') if record['output_dir'].is_dir() else '-'}</td>"
            "</tr>"
        )

    chart = chart_bars(
        [
            (record["scenario"], (record["search_p95_s"] or 0) * 1000)
            for record in records
            if record["search_p95_s"] is not None
        ],
        "ms",
    )
    coverage_rows = "".join(
        f"<tr><td>{esc(family)}</td><td class={'pass' if family in coverage['present'] else 'inconclusive'}>"
        f"{'已采集' if family in coverage['present'] else '缺失'}</td></tr>"
        for family in coverage["families"]
    )
    lane_rows = "".join(
        f"<tr><td>{esc(lane)}</td>"
        + "".join(
            f"<td class={'pass' if bool(values.get(field)) else 'inconclusive'}>"
            f"{'有' if values.get(field) else '无'}</td>"
            for field in ("queued", "wait", "exec", "rejected")
        )
        + "</tr>"
        for lane, values in sorted(lane_quartets.items())
    )
    if not lane_rows:
        lane_rows = "<tr><td colspan='5' class='empty'>没有 lane 四元组记录</td></tr>"

    old_bounded_features = (old_bounded.get("feature_verdicts") or {}).get("features", {})
    old_fairness_features = (old_fairness.get("feature_verdicts") or {}).get("features", {})
    suite_payload = read_json(suite_path, {})
    source_commit = (
        ((suite_payload.get("plan_sources") or {}).get("pr421") or {}).get("commit")
        or ((suite_payload.get("acceptance_targets") or {}).get("source") or {}).get("commit")
        or "-"
    )
    generated_at = suite_payload.get("created_at") or "2026-09-03"
    capacity_rows = "".join(
        f"<tr><td>{esc(item.get('capacity_target_active_users') or level)}</td>"
        f"<td>{esc(item.get('tenant_count') or '-')}</td>"
        f"<td>{esc(item.get('status'))}</td>"
        f"<td>{esc(item.get('active_users'))}</td>"
        f"<td>{esc(item.get('hot_user_requests'))}</td>"
        f"<td>{esc(item.get('search_succeeded'))}/{esc(item.get('search_submitted'))}</td>"
        f"<td>{esc(item.get('search_errors') or 0)}</td></tr>"
        for level, item in sorted(capacity_active_users.items())
    )
    if not capacity_rows:
        capacity_rows = "<tr><td colspan='7' class='empty'>没有 capacity-* 场景记录</td></tr>"
    fairness_rows = ""
    if fairness_record:
        fairness_rows = "".join(
            f"<tr><td>{esc(tenant_id)}</td>"
            f"<td>{esc((tenant.get('commit') or {}).get('submitted') or 0)}</td>"
            f"<td>{esc((tenant.get('commit') or {}).get('completed') or 0)}</td>"
            f"<td>{esc((tenant.get('search') or {}).get('submitted') or 0)}</td>"
            f"<td>{esc((tenant.get('search') or {}).get('succeeded') or 0)}</td>"
            f"<td>{num(((tenant.get('search') or {}).get('latency') or {}).get('p95_s', None) * 1000) if ((tenant.get('search') or {}).get('latency') or {}).get('p95_s') is not None else '-'}</td>"
            "</tr>"
            for tenant_id, tenant in sorted(fairness_record["per_tenant"].items())
        )
    if not fairness_rows:
        fairness_rows = "<tr><td colspan='6' class='empty'>没有可比较的公平性租户数据</td></tr>"
    source_notes = [
        f"本轮正式套件：{suite_path}",
        f"方案记录的 EchoMem commit：{source_commit}",
        "真实 HTTP、真实 DashScope 模型；rerank 关闭；soak 关闭；4U8G 单实例。",
        f"补充 bounded 结果中的租户隔离状态：{((old_bounded.get('isolation_probe') or {}).get('status') or '-')}",
        f"补充旧公平性场景的判定：{old_fairness_features.get('tenant_fairness', {}).get('verdict', '-')}",
    ]
    recovery_block = (
        f"<div class='timeline'><span>202 accepted={esc(recovery.get('accepted_202'))}</span>"
        f"<i></i><span>SIGKILL control={esc(recovery.get('container_control_ok'))}</span>"
        f"<i></i><span>recovered={esc(recovery.get('recovered'))}</span>"
        f"<i></i><span>message={esc(messages.get('status'))}</span>"
        f"<i></i><span>order={esc(order.get('status'))}</span>"
        f"<i></i><span>cursor={esc(cursor.get('status'))}</span></div>"
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>EchoMem 4U8G 六项黑盒验收报告</title>
<style>
:root{{--bg:#f4f7f8;--paper:#fff;--ink:#17212b;--muted:#6f7d86;--line:#dce5e9;--green:#13795b;--red:#b43e3e;--amber:#9a6900;--blue:#286b8f}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1440px;margin:auto;padding:28px 22px 64px}}header{{display:flex;justify-content:space-between;gap:24px;align-items:flex-end;border-bottom:1px solid var(--line);padding-bottom:18px}}
h1{{font-size:28px;margin:0 0 4px;letter-spacing:0}}h2{{font-size:19px;margin:0 0 12px}}h3{{font-size:16px;margin:4px 0 8px}}p{{margin:7px 0}}.muted{{color:var(--muted)}}.meta{{text-align:right;color:var(--muted);font-size:12px}}
.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:18px 0}}.objective{{background:var(--paper);border:1px solid var(--line);border-left:5px solid var(--amber);padding:15px;min-height:180px}}
.objective.pass{{border-left-color:var(--green)}}.objective.fail{{border-left-color:var(--red)}}.eyebrow{{font-size:12px;color:var(--muted);font-weight:700}}.status{{font-size:18px;font-weight:800;color:var(--amber)}}.pass .status{{color:var(--green)}}.fail .status{{color:var(--red)}}.objective code{{display:block;background:#f3f6f7;padding:7px;white-space:pre-wrap;font-size:12px}}
section{{background:var(--paper);border:1px solid var(--line);padding:18px;margin-top:14px}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;min-width:980px}}th,td{{border-bottom:1px solid #e7edef;padding:8px;text-align:left;vertical-align:top}}th{{background:#f7f9fa;font-size:12px;color:#56656e}}td{{font-size:13px}}a{{color:var(--blue);text-decoration:none}}a:hover{{text-decoration:underline}}
.bar-row{{display:grid;grid-template-columns:180px 1fr 90px;gap:10px;align-items:center;margin:8px 0}}.bar-label{{font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.bar-track{{height:10px;background:#e8eef0;border-radius:2px;overflow:hidden}}.bar-fill{{display:block;height:100%;background:var(--blue)}}.empty{{color:var(--muted);padding:16px}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}.pass{{color:var(--green);font-weight:700}}.inconclusive{{color:var(--amber);font-weight:700}}.fail{{color:var(--red);font-weight:700}}
.timeline{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:10px 0;padding:12px;background:#f7f9fa}}.timeline span{{font-weight:700}}.timeline i{{width:28px;height:1px;background:#9eabb2;display:inline-block}}
ul{{margin:8px 0;padding-left:20px}}details{{margin-top:10px}}summary{{cursor:pointer;font-weight:700}}@media(max-width:900px){{header{{display:block}}.meta{{text-align:left;margin-top:10px}}.grid{{grid-template-columns:1fr}}.two{{grid-template-columns:1fr}}.bar-row{{grid-template-columns:110px 1fr 70px}}}}
</style></head><body><main>
<header><div><div class="muted">黑盒验收 / 4U8G</div><h1>EchoMem 六项指标实测报告</h1><div class="muted">develop · 4U8G · 真实模型 · soak 关闭</div></div>
<div class="meta">生成时间：{esc(generated_at)}<br>测试对象：EchoMem develop<br>服务版本：0.4.3</div></header>
<div class="grid">{objective_cards}</div>
<section><h2>结论先看</h2><p>本轮正式场景共完成 {len(records)} 个，其中容量场景实际观察到最多 {max_observed_capacity} 个活跃用户代理；按 99% Search 成功率严格口径暂无合格容量边界。服务端 Search 大多有响应，但真实 Commit 抽取/队列等待和限流是主要干扰来源。</p>
<p>当前不能把六项全部判定为通过：O2 缺少真实故障控制，O3 不是稳态固定速率，O4 洪泛中有大量拒绝/超时，O5 只完成一次恢复且幂等重放未充分证明，O6 的 lane 四元组或 fan-out 覆盖仍不完整。</p></section>
<section class="scroll"><h2>本轮场景数据</h2>
<p class="muted">Search 分母包含失败和限流请求；Commit 的提交、完成、失败、超时分别列出，不用完成数替代提交数。</p>
<table><thead><tr><th>场景</th><th>用途</th><th>状态</th><th>Search 请求</th><th>成功/提交</th><th>成功率</th><th>HTTP 错误</th><th>限流</th><th>质量失败</th><th>Search P95(ms)</th><th>Commit 提交</th><th>完成</th><th>失败</th><th>超时</th><th>CPU 均值</th><th>RSS 峰值(MB)</th><th>原始制品</th></tr></thead><tbody>{"".join(scenario_rows)}</tbody></table></section>
<section><h2>Search P95 可视化</h2><p class="muted">横向柱状图按场景展示客户端 Search P95；它只表示延迟，不代表记忆质量。</p>{chart}</section>
<section><h2>容量与公平性明细</h2>
<div class="two"><div><h3>容量阶梯（只看 capacity 场景）</h3><table><thead><tr><th>目标活跃用户</th><th>真实租户数</th><th>状态</th><th>实测 Active users</th><th>热用户请求</th><th>Search 成功/提交</th><th>错误</th></tr></thead><tbody>{capacity_rows}</tbody></table></div>
<div><h3>fairness-bounded 每租户数据</h3><table><thead><tr><th>租户</th><th>Commit 提交</th><th>完成</th><th>Search 提交</th><th>成功</th><th>P95(ms)</th></tr></thead><tbody>{fairness_rows}</tbody></table></div></div>
<div class="two"><div><h3>公平性计算</h3><p>本轮每租户 Commit 完成数：{esc(tenant_commit or '-')}。</p><p>Commit Jain：<strong>{num(commit_jain, 4)}</strong></p><p>Search latency utility Jain：<strong>{num(search_jain, 4)}</strong></p><p class="muted">计算公式：Jain(x) = (Σx)² / (n × Σx²)。当前仍标记 INCONCLUSIVE，因为 bounded barrier 不是正式稳态固定速率窗口。</p></div>
<div><h2>优先级对照</h2><p>Baseline Search P95：{num((baseline["search_p95_s"] if baseline else None) * 1000)} ms</p><p>洪泛 Search P95：{num((priority["search_p95_s"] if priority else None) * 1000)} ms</p><p>劣化倍数：<strong>{num(priority_ratio, 3)}x</strong></p><p>重叠窗口：{esc(priority["overlap_ms"] if priority else None)} ms；不能单凭“Search 没完全超时”证明严格优先级。</p></div></section>
<section><h2>崩溃恢复时间线</h2>{recovery_block}<table><thead><tr><th>证据</th><th>状态</th><th>说明</th></tr></thead><tbody>
<tr><td>HTTP 202</td><td class="{'pass' if accepted else 'fail'}">{esc(accepted)}</td><td>必须在 kill 前明确收到 202 才能进入恢复验收</td></tr>
<tr><td>消息集合</td><td>{esc(messages.get('status'))}</td><td>{esc(messages.get('reason'))}</td></tr>
<tr><td>顺序</td><td>{esc(order.get('status'))}</td><td>{esc(order.get('reason'))}</td></tr>
<tr><td>Cursor</td><td>{esc(cursor.get('status'))}</td><td>{esc(cursor.get('reason'))}</td></tr>
<tr><td>幂等重放</td><td class="inconclusive">{esc(idem.get('status'))}</td><td>一次相同 idempotency key 的观察不足以证明多次重放场景</td></tr>
</tbody></table></section>
<section class="scroll"><h2>Metrics 四元组覆盖</h2><p>{esc(o6_reason)}</p>
<table><thead><tr><th>指标族</th><th>证据</th></tr></thead><tbody>{coverage_rows}</tbody></table>
<h3>实际发现的 lane</h3>
<table><thead><tr><th>Lane</th><th>queued</th><th>wait</th><th>exec</th><th>rejected</th></tr></thead><tbody>{lane_rows}</tbody></table></section>
<section><h2>测试边界与原始来源</h2><ul>{"".join(f"<li>{esc(note)}</li>" for note in source_notes)}</ul>
<details><summary>打开原始恢复 JSON</summary><pre>{esc(json.dumps(recovery,ensure_ascii=False,indent=2))}</pre></details>
<details><summary>打开原始 suite.json</summary><p>{link(suite_path, str(suite_path))}</p></details>
<p class="muted">说明：RSS 短窗口上升不能直接等价于长期内存泄漏；容量“超时”也不会自动等价于 DAU 上限，必须结合下一档边界和服务资源证据复核。</p></section>
</main></body></html>"""
    args.out.write_text(document, encoding="utf-8")
    return args.out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--bounded", type=Path)
    parser.add_argument("--fairness", type=Path)
    parser.add_argument("--recovery", type=Path)
    args = parser.parse_args()
    print(build(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
