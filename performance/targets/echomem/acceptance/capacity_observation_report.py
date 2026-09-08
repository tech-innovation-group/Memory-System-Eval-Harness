"""Measurement-first capacity report, without an implicit performance SLO."""

from __future__ import annotations

from html import escape

from performance.targets.echomem.acceptance.capacity_confirmation import estimate_dau
from performance.targets.echomem.acceptance.route_path_report import render_route_paths


def _fmt(value):
    if value is None:
        return "未采集"
    return f"{value:.3f}" if isinstance(value, float) else escape(str(value))


def _table(headers, rows):
    body = "".join("<tr>" + "".join(f"<td>{_fmt(v)}</td>" for v in row) + "</tr>" for row in rows)
    return '<div class="scroll"><table><thead><tr>' + "".join(
        f"<th>{escape(h)}</th>" for h in headers) + "</tr></thead><tbody>" + (
        body or f'<tr><td colspan="{len(headers)}">尚无数据</td></tr>') + "</tbody></table></div>"


def _bars(title, points, unit):
    maximum = max((p[1] for p in points if p[1] is not None), default=0) or 1
    rows = "".join(f'<div class="barrow"><span>{escape(label)}</span><div class="track">'
                   f'<span style="width:{100 * value / maximum:.2f}%"></span></div>'
                   f'<b>{value:.3f} {escape(unit)}</b></div>' for label, value in points if value is not None)
    return f'<div><h3>{escape(title)}</h3>{rows or "尚无数据"}</div>'


def render_observation(report: dict) -> str:
    env = report.get("manifest") or report.get("environment") or {}
    levels = []
    repeats = []
    for level in report.get("levels", []):
        if "search" in level:
            levels.append(level)
        else:
            for key in ("pure_aggregate", "mixed_aggregate"):
                if level.get(key):
                    levels.append({**level[key], "hot_users": level["hot_users"]})
            for repeat in level.get("repeats", []):
                for mode in ("pure", "mixed"):
                    if mode in repeat:
                        repeats.append({**repeat[mode], "hot_users": level["hot_users"],
                                        "repeat": repeat["repeat"]})
    highest = max((l.get("hot_users", l.get("identity_count", 0)) for l in levels
                   if l.get("search", {}).get("sent")), default=None)
    total_sent = sum(l["search"]["sent"] for l in levels)
    total_errors = sum(l["search"]["errors"] for l in levels)
    summary_rows, detail_rows, class_rows, commit_rows, dau_rows, issue_rows = [], [], [], [], [], []
    route_summaries = []
    p95_points, rps_points = [], []
    for level in levels:
        h = level.get("hot_users", level.get("identity_count"))
        name = {
            "search": "纯 Search",
            "commit": "纯 Commit",
            "mixed": "混合负载",
            "hotspot": "热点租户",
        }.get(level.get("load_mode"), "混合读写" if level.get("mixed") else "纯 Search")
        search, commit = level["search"], level.get("commit", {})
        limits = level.get("resource_summary", {})
        rss_peak = limits.get("rss_peak_bytes")
        summary_rows.append([h, name, search.get("p95_s"), level.get("sent_search_rps"),
            (search.get("http_status_counts", {}).get("200", 0) / level["duration_s"]),
            level.get("effective_search_rps"), search.get("transport_or_http_errors"),
            f"{search.get('success', 0)}/{search.get('sent', 0)}",
            limits.get("cpu_peak_percent_one_core_100"),
            rss_peak / 1048576 if rss_peak is not None else None])
        detail_rows.append([h, level.get("tenant_count"), name, level.get("duration_s"),
            search.get("planned"), search.get("sent"), search.get("success"),
            search.get("mean_s"), search.get("p50_s"), search.get("p95_s"), search.get("p99_s"),
            search.get("timeout_censored"), search.get("not_sent"), search.get("degraded"),
            str(search.get("http_status_counts", {}))])
        p95_points.append((f"H={h} {name}", search.get("p95_s")))
        rps_points.append((f"H={h} {name}", level.get("effective_search_rps")))
        for cell in level.get("cells", []):
            class_rows.append([h, name, cell.get("identity_index"), cell.get("query_type"),
                cell.get("sent"), cell.get("success"), cell.get("fact_hits"),
                cell.get("nonempty_results"), cell.get("degraded"), cell.get("mean_s"),
                cell.get("p95_s"), cell.get("atomic_p95_s"),
                str(cell.get("p95_block_bootstrap_95")), str(cell.get("quality_wilson_95"))])
        if level.get("load_mode") in {"commit", "mixed", "hotspot"}:
            recovery_samples = level.get("recovery", {}).get("samples", [])
            commit_rows.append([h, commit.get("submitted"), commit.get("accepted_202"),
                commit.get("completed"), commit.get("completed_in_window"),
                sum(r.get("completed_since_poll", 0) for r in recovery_samples) if recovery_samples else None,
                sum(r.get("failed_since_poll", 0) for r in recovery_samples) if recovery_samples else None,
                commit.get("not_accepted"), commit.get("unfinished_or_failed"),
                commit.get("p95_s"), commit.get("completed_rps"),
                max((b["pending"] for b in commit.get("backlog", [])), default=None)])
            for row in estimate_dau(level, peak_factors=(3,)):
                dau_rows.append([h, row["searches_per_user_day"], row["commits_per_user_day"],
                    row["search_peak_factor"], row["search_limited_dau"], row["commit_limited_dau"],
                    row["conservative_dau"]])
        if search.get("degraded"):
            issue_rows.append([h, name, "路由 / 引擎可用性", search["degraded"],
                               str(search.get("degraded_reason_counts", "旧汇总未保留原因"))])
        if search.get("transport_or_http_errors"):
            issue_rows.append([h, name, "HTTP / 传输", search["transport_or_http_errors"],
                               str(search.get("http_status_counts", {}))])
        issue_rows.append([h, name, "原子检索 / 编排耗时", search.get("atomic_p95_s"),
            f"Atomic P95(s)；端到端减已上报引擎耗时的残差 P95={_fmt(search.get('unattributed_residual_p95_s'))}s"])
        route_summaries.append((f"H={h} {name}", search))
    repeat_rows = [[r.get("hot_users"), r.get("repeat"), "混合" if r.get("mixed") else "纯召回",
                   r["search"]["sent"], r["search"]["success"], r["search"]["p95_s"],
                   r.get("commit", {}).get("completed")] for r in repeats]
    seed = report.get("seed_summary") or {}
    if not seed and report.get("seed"):
        actors = report["seed"].get("actors", [])
        qs = [q for a in actors for q in a.get("queries", [])]
        seed = {"actors": len(actors), "queries": len(qs), "strict_valid": sum(bool(q.get("success")) for q in qs)}
    resource_rows = report.get("resources") or []
    resource = report.get("resources_summary") or {}
    if not resource:
        resource = {"samples": len(resource_rows),
                    "rss_peak_bytes": max((r.get("rss_bytes") or 0 for r in resource_rows), default=None),
                    "cpu_peak_percent_one_core_100": max((r.get("cpu_percent_one_core_100") or 0
                                                           for r in resource_rows), default=None)}
    rss = resource.get("rss_peak_bytes")
    plan = _table(["范围", "当前数据"], [
        ["M1-HOT 跨租户", f"本报告最高已测 H={highest}；各档位均逐项列出。"],
        ["同租户多用户 / 大规模记忆", "本次未覆盖；不能由跨租户小记忆数据推算。"],
        ["纯 Commit 饱和 / 混合速率矩阵", "当前仅为用户画像下读写点位；纯提交饱和曲线及更多速率组合待测。"],
        ["热点倾斜 / 长文本写读重叠 / auto-commit", "待测，不计为已完成。"],
        ["其余五项指标", "按顺序继续测试，当前报告仅对应 M1。"],
    ])
    operational = report.get("operational_boundary")
    legacy = report.get("max_hot_users") == 0 or report.get("retracted_legacy_slo", False)
    notice = ("之前由性能门槛推导的最大热用户数=0、DAU=0结论已撤销；当前不使用该口径。" if legacy else
              "未使用性能合格线。最高已测用户数不是最大用户数，缺少运行故障边界时上限仍未知。")
    if operational and operational.get("hot_users") is not None:
        evidence = operational.get("evidence") or {}
        boundary_text = (f"在 H={operational['hot_users']} 观察到：{evidence.get('reason', '运行故障')}。"
                         f"恢复观察窗口 {evidence.get('recovery_window_s', '未记录')} 秒，"
                         "需结合前一档与故障证据解释，不能外推为永久无法恢复。")
    else:
        tested = (operational or {}).get("highest_tested_hot_users", highest)
        boundary_text = (f"已测到 H={tested}，尚未提供崩溃、OOM或停止发压后无法恢复的边界证据；"
                         "该数字只是最高观察档，不是容量上限。")
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>4U8G 热用户与 DAU 实测数据</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f5f7f8;color:#202a30;font:15px/1.7 system-ui,-apple-system,"PingFang SC",sans-serif;letter-spacing:0}}main{{max-width:1400px;margin:auto;padding:26px}}h1{{font-size:28px;margin:6px 0 12px}}h2{{font-size:21px;margin:24px 0 12px}}h3{{font-size:16px}}header,section{{border-bottom:1px solid #cdd7dc;padding:18px 0}}.muted{{color:#566773}}.notice{{border-left:4px solid #b74c39;padding:8px 14px;background:#fff4ec}}.kpis{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:18px;margin:22px 0}}.kpis div{{border-top:3px solid #258577;padding:10px 0}}.kpis strong{{display:block;font-size:26px}}.charts{{display:grid;grid-template-columns:1fr 1fr;gap:30px}}.barrow{{display:grid;grid-template-columns:112px minmax(0,1fr) 85px;align-items:center;gap:10px;margin:12px 0;font-size:13px}}.barrow b{{text-align:right;font-variant-numeric:tabular-nums}}.track{{height:15px;background:#e0e6e8}}.track span{{display:block;height:100%;background:#268978}}.charts>div+div .track span{{background:#4c7fba}}.scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;background:white;font-size:13px}}th,td{{padding:9px 12px;border-bottom:1px solid #dce3e6;text-align:left;vertical-align:top}}th{{white-space:nowrap;background:#e8eff1}}td{{overflow-wrap:anywhere}}code{{overflow-wrap:anywhere}}summary{{cursor:pointer;font-weight:600;padding:12px 0}}a{{color:#1568a3}}@media(max-width:720px){{main{{padding:14px}}.kpis{{grid-template-columns:1fr 1fr}}.charts{{grid-template-columns:1fr}}h1{{font-size:24px}}}}
</style></head><body><main><header><p class="muted">M1 · 服务器实测 · 4 CPU / 8 GiB · 真实模型</p><h1>单实例热用户与 DAU 实测数据</h1>
<p>按你的要求只呈现数据，不应用延迟、吞吐或准确率的合格门槛。记录所有未发出、错误、超时、降级和未完成 Commit。</p>
<p class="notice">{notice}</p><p>{escape(boundary_text)}</p></header>
<div class="kpis"><div>最高已测热用户<strong>{_fmt(highest)}</strong></div><div>Search 已发出<strong>{total_sent}</strong></div><div>严格有效性未满足<strong>{total_errors}</strong></div><div>绝对最大容量<strong>尚未确定</strong></div></div>
<section><h2>环境与负载</h2><p>服务器 {_fmt(env.get('host'))}；限制 {_fmt(env.get('cpus'))} CPU / {_fmt(env.get('memory_bytes'))} 字节。EchoMem <code>{_fmt(env.get('echomem_commit'))}</code>，develop <code>{_fmt(env.get('develop_commit'))}</code>。</p>
<p>配置摘要 <code>{_fmt(env.get('config_sha256'))}</code>。每热用户 Search 1/s，独立泊松到达；纯召回和 70% recall + 30% no-recall 混合组分别测量。混合组每用户约每分钟 2 条新消息，每 300 秒一次显式 Commit；搜索和写入独立并发。</p>
<p>记忆准备：{_fmt(seed.get('actors'))} 个身份，{_fmt(seed.get('strict_valid'))}/{_fmt(seed.get('queries'))} 道严格有效验证。当前窗口复用记忆：{_fmt(report.get('seed_reused'))}。配置和 API key 不包含在报告内。</p></section>
<section><h2>容量曲线</h2><div class="charts">{_bars('Search P95',p95_points,'s')}{_bars('严格有效 Search 吞吐',rps_points,'/s')}</div>
{_table(['H','负载','P95 s','发送/s','HTTP 200/s','有效召回/s','HTTP/传输错误','严格有效/已发出','CPU峰值 %','RSS峰值 MiB'],summary_rows)}
<p class="muted">H=T×U，用户请求率决定在途请求量。严格有效性同时要求 HTTP 正常、满足问题预期、无降级；其错误数不等于传输错误数。短窗口数字不代表全天稳态。</p></section>
<details><summary>各档完整计数、延迟与 HTTP 状态</summary>{_table(['H','租户 T','负载','时长 s','计划','发出','严格有效','平均 s','P50 s','P95 s','P99 s','超时','未发出','降级','HTTP 状态计数'],detail_rows)}</details>
<section><h2>Commit 实际执行</h2>{_table(['H','计划提交','受理 202','原期限内completed','其中负载窗口内','追加观察completed','追加观察failed','未受理','原期限内未成功','P95 s','窗口内完成/s','积压峰值'],commit_rows)}
<p>原期限为每个 Commit 受理后最多 180 秒；追加观察会重新对全部受理任务核对状态，因此 completed 数含原期限内已完成任务，不能相加。排空只表示无未终态任务，不保证全部成功。完成判定来自 commit_status，当前 M1 还没有逐条源消息对账；M5 单独核验持久化与恢复。</p></section>
<section><h2>DAU 条件换算</h2>{_table(['H','Search/人/天','Commit/人/天','峰均比','Search折算','Commit折算','两者较小值'],dau_rows)}
<p>表内采用参考画像：50 Search/人/天、40–60 Commit/人/天、峰均比 3。DAU=有效吞吐×86400÷每日次数÷峰均比。Commit 速率不超过每热用户 300 秒一次的稳态供给，避免短窗口首尾任务使结果虚高。以上是各已测点的流量等价值，尚未验证对应的全天行为、用户驻留和其他读写组合，不能称最大 DAU。</p></section>
<section><h2>模块观测</h2>{_table(['H','负载','模块','观测值','证据'],issue_rows)}<p>RSS 峰值 {_fmt(rss / 1048576 if rss is not None else None)} MiB；CPU 峰值 {_fmt(resource.get('cpu_peak_percent_one_core_100'))}%（100%=一核），采样 {_fmt(resource.get('samples'))} 次。容器限制不等于宿主资源独占。残差包含路由、模型调用和未上报环节，不能直接命名为模型耗时。</p></section>
<section>{render_route_paths(route_summaries)}
<p>意图 LLM 与快速路径根据响应 Explain 的 <code>executed_layers</code> 拆分；缺失 Explain 的样本保留在“路由层未观测”分母。表内是整条 Search 的端到端耗时，Atomic 检索耗时仍在模块观测和按身份表中单列。</p></section>
<section><h2>按身份和问题类型</h2>{_table(['H','负载','身份序号','类型','发出','严格有效','事实命中','非空返回','降级','平均 s','P95 s','Atomic P95 s','P95 95%区间','有效率95%区间'],class_rows)}
<p>no-recall 的非空返回才是误召回观测；仅凭严格有效性未满足或降级，不能断言发生了误召回。缺失字段显示“未采集”。</p></section>
<details><summary>已有三轮低负载数据</summary>{_table(['H','轮次','负载','发出','严格有效','P95 s','Commit完成'],repeat_rows)}</details>
<section><h2>覆盖范围</h2>{plan}</section><p><a href="report.json">下载脱敏统计 JSON</a></p></main></body></html>'''
