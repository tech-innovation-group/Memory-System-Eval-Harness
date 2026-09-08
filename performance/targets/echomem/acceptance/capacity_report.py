"""Render a standalone M1 evidence report without inferring an unmeasured maximum."""
from __future__ import annotations
from performance.targets.echomem.acceptance.provenance import render_platform_provenance

import argparse
from collections import Counter
from html import escape
import json
from pathlib import Path

from performance.stats import percentile
from performance.targets.echomem.acceptance.route_path_report import render_route_paths


def fmt(value, unit=""):
    if value is None:
        return "未测得"
    if isinstance(value, float):
        return f"{value:.3f}{unit}"
    return escape(str(value)) + unit


def _counts(value):
    if not value:
        return "无"
    return ", ".join(f"{key}: {count}" for key, count in sorted(value.items()))


def _level_snapshots(levels: list[dict]) -> list[dict]:
    """Flatten exploration and confirmation outputs into one report contract."""
    snapshots = []
    for level in levels:
        if "search" in level:
            snapshots.append({**level, "phase": "混合" if level.get("mixed") else "纯召回",
                              "hot_users": level.get("identity_count")})
            continue
        for key, phase in (("pure_aggregate", "纯召回"), ("mixed_aggregate", "混合")):
            aggregate = level.get(key)
            if aggregate:
                snapshots.append({**aggregate, "phase": phase,
                                  "hot_users": level.get("hot_users"),
                                  "users_per_tenant": level.get("users_per_tenant"),
                                  "level_status": level.get("status")})
    return snapshots


def _dau_label(dau: object) -> str:
    if not isinstance(dau, dict):
        return fmt(dau)
    estimates = dau.get("estimates") or []
    values = [row.get("conservative_dau") for row in estimates
              if row.get("conservative_dau") is not None]
    if not values:
        return "0" if dau.get("status") == "ZERO_UNDER_LOCKED_SLO" else "未测得"
    return f"条件估算 {min(values):.0f}–{max(values):.0f}"


def _merge_seed_evidence(paths: list[Path]) -> dict:
    actors = []
    statuses = []
    for path in paths:
        value = json.loads(path.read_text(encoding="utf-8"))
        statuses.append(value.get("status"))
        actors.extend(value.get("actors", []))
    return {"status": "PASS" if statuses and all(s == "PASS" for s in statuses) else
            "UNKNOWN" if not statuses else "FAIL", "actors": actors,
            "evidence_files": [str(path.name) for path in paths]}


def render(report: dict) -> str:
    if report.get("assessment_mode") == "observe":
        from performance.targets.echomem.acceptance.capacity_observation_report import render_observation
        return render_observation(report)
    environment = report.get("environment") or report.get("manifest", {})
    actors = report.get("seed", {}).get("actors", [])
    queries = [q for a in actors for q in a.get("queries", [])]
    seed_summary = report.get("seed_summary") or {}
    correct = seed_summary.get("strict_valid", sum(bool(q.get("success")) for q in queries))
    fact_hits = seed_summary.get("fact_hits", sum(bool(q.get("matched_expected_fact")) for q in queries))
    degraded = seed_summary.get("degraded", sum(bool(q.get("degraded")) for q in queries))
    query_count = seed_summary.get("queries", len(queries))
    empty = seed_summary.get("empty", sum(q.get("hit_count") == 0 for q in queries))
    latency = [q["elapsed_s"] for q in queries if q.get("elapsed_s") is not None]
    reasons = Counter(r for q in queries for r in q.get("degraded_reasons", []))
    levels = report.get("levels", [])
    snapshots = _level_snapshots(levels)
    # Only an explicit confirmed boundary permits a maximum/DAU claim.
    boundary = report.get("boundary") or {}
    confirmed = boundary.get("status") == "CONFIRMED" and bool(boundary.get("evidence"))
    zero_error_confirmed = (boundary.get("status") == "ZERO_ERROR_CONFIRMED" and
                            bool(boundary.get("evidence")))
    zero_error_level = boundary.get("highest_zero_error") if zero_error_confirmed else None
    maximum = (boundary.get("max_hot_users") or report.get("max_hot_users")) if confirmed else None
    dau = report.get("dau") if confirmed else None
    actor_rows = []
    for actor in actors:
        qs = actor.get("queries", [])
        times = [q["elapsed_s"] for q in qs if q.get("elapsed_s") is not None]
        actor_rows.append("<tr>" + "".join(f"<td>{value}</td>" for value in (
            f"T{actor['tenant_index'] + 1} / U{actor['user_index'] + 1}",
            fmt(actor.get("input_documents")), fmt(actor.get("input_characters")),
            fmt(actor.get("commit_http_status")), fmt(actor.get("commit_state")),
            f"{sum(bool(q.get('success')) for q in qs)}/{len(qs)}",
            f"{len(qs)}/{actor.get('semantic_queries', 40)}",
            fmt(sum(times) / len(times) if times else None, " s"), fmt(percentile(times, 95), " s"),
            fmt(actor.get("status")))) + "</tr>")
    level_rows = []
    error_rows = []
    route_summaries = []
    for level in snapshots:
        search = level["search"]
        level_rows.append("<tr>" + "".join(f"<td>{fmt(value)}</td>" for value in (
            level["tenant_count"], level.get("hot_users", level["identity_count"]), level["phase"],
            level["duration_s"], search["planned"], search["sent"], search["success"],
            search["mean_s"], search["p95_s"], search["p99_s"], search["degraded"],
            search["timeout_censored"], search.get("atomic_p95_s"),
            search.get("unattributed_residual_p95_s"), level["effective_search_rps"],
            ("诊断 / 观测SLO=" + level.get("observed_slo_result", "UNKNOWN")) if level.get("diagnostic_only")
            else level.get("status", level.get("level_status")))) + "</tr>")
        breakdown = search.get("error_breakdown") or {}
        partition = breakdown.get("outcome_partition") or {}
        error_rows.append("<tr>" + "".join(f"<td>{fmt(value)}</td>" for value in (
            level.get("hot_users", level["identity_count"]), level["phase"],
            breakdown.get("denominator_sent", search.get("sent")),
            partition.get("strict_success", search.get("success")),
            breakdown.get("http_200_quality_failures"), breakdown.get("http_200_degraded"),
            breakdown.get("http_non_200"), breakdown.get("http_4xx"),
            breakdown.get("authentication_or_permission_http"),
            breakdown.get("rate_limited_http_429"), breakdown.get("http_5xx"),
            breakdown.get("transport_errors"),
            breakdown.get("timeout_censored", search.get("timeout_censored")),
            _counts(breakdown.get("transport_error_types")),
            _counts(breakdown.get("reason_code_counts") or search.get("http_reason_counts")),
            breakdown.get("unclassified_failures"),
            "是" if breakdown.get("partition_complete") else "旧数据未保留完整分区",
        )) + "</tr>")
        route_summaries.append((f"H={level.get('hot_users', level['identity_count'])} {level['phase']}", search))
    repeat_rows = []
    cell_rows = []
    for level in levels:
        for repeat in level.get("repeats", []):
            for phase in ("pure", "mixed"):
                value = repeat.get(phase)
                if not value:
                    continue
                search = value["search"]
                commit = value.get("commit", {})
                repeat_rows.append("<tr>" + "".join(f"<td>{fmt(cell)}</td>" for cell in (
                    level.get("hot_users"), repeat.get("repeat"), "纯召回" if phase == "pure" else "混合",
                    repeat.get("seed_status"), value.get("status"), search.get("sent"),
                    search.get("success"), search.get("quality_rate"), search.get("p95_s"),
                    search.get("atomic_p95_s"), commit.get("accepted_202"), commit.get("completed"),
                    commit.get("p95_s"))) + "</tr>")
        for phase, aggregate in (("纯召回", level.get("pure_aggregate")),
                                 ("混合", level.get("mixed_aggregate"))):
            if not aggregate:
                continue
            for cell in aggregate.get("cells", []):
                p95_ci = cell.get("p95_block_bootstrap_95") or []
                quality_ci = cell.get("quality_wilson_95") or []
                cell_rows.append("<tr>" + "".join(f"<td>{fmt(value)}</td>" for value in (
                    level.get("hot_users"), phase, cell.get("identity_index"),
                    cell.get("query_type"), cell.get("sent"), cell.get("success"),
                    cell.get("quality_rate"), " – ".join(f"{v:.4f}" for v in quality_ci),
                    cell.get("p95_s"), " – ".join(f"{v:.3f}" for v in p95_ci),
                    cell.get("atomic_p95_s"), cell.get("degraded"), cell.get("errors"))) + "</tr>")
    detail_rows = []
    for a in actors:
        for q in a.get("queries", []):
            detail_rows.append("<tr>" + "".join(f"<td>{value}</td>" for value in (
                f"T{a['tenant_index'] + 1}/U{a['user_index'] + 1}", fmt(q.get("query_id")),
                fmt(q.get("http_status")), fmt(q.get("hit_count")),
                "命中" if q.get("matched_expected_fact") else "未命中", "通过" if q.get("success") else "失败",
                fmt(q.get("elapsed_s"), " s"), escape(", ".join(q.get("degraded_reasons", []))) or "无",
                escape(", ".join(q.get("executed_layers", []))) or "未提供",
                escape(str(q.get("final_verdicts", {}))),
            )) + "</tr>")
    provider_rows = "".join("<tr>" + "".join(f"<td>{fmt(p.get(key))}</td>" for key in
        ("kind", "model", "status", "code", "elapsed_s")) + "</tr>" for p in report.get("providers", []))
    router = report.get("router_compatibility") or {}
    router_rows = "".join("<tr>" + "".join(f"<td>{fmt(r.get(key))}</td>" for key in
        ("query_index", "max_tokens", "content_characters", "reasoning_characters", "valid_tag", "finish_reason")) + "</tr>"
        for r in router.get("diagnostic_requests", []))
    route_rows = "".join("<tr>" + "".join(f"<td>{fmt(value)}</td>" for value in (
        r.get("query_id"), r.get("synthetic_query"), (r.get("verdict_resolvers") or {}).get("resource"),
        (r.get("final_verdicts") or {}).get("resource"), ", ".join(r.get("executed_layers", [])))) + "</tr>"
        for r in report.get("route_failures", []))
    calibration_rows = "".join("<tr>" + "".join(f"<td>{fmt(value)}</td>" for value in (
        row.get("template_id"), row.get("planned"), row.get("success"), row.get("matched"),
        row.get("degraded"), escape(str(row.get("reasons", {}))))) + "</tr>"
        for row in (report.get("query_calibration") or {}).get("candidates", []))
    exploration_rows = []
    for level in (report.get("exploration") or {}).get("levels", []):
        search = level.get("search", {})
        exploration_rows.append("<tr>" + "".join(f"<td>{fmt(value)}</td>" for value in (
            level.get("tenant_count"), level.get("identity_count"),
            "混合" if level.get("mixed") else "纯召回", level.get("duration_s"),
            search.get("sent"), search.get("success"), search.get("p95_s"),
            search.get("atomic_p95_s"), level.get("status"))) + "</tr>")
    attempts_html = []
    for attempt in report.get("attempts", []):
        aq = [q for a in attempt.get("seed", {}).get("actors", []) for q in a.get("queries", [])]
        attempts_html.append("<tr>" + "".join(f"<td>{fmt(value)}</td>" for value in (
            attempt.get("label"), attempt.get("query_contract", "source-ambiguous-v1"),
            attempt.get("environment", {}).get("intent_thinking_enabled", "未显式关闭"),
            len(aq), sum(bool(q.get("matched_expected_fact")) for q in aq),
            sum(bool(q.get("success")) for q in aq), sum(bool(q.get("degraded")) for q in aq),
            sum("invalid_output" in q.get("degraded_reasons", []) for q in aq),
            attempt.get("status"))) + "</tr>")
    reasons_html = "".join(f"<li><code>{escape(str(reason))}</code>：{count} 次</li>" for reason, count in reasons.most_common())
    width = 100 * correct / query_count if query_count else 0
    resources = report.get("resources", [])
    rss = [r["rss_bytes"] / 1048576 for r in resources if r.get("rss_bytes") is not None]
    cpu = [r["cpu_percent_one_core_100"] for r in resources if r.get("cpu_percent_one_core_100") is not None]
    dau_rows = []
    for row in (dau or {}).get("estimates", []) if isinstance(dau, dict) else []:
        dau_rows.append("<tr>" + "".join(f"<td>{fmt(value)}</td>" for value in (
            row.get("searches_per_user_day"), row.get("commits_per_user_day"),
            row.get("search_peak_factor"), row.get("search_limited_dau"),
            row.get("commit_limited_dau"), row.get("conservative_dau"),
            row.get("steady_commit_capacity_rps"))) + "</tr>")
    def bars(values):
        ceiling = max(values, default=0) or 1
        return ''.join(f'<span title="{v:.2f}" style="height:{max(1, 100*v/ceiling):.2f}%"></span>' for v in values)
    title = "4U8G 单实例热用户容量与 DAU"
    conclusion = ("已确认容量边界；DAU 是锁定业务行为与峰均比后的条件估算。" if confirmed else
        f"已确认三轮零错误档 H={zero_error_level}；它不是最大容量，硬边界仍需继续加压。" if zero_error_confirmed else
        "尚未测得最大热用户数和 DAU。以下是已完成的真实数据，不把预检或最高尝试档位当作容量上限。")
    if confirmed and maximum == 0:
        blocker = ("最低档 H=1 已在三组全新身份上违反锁定 SLO，因此按 fail-fast 规则跳过更高负载；"
                   "0 表示当前测试合同下没有正容量，不代表接口完全不可用。")
    elif zero_error_confirmed:
        blocker = (f"H={zero_error_level} 是连续三轮零 HTTP/传输错误且 Commit 全部完成的档位；"
                   f"H={boundary.get('first_nonzero_error')} 是首个三轮复测出现非零错误的档位。"
                   "两者都不是崩溃/OOM/不可恢复意义上的硬容量上限。")
    elif confirmed:
        blocker = "相邻通过/失败档已确认；DAU 仍是固定业务行为与峰均比下的条件估算。"
    elif report.get("status") == "BLOCKED":
        blocker = "真实语义召回前置验证未通过，容量阶梯尚未形成有效边界。"
    else:
        blocker = "已有探索数据仍需相邻成功/失败档、三次复测及混合负载确认。"
    if confirmed and maximum == 0:
        remaining = [
            "H=2 及以上按最低档失败的 fail-fast 规则跳过；它们不是确认最大值为 0 所必需的分母。",
            "纯 Commit 饱和曲线、10×记忆规模、热点倾斜、写读重叠和 auto-commit 可作为后续诊断执行，不能改变本合同下 H=1 已失败的结论。",
            "若要评估另一条产品 SLO 或另一种每用户请求率，必须作为新合同重新运行，不能事后放宽本轮 2.5 秒门槛。",
        ]
    else:
        remaining = [
            "跨租户：U=1，T 从 2/4/8/16/32 开始递增；同租户：T=4，U 从 1/2/4/8/16 递增。",
            "纯召回与 70% 召回 + 30% 日常问题分别测试；混合负载每用户每分钟 2 条新消息、每 5 分钟一次显式 Commit，独立异步路径。",
            "30 秒预热、60 秒探索；成功/失败相邻档三次独立确认，纯召回每次至少 180 秒，混合至少 300 秒并覆盖每用户非空 Commit；样本或置信区间不足继续增加测量。",
            "发出/计划 ≥95%，每身份每类有效召回 ≥99%、端到端 P95 <2.5 秒；recall 类 atomic 执行 P95 <2 秒。Commit 180 秒内完成率 ≥99%，末段积压不持续增长。",
            "验证 1×/10× 记忆规模与实际 auto-commit；所有档均通过只能报告‘至少 H’，没有下一失败档不能称最大值。",
        ]
    remaining_html = "".join(f"<li>{escape(item)}</li>" for item in remaining)
    dau_empty = ("容量边界为 0，因此在当前 SLO 下不生成正 DAU。" if confirmed and maximum == 0
                 else "容量边界未确认，不生成 DAU。")
    capacity_conclusion = ("当前合同下 H=1 已失败，最大热用户数为 0；修复低负载误召回或端到端延迟后必须重新运行，不能沿用本轮边界。"
                           if confirmed and maximum == 0 else
                           "零错误完成档不是最大容量；继续升档并分别报告错误率、有效吞吐和硬失败证据。"
                           if zero_error_confirmed else
                           "先修复或解释健康低负载召回问题，再测最大热用户边界；不降低质量门槛换取容量数字。")
    download_links = '<a href="report.json">下载脱敏结构化证据</a>'
    if not report.get("publication", {}).get("redacted"):
        download_links += ' · <a href="seed-evidence.json">下载记忆验证证据</a>'
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>
:root{{font-family:system-ui,-apple-system,"PingFang SC",sans-serif;color:#20282d;background:#f5f7f8;line-height:1.65;letter-spacing:0}}
body{{margin:0}}main{{max-width:1240px;margin:auto;padding:28px}}h1{{font-size:28px;line-height:1.3}}h2{{font-size:21px;margin:28px 0 12px}}
header{{border-bottom:2px solid #1b7869;padding-bottom:20px}}section{{padding:12px 0;border-bottom:1px solid #d3dade}}
.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:18px;margin:22px 0}}.metric{{border-top:3px solid #1b7869;padding:12px 0}}.metric strong{{display:block;font-size:25px}}
.warn{{color:#a82435}}.note{{color:#56636b}}.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:14px;background:white}}th,td{{padding:10px 12px;text-align:left;border-bottom:1px solid #dde3e6;vertical-align:top}}th{{background:#e9efef;white-space:nowrap}}code{{overflow-wrap:anywhere}}a{{color:#176aac}}
.track{{height:24px;background:#efcdd2;overflow:hidden;border-radius:4px}}.fill{{height:24px;background:#20836f}}summary{{cursor:pointer;font-weight:600;padding:14px 0}}details table{{min-width:1000px}}
.series{{height:110px;display:flex;align-items:flex-end;gap:4px;border-bottom:1px solid #bcc8cd}}.series span{{flex:1;background:#318576;min-width:2px}}.series.cpu span{{background:#487caf}}
@media(max-width:700px){{main{{padding:16px}}.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}h1{{font-size:24px}}}}
</style><main><header><p class="note">M1 · 真实模型 · 黑盒 HTTP · 独立压测端</p><h1>{title}</h1>
<p class="warn">{conclusion}</p><p>{blocker}</p></header>
{render_platform_provenance(environment.get('platform_provenance'))}
<div class="grid"><div class="metric">最大热用户数<strong>{fmt(maximum)}</strong></div>
<div class="metric">三轮零错误档<strong>{fmt(zero_error_level)}</strong></div>
<div class="metric">DAU<strong>{_dau_label(dau)}</strong></div>
<div class="metric">语义事实有效召回<strong>{correct} / {query_count}</strong></div>
<div class="metric">有效容量窗口<strong>{sum(not l.get('diagnostic_only', False) for l in snapshots)}</strong></div></div>
<section><h2>方案来源与验收边界</h2><p>参考 <a href="https://github.com/tech-innovation-group/EchoMem/pull/397">PR397</a> 的 S1 基线、S11 容量阶梯和内存/吞吐/队列三维约束，
以及 <a href="https://github.com/tech-innovation-group/EchoMem/pull/421">PR421</a> 的单实例规格、租户缓存和调度限制。当前单项验收以 9 月 7 日更新的六指标方案为准。</p>
<p>PR397 的原始 S11 是每档 10 分钟、Zipf 标准用户负载；本方案的 60 秒阶段只用于快速探索，不等同于完成原始 S11。
PR421 文档中的 40–60 DAU、16 热租户（hard_cap 20）属于设计估算与配置目标，<strong>不是本次实测数字</strong>。
本次还区分 T 个租户与每租户 U 个用户，不能将缓存租户数直接当成热用户上限。</p></section>
<section><h2>1. 环境与版本</h2><p>服务器：{fmt(environment.get('host'))}；容器限制：{fmt(environment.get('cpus'))} CPU / {fmt(environment.get('memory_bytes'))} 字节。
容器内存上限不等于宿主机始终有同等空闲内存；宿主机并非独占，未证明没有邻居负载干扰。</p>
<p>EchoMem：<code>{fmt(environment.get('echomem_commit'))}</code><br>develop 基线：<code>{fmt(environment.get('develop_commit'))}</code></p>
<p>配置文件 SHA256：<code>{fmt(environment.get('config_sha256'))}</code>；意图模型 thinking：{fmt(environment.get('intent_thinking_enabled'))}。
这里只记录可核对的摘要，不导出配置正文或凭证。</p>
<p>负载由独立 runner 发起；无 mock。每个用户使用独立认证身份，预先写入固定事实，Search 不传 seed session，避免会话上下文直接泄露答案。</p>
<div class="scroll"><table><tr><th>类型</th><th>模型</th><th>预检状态</th><th>HTTP</th><th>耗时 s</th></tr>{provider_rows}</table></div></section>
<section><h2>2. 真实记忆准备与验证</h2><p>每用户 5 段文字、20 项固定事实、40 道改写问题。Commit 的 completed 与 Search 的实际事实命中分别验证。
随机 marker 仅作为独立诊断，不替代语义问题；各题的预期答案在注入前固定。</p>
<div class="track" role="img" aria-label="有效召回 {correct}/{query_count}"><div class="fill" style="width:{width:.2f}%"></div></div>
<p><strong>命中预期事实 {fact_hits}/{query_count}</strong>；同时满足 HTTP 200、命中事实、无降级的严格有效召回 {correct}/{query_count}。
空结果 {empty}，降级 {degraded}（分类可能重叠）。不能将严格有效数为零解释为记忆全部丢失。</p>
<p>串行语义预检平均耗时 {fmt(sum(latency)/len(latency) if latency else None, ' s')}；P95 {fmt(percentile(latency,95),' s')}。
这些是准备阶段数据，<strong>不是并发压测延迟</strong>；失败请求也计入耗时。</p>
<div class="scroll"><table><tr><th>身份</th><th>文档</th><th>字符</th><th>Commit HTTP</th><th>终态</th><th>有效/已发</th><th>已发/计划</th><th>平均</th><th>P95</th><th>状态</th></tr>{''.join(actor_rows)}</table></div>
<h3>固定问法校准</h3><div class="scroll"><table><tr><th>候选模板</th><th>计划</th><th>严格有效</th><th>事实命中</th><th>降级</th><th>原因</th></tr>{calibration_rows or '<tr><td colspan="6">未附校准数据。</td></tr>'}</table></div>
<p>容量运行只使用校准规则预先选出的前两种 20/20 模板；所有候选都展示，避免只披露通过样本。问法校准是样本合同验证，不计入容量SLO。</p>
<h3>降级原因</h3><ul>{reasons_html or '<li>没有已记录的降级原因；这不等于召回成功。</li>'}</ul></section>
<section><h2>阶段对照：失败证据完整保留</h2><div class="scroll"><table><tr><th>轮次</th><th>样本契约</th><th>意图 thinking</th><th>已测题</th><th>事实命中</th><th>严格有效</th><th>降级</th><th>invalid_output</th><th>阶段结论</th></tr>{''.join(attempts_html) or '<tr><td colspan="9">本文件只有当前轮次。</td></tr>'}</table></div>
<p>样本契约 source-ambiguous-v1 包含“之前记录的”等可能指向资源的措辞；explicit-chat-memory-v2 明确指向聊天记忆。
后者用于纯 memory recall 场景，不能代替面向文档/资源的查询覆盖。模型配置修复前后如重新注入了记忆，不能将全部差异只归因于配置；措辞对照复用同一份已注入记忆。</p></section>
<section><h2>3. 容量阶梯与诊断窗口</h2><div class="scroll"><table><tr><th>T</th><th>H</th><th>负载</th><th>时长 s</th><th>计划</th><th>发出</th><th>有效</th><th>平均 s</th><th>P95 s</th><th>P99 s</th><th>降级</th><th>超时</th><th>Atomic P95 s</th><th>未归属残差 P95 s</th><th>有效 RPS</th><th>结论</th></tr>{''.join(level_rows) or '<tr><td colspan="16">未开始有效的容量阶梯，不宣称“最多支持 2 人”，也不折算 DAU。</td></tr>'}</table></div>
<p>T 是租户数，U 是每租户热用户数，H=T×U。每个热用户计划每秒 1 次 Search；每身份、每类问题单独计算成功率与 P95。
标有“诊断”的窗口是在健康门槛未通过时收集现象，不能用于确认容量边界，也不能拿它的有效 RPS 折算 DAU。</p>
<h3>Search 错误完整拆分</h3><div class="scroll"><table><tr><th>H</th><th>窗口</th><th>已发出分母</th><th>严格成功</th><th>HTTP 200质量失败</th><th>其中降级</th><th>HTTP非200</th><th>4xx</th><th>401/403</th><th>429</th><th>5xx</th><th>传输错误</th><th>超时</th><th>传输类型</th><th>EchoMem reason_code</th><th>未分类</th><th>分母对账</th></tr>{''.join(error_rows) or '<tr><td colspan="17">尚无请求数据。</td></tr>'}</table></div>
<p class="note">每个已发请求只进入一个结果类别，分母对账为“是”时类别之和等于已发出数。401/403 仅证明当前 HTTP 层鉴权或权限失败；没有上游 provider 明确原因码或日志时，不能据此断言模型 API Key 异常。</p>
<p>“未归属残差”是 Search 端到端耗时减去 Explain 中已报告引擎耗时，包含意图路由、模型调用、序列化及未上报工作，<strong>不能直接当成 LLM 精确耗时</strong>；它用于区分检索引擎本身与外围编排瓶颈。</p>
{render_route_paths(route_summaries)}
<p>“意图 LLM 路径”表示 <code>executed_layers</code> 中包含 LLM 层；“快速路径”表示 Explain 已提供且未执行 LLM；缺少 Explain 的样本单列为“路由层未观测”。这里统计的是整条 Search 的端到端耗时，不冒充 LLM 自身精确耗时。是否启用 Thinking 以环境配置摘要为准。</p>
<h3>短窗口探索（仅用于选边界）</h3><div class="scroll"><table><tr><th>T</th><th>H</th><th>负载</th><th>时长 s</th><th>发出</th><th>有效</th><th>P95 s</th><th>Atomic P95 s</th><th>结论</th></tr>{''.join(exploration_rows) or '<tr><td colspan="9">未附短窗口探索证据。</td></tr>'}</table></div>
<p>探索窗口只决定确认档位，不参与最终最大值和 DAU 分子。</p>
<h3>资源趋势（预热 + 诊断窗口）</h3><p>RSS 峰值 {fmt(max(rss) if rss else None, ' MiB')}；CPU 峰值 {fmt(max(cpu) if cpu else None, '%')}，100% 表示占满一核，4核上限约400%。
资源样本 {len(resources)} 条；RSS 与容器总内存占用不同，图纵轴各自归一到本段观察峰值，不代表8GiB已用满。</p>
<p>RSS</p><div class="series">{bars(rss)}</div><p>CPU</p><div class="series cpu">{bars(cpu)}</div></section>
<section><h2>4. 三轮边界确认</h2><div class="scroll"><table><tr><th>H</th><th>轮次</th><th>窗口</th><th>灌种</th><th>结论</th><th>发出</th><th>有效</th><th>有效率</th><th>Search P95 s</th><th>Atomic P95 s</th><th>Commit 202</th><th>Commit 完成</th><th>Commit P95 s</th></tr>{''.join(repeat_rows) or '<tr><td colspan="13">尚未完成三轮独立确认。</td></tr>'}</table></div>
<p>每一轮都使用全新租户、用户、记忆和会话，并在测量前重启专用目标容器。确认结论基于三轮合并后的 10 秒分块 bootstrap P95 区间与 Wilson 有效率区间；单轮偶然通过不构成容量边界。</p></section>
<section><h2>5. 按身份与查询类型验收</h2><div class="scroll"><table><tr><th>H</th><th>窗口</th><th>身份</th><th>查询类型</th><th>发出</th><th>严格有效</th><th>有效率</th><th>Wilson 95%</th><th>P95 s</th><th>P95 bootstrap 95%</th><th>Atomic P95 s</th><th>降级</th><th>错误</th></tr>{''.join(cell_rows) or '<tr><td colspan="13">确认聚合尚未完成。</td></tr>'}</table></div>
<p>recall 必须命中预先锁定的事实且无降级；no-recall 必须保持空召回。二者分别验收，任何身份或查询类型不通过都不能用总体平均掩盖。</p></section>
<section><h2>6. 未执行范围与证据边界</h2><ol>{remaining_html}</ol>
<p>当前不能把串行预检失败归因为并发容量不足，也不能用其他日期、其他版本的测试结果替代。</p></section>
<section><h2>7. DAU 如何计算</h2><p>条件估算 DAU = 同一业务负载下的有效 RPS × 86400 ÷ 每用户每日请求数 ÷ 峰均比。</p>
<div class="scroll"><table><tr><th>Search/用户/天</th><th>Commit/用户/天</th><th>峰均比</th><th>Search 限制 DAU</th><th>Commit 限制 DAU</th><th>保守 DAU</th><th>稳态 Commit/s</th></tr>{''.join(dau_rows) or f'<tr><td colspan="7">{dau_empty}</td></tr>'}</table></div>
<p>这里锁定 PR397 标准用户假设：每日 50 次 Search、40–60 次 Commit，峰均比 1/3/5。Commit 能力取实际完成速率与“每热用户 300 秒一次”的稳态供给速率二者较小值，避免有限窗口多落入一次 Commit 而虚高。即使得到 DAU，也只是这些业务假设下的折算，不是全天在线人数或长稳态承诺。</p></section>
<section><h2>8. 责任归属与下一步</h2><p><strong>测试平台：</strong>随机标记不能充当唯一语义门槛；已保留诊断并继续固定问题验证。未发出请求、超时、降级和失败必须保留分母。</p>
<p><strong>EchoMem / 部署：</strong>若语义查询存在 invalid_output 或 engine_not_enabled，需要结合路由 Explain 与生效配置定位。模型可达、Commit completed 都不能证明事实可被召回，当前证据不足以直接归因到记忆丢失。</p>
<h3>剩余资源域降级：判定来自哪一层</h3><div class="scroll"><table><tr><th>题号</th><th>固定合成问题</th><th>资源域判定来源</th><th>资源域</th><th>实际执行层</th></tr>{route_rows or '<tr><td colspan="5">尚未采集独立 Explain 对照。</td></tr>'}</table></div>
<p>当资源域由 semantic 层判为 yes 且 LLM 层未执行时，更换 LLM 不能修复这条分支。
需要明确部署是否支持资源域：若支持则启用并验证资源引擎；若只支持聊天记忆，则应由 EchoMem 团队评审意图路由与引擎可用性的契约。测试平台不能自行隐藏该降级。</p>
<h3>意图模型兼容性实测</h3><p>模型 {fmt(router.get('model'))}；路由代码要求输出一个 A–H 字母，运行时输出预算为 {fmt(router.get('runtime_max_tokens'))} token。
下表是独立诊断请求，不计入容量负载或 80 题召回分母。仅记录响应结构，没有导出模型原文。</p>
<div class="scroll"><table><tr><th>诊断问题</th><th>token 预算</th><th>正文字符</th><th>推理字符</th><th>合法标签</th><th>停止原因</th></tr>{router_rows or '<tr><td colspan="6">尚未采集</td></tr>'}</table></div>
<p>正文为空而推理内容非空、finish_reason=length 时，增加少量 token 不一定得到合法标签。需要先验证配置中的非推理模式或源码默认专用意图模型，再重新运行健康性验证；不能把推理文字当路由标签，也不能忽略 invalid_output。</p>
<p><strong>容量结论：</strong>{capacity_conclusion}</p></section>
<details><summary>逐题原始观测（{len(queries)} 条）</summary><div class="scroll"><table><tr><th>身份</th><th>题号</th><th>HTTP</th><th>条目</th><th>事实</th><th>结论</th><th>耗时</th><th>降级原因</th><th>路由层</th><th>领域判定</th></tr>{''.join(detail_rows)}</table></div></details>
<p>{download_links}</p>
<p>诊断窗口的请求明细及资源样本另存为原始 JSON；本报告没有把种子 Commit 当作混合负载 Commit 容量结果。</p>
</main></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preflight", type=Path)
    parser.add_argument("--router-diagnostics", type=Path)
    parser.add_argument("--prior-run", type=Path, action="append", default=[])
    parser.add_argument("--route-failures", type=Path)
    parser.add_argument("--query-calibration", type=Path)
    parser.add_argument("--exploration", type=Path)
    parser.add_argument("--seed-evidence", type=Path, action="append", default=[])
    parser.add_argument("--resources", type=Path, action="append", default=[])
    parser.add_argument("--assessment-mode", choices=("observe", "completion", "slo"), default="observe")
    args = parser.parse_args()
    report = json.loads(args.input.read_text())
    if args.assessment_mode == "observe":
        report.update(assessment_mode="observe", max_hot_users=None, dau=None,
                      boundary={"status": "NOT_ESTABLISHED"},
                      performance_requirements_applied=False)
    if args.preflight:
        report["providers"] = [{k: p.get(k) for k in ("kind", "model", "status", "code", "elapsed_s")}
                               for p in json.loads(args.preflight.read_text()).get("engines", [])]
    if args.router_diagnostics:
        report["router_compatibility"] = json.loads(args.router_diagnostics.read_text())
    if args.route_failures:
        report["route_failures"] = json.loads(args.route_failures.read_text())
    if args.query_calibration:
        report["query_calibration"] = json.loads(args.query_calibration.read_text())
    if args.exploration:
        report["exploration"] = json.loads(args.exploration.read_text(encoding="utf-8"))
    seed_paths = args.seed_evidence or sorted(args.input.parent.glob("level-*-repeat-*/seed-evidence.json"))
    if seed_paths:
        report["seed"] = _merge_seed_evidence(seed_paths)
    resource_paths = args.resources or sorted(args.input.parent.glob("level-*-repeat-*/*-resources.json"))
    if resource_paths:
        report["resources"] = [row for path in resource_paths
                               for row in json.loads(path.read_text(encoding="utf-8"))]
    if args.prior_run:
        report["attempts"] = [{"label": p.name, **{k: v for k, v in json.loads(p.read_text()).items()
                               if k in ("seed", "query_contract", "environment", "status")}} for p in args.prior_run]
        report["attempts"].append({"label": "当前轮次", **{k: report[k] for k in
            ("seed", "query_contract", "environment", "status") if k in report}})
    evidence = args.output.parent / "report.json"
    if evidence.resolve() != args.input.resolve() or args.assessment_mode == "observe":
        evidence.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output.parent / "seed-evidence.json").write_text(
        json.dumps(report.get("seed", {}), ensure_ascii=False, indent=2), encoding="utf-8")
    args.output.write_text(render(report), encoding="utf-8")


if __name__ == "__main__":
    main()
