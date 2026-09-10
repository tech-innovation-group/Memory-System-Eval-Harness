"""Plain-language charts for the bounded Commit diagnostic, using measured data."""
from __future__ import annotations

from collections import Counter
import html
import json
from pathlib import Path

from .report import _check_detail, render_objective_suite_html


def _escape(value):
    return html.escape(str(value))


def _scene(result):
    profiles = result.get("profiles", [])
    if not profiles:
        return {}, {}
    profile = profiles[0]
    matrix = _check_detail(profile.get("concurrency_topology", {})).get("matrix", [])
    return profile, matrix[0] if matrix else {}


def _ratio(value, total):
    return f"{100 * value / total:.1f}%" if total else "未采集"


def _distribution(title, subtitle, segments, total, note):
    tiles = "".join(
        f'<i class="tile {color}" title="{_escape(label)} · 第{i + 1}次观察"></i>'
        for label, value, color in segments for i in range(value)
    )
    legend = "".join(
        f'<div class="legend"><span><i class="swatch {color}"></i>{_escape(label)}</span>'
        f'<b>{value}<small> / {total}</small></b><span>{_ratio(value, total)}</span></div>'
        for label, value, color in segments
    )
    return (f'<article class="plot"><h3>{_escape(title)}</h3><p class="sub">{_escape(subtitle)}</p>'
            f'<div class="tiles" role="img" aria-label="{_escape(title)}：'
            + _escape("，".join(f"{label}{value}次" for label, value, _ in segments))
            + f'">{tiles}</div>{legend}<p class="note">{_escape(note)}</p></article>')


def render_commit_diagnostic_html(result):
    profile, scene = _scene(result)
    if not scene:
        return render_objective_suite_html(result)
    level = scene["level"]
    operations = scene["operations"]
    commit, search = operations.get("commit", {}), operations.get("search", {})
    calls = commit.get("offered", 0)
    completed, timeout = commit.get("commit_completed", 0), commit.get("commit_timed_out", 0)
    failed = commit.get("commit_failed", 0)
    other = max(0, calls - completed - timeout - failed)
    search_rows = [row for row in scene.get("samples", []) if row.get("operation") == "search"]
    recall = Counter("healthy" if row.get("quality_ok") else "degraded_hit" if row.get("recall_hit")
                     else "miss" if row.get("quality_observed") else "unknown" for row in search_rows)
    observed = profile.get("objectives", [{}])[0].get("observed", {})
    model = observed.get("parameters", {}).get("models", {}).get("llm", "见运行证据")
    events = observed.get("service_events", {})
    queue_text = (f"{timeout} 次写入等待超过观察期限" if timeout else
                  f"{failed} 次写入返回失败" if failed else "写入调用均已观察到完成")
    search_text = (f"{search.get('search_degraded', 0)} 次检索出现降级" if search.get("search_degraded")
                   else "本轮未记录检索降级")
    commit_segments = [("观察到完成", completed, "green"), ("90秒内未完成", timeout, "amber")]
    if failed:
        commit_segments.append(("服务返回失败", failed, "red"))
    if other:
        commit_segments.append(("其他 / 缺少终态", other, "gray"))
    search_segments = [("命中且无降级", recall["healthy"], "green"),
                       ("命中，但未满足健康检索条件", recall["degraded_hit"], "amber"),
                       ("未命中预期记忆", recall["miss"], "red")]
    if recall["unknown"]:
        search_segments.append(("缺少质量证据", recall["unknown"], "gray"))
    charts = _distribution("写入：提交之后，真正完成了吗？", "Commit 的 HTTP 202 只代表接收，不代表完成。",
        commit_segments, calls, "每格表示一次调用观察，不是独立任务；格子按结果分组，不代表时间顺序。")
    charts += _distribution("检索：返回成功，就代表找对了吗？", "HTTP 200、找到了记忆、没有降级，是三个不同条件。",
        search_segments, len(search_rows), "降级意味着部分检索路径未正常参与；即使找到了目标记忆，也不计入健康命中。")
    comparison = ""
    if result.get("comparison"):
        _, old = _scene(result["comparison"])
        if old:
            old_ops = old["operations"]
            pairs = [
                ("写入完成观察占比", "%", "green", "越高越好；分母是全部 Commit 调用。",
                 100 * old_ops["commit"]["commit_completed"] / old_ops["commit"]["offered"], 100 * completed / calls),
                ("Commit 操作 P95", "秒", "amber", "包含写入、提交、轮询及超时等待；不是纯模型耗时。",
                 old_ops["commit"]["p95_ms"] / 1000, commit["p95_ms"] / 1000),
                ("Search 接口 P95", "秒", "teal", "只表示响应速度，不能替代召回质量。",
                 old_ops["search"]["p95_ms"] / 1000, search["p95_ms"] / 1000),
                ("健康检索占比", "%", "green", "必须同时命中预期记忆且没有降级。",
                 100 * old_ops["search"]["search_quality_ok"] / old_ops["search"]["offered"],
                 100 * search["search_quality_ok"] / search["offered"]),
            ]
            for title, unit, color, note, before, after in pairs:
                scale = 100 if unit == "%" else max(before, after, .01) * 1.1
                bars = ""
                for label, value, tone in ((old["level"], before, "gray"), (level, after, color)):
                    bars += (f'<div class="bar-row"><span>{label} 并发</span><div class="track">'
                             f'<div class="fill {tone}" style="width:{min(100, value / scale * 100):.3f}%"></div>'
                             f'</div><b>{value:.2f}<small>{unit}</small></b></div>')
                comparison += f'<article class="plot compare"><h3>{title}</h3>{bars}<p class="note">{note}</p></article>'
            comparison = (f'<section id="compare"><div class="section-head"><span>02</span><h2>从 {old["level"]} 到 {level} 并发，变化在哪里？</h2></div>'
                '<p class="sub">灰色是上一轮，彩色是本轮。P95 表示约95%的操作耗时不超过该值，不是平均值。</p>'
                f'<div class="plots">{comparison}</div><p class="note">两轮请求总数不同：上一轮 {old["offered"]} 次，本轮 {scene["offered"]} 次。'
                '这是负载档位对照，不是固定请求数或固定时长实验；不能据此单独推算最大容量。</p></section>')
    details = _escape(json.dumps(result, ensure_ascii=False, indent=2))
    method = _escape(result.get("method", ""))
    source = _escape(profile.get("objectives", [{}])[0].get("evidence", ""))
    checks = [row for row in profile.get("model_preflight", {}).get("engines", []) if row.get("model")]
    models = "".join(f'<tr><td>{_escape(row.get("id"))}</td><td>{_escape(row["model"])}</td>'
                     f'<td>{_escape(row.get("api_base"))}</td></tr>' for row in checks)
    metadata = observed.get("manifest", {})
    warning = ("存在未完成或降级，不能判定本轮全部通过" if timeout or failed or recall["healthy"] != len(search_rows)
               else "本轮有限样本全部完成；不代表已确定容量上限")
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{level}并发压测 · EchoMem</title>
<style>
:root{{--ink:#202724;--muted:#626c66;--line:#dce3dd;--green:#247758;--amber:#b96813;--red:#b84343;--teal:#267e91}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;color:var(--ink);background:#f7f9f7;font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;letter-spacing:0}}
main{{max-width:1160px;margin:auto;padding:32px 32px 60px}}nav{{display:flex;gap:20px;align-items:center;flex-wrap:wrap;border-bottom:1px solid var(--line);padding:0 0 18px;font-size:13px}}nav strong{{margin-right:auto}}a{{color:var(--teal);text-decoration:none}}a:hover{{text-decoration:underline}}
header{{padding:32px 0 24px}}.eyebrow{{font-size:13px;color:var(--muted)}}h1{{font-size:32px;line-height:1.3;margin:10px 0 14px}}h2{{font-size:23px;line-height:1.4;margin:0}}h3{{font-size:18px;line-height:1.5;margin:0 0 10px}}p{{margin:8px 0}}.lead{{font-size:18px;max-width:900px}}.status{{color:var(--amber);font-weight:650;font-size:14px}}.sub,.note{{color:var(--muted)}}.note{{font-size:13px;line-height:1.7;margin-top:16px}}.metrics{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:20px 0;gap:20px}}.metric{{border-right:1px solid var(--line);padding-right:12px}}.metric:last-child{{border:0}}.metric strong{{display:block;font-size:32px;font-variant-numeric:tabular-nums;line-height:1.4}}.metric small{{font-size:13px;color:var(--muted)}}section{{padding:30px 0;border-bottom:1px solid var(--line)}}.section-head{{display:flex;gap:12px;align-items:baseline;margin-bottom:14px}}.section-head>span{{color:var(--muted);font:13px ui-monospace,monospace}}
.plots{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:28px;margin-top:20px}}.plot{{min-width:0}}.plot+.plot{{border-left:1px solid var(--line);padding-left:28px}}.compare:nth-child(3){{border:0;padding-left:0}}.tiles{{display:grid;grid-template-columns:repeat(16,minmax(0,1fr));gap:5px;margin:22px 0;max-width:100%}}.tile{{display:block;aspect-ratio:1;border-radius:3px}}.green{{background:var(--green)}}.amber{{background:var(--amber)}}.red{{background:var(--red)}}.teal{{background:var(--teal)}}.gray{{background:#a9b7b0}}.legend{{display:grid;grid-template-columns:minmax(0,1fr) 82px 65px;gap:8px;border-bottom:1px solid var(--line);padding:8px 0;align-items:center}}.legend b,.legend>span:last-child{{text-align:right;font-variant-numeric:tabular-nums}}.swatch{{display:inline-block;width:10px;height:10px;margin-right:8px}}small{{font-size:12px;color:var(--muted);font-weight:400}}.bar-row{{display:grid;grid-template-columns:70px minmax(0,1fr) 92px;gap:12px;align-items:center;margin:14px 0;font-size:14px}}.bar-row b{{text-align:right;font-variant-numeric:tabular-nums}}.track{{height:18px;background:#e8ede9;border-radius:3px;overflow:hidden}}.fill{{height:100%;border-radius:3px}}
.callout{{border-left:3px solid var(--amber);padding:10px 18px;margin:22px 0;background:#fff}}.issues{{display:grid;gap:0}}.issue{{display:grid;grid-template-columns:190px minmax(0,1fr);gap:22px;padding:18px 0;border-bottom:1px solid var(--line)}}.issue:last-child{{border:0}}.issue strong{{font-size:17px}}.issue p{{margin:0}}.steps{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:20px;margin:20px 0}}.step b{{display:block;font-size:17px}}details{{margin:12px 0;border:1px solid var(--line);border-radius:4px;background:#fff;padding:12px 16px}}summary{{cursor:pointer;font-weight:600}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;max-height:480px;overflow:auto}}.table-wrap{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{padding:9px;border-bottom:1px solid var(--line);text-align:left;overflow-wrap:anywhere}}footer{{color:var(--muted);font-size:12px;padding-top:20px}}:focus-visible{{outline:3px solid var(--teal);outline-offset:4px}}
@media(max-width:720px){{main{{padding:20px 18px 40px}}h1{{font-size:26px}}h2{{font-size:21px}}.metrics{{grid-template-columns:repeat(2,minmax(0,1fr))}}.metric:nth-child(2){{border:0}}.plots{{grid-template-columns:1fr}}.plot+.plot{{border-left:0;padding-left:0;border-top:1px solid var(--line);padding-top:24px}}.compare:nth-child(3){{border-top:1px solid var(--line);padding-top:24px}}.issue{{grid-template-columns:1fr;gap:8px}}.steps{{grid-template-columns:repeat(2,minmax(0,1fr))}}.legend{{grid-template-columns:minmax(0,1fr) 70px 55px;font-size:13px}}.bar-row{{grid-template-columns:62px minmax(0,1fr) 82px;gap:8px}}nav{{gap:14px}}.lead{{font-size:16px}}}}
@media print{{body{{background:white}}main{{padding:0}}nav{{display:none}}details{{break-inside:avoid}}.plot{{break-inside:avoid}}}}
</style></head><body><main>
<nav><strong>EchoMem / 实测报告</strong><a href="#results">完成情况</a><a href="#compare">并发对比</a><a href="#issues">问题归属</a><a href="#method">测试方式</a></nav>
<header><div class="eyebrow">服务器 8.130.75.94 · 4 CPU / 8 GiB · {_escape(model)}</div>
<h1>{level}并发：写入和检索表现如何？</h1><p class="status">{warning}</p>
<p class="lead">{queue_text}；{search_text}。<br>接口接收成功，不等于后台写入完成，也不等于检索质量正常。</p></header>
<div class="metrics"><div class="metric">实际并发峰值<strong>{scene.get('observed_inflight_peak', '未采集')}</strong><small>目标 {level}，不是每秒请求数</small></div>
<div class="metric">Commit 完成观察<strong>{completed}<small> / {calls}</small></strong><small>{_ratio(completed,calls)} · 按调用统计</small></div>
<div class="metric">健康检索<strong>{recall['healthy']}<small> / {len(search_rows)}</small></strong><small>命中预期事实且无降级</small></div>
<div class="metric">主场景耗时<strong>{scene.get('elapsed_s',0):.1f}<small> 秒</small></strong><small>不含容器启动和前置检查</small></div></div>
<section id="results"><div class="section-head"><span>01</span><h2>请求最终怎么样了？</h2></div>
<div class="plots">{charts}</div><div class="callout"><b>{calls} 次 Commit 调用 ≠ {calls} 个独立任务</b><p>本轮涉及 {commit.get('commit_unique_archives',0)} 个独立归档任务，另有 {commit.get('commit_repeated_archive_observations',0)} 次调用观察复用了任务。完成、超时比例按调用计算，不能当成独立任务的成功率。</p></div>
<p class="note">观察超时只说明90秒内没等到 completed，不等于服务返回 failed。测试结束后专用容器已清理，未继续追踪这些任务最终是否完成。</p></section>
{comparison}
<section id="issues"><div class="section-head"><span>03</span><h2>问题分别在哪里？</h2></div><div class="issues">
<div class="issue"><strong>Commit 等待</strong><p>{timeout} 次调用观察超时，{failed} 次服务端失败。需要继续拆分排队、抽取、摘要和持久化耗时；仅凭总耗时不能认定是 DeepSeek 慢。</p></div>
<div class="issue"><strong>Search 意图识别</strong><p>日志记录 {events.get('recall_llm_failed',0)} 次意图识别失败事件。模型和端点见下方运行配置；HTTP异常缺少上游状态码时，不能直接认定为限流。事件数不是独立请求数。</p></div>
<div class="issue"><strong>引擎状态 / 检索降级</strong><p>本轮记录 {events.get('engine_event_failed',0)} 次引擎事件失败。具体引擎和错误类型保留在技术证据中；Search的降级原因不能仅凭前置样本推断为同一种。</p></div>
<div class="issue"><strong>是否模型限流？</strong><p>已分类的配额错误日志：{observed.get('model_quota_log_events',0)} 条。这不等于证明不存在限流，也不等于没有其他模型错误；欠费、Token限流和HTTP异常不能混为一谈。</p></div></div></section>
<section id="method"><div class="section-head"><span>04</span><h2>这次是怎么测的？</h2></div>
<div class="steps"><div class="step"><b>{scene.get('actual_users',0)} 个独立用户</b><span>每人1个主场景Session</span></div><div class="step"><b>每Session {scene.get('per_session_concurrency',0)} 并发</b><span>2人做Commit，2人做Search</span></div><div class="step"><b>{calls} 次写入 + {len(search_rows)} 次检索</b><span>总计 {scene.get('offered',0)} 次操作</span></div><div class="step"><b>最长观察90秒</b><span>提交后轮询Commit终态</span></div></div>
<p>{method}</p><details><summary>运行模型与供应商</summary><div class="table-wrap"><table><thead><tr><th>用途</th><th>模型</th><th>端点</th></tr></thead><tbody>{models}</tbody></table></div></details>
<details><summary>技术证据：错误分类、配置、逐调用结果</summary><p class="note">证据目录：{source}</p><pre>{details}</pre></details></section>
<footer>生成时间：{_escape(result.get('created_at',''))} · 测试镜像：{_escape(metadata.get('image','未采集'))}<br>只报告本次实际观测，不把HTTP成功当业务成功，不把缺失证据当作通过。</footer>
</main></body></html>'''


def write_commit_diagnostic_html(result, path: Path):
    path.write_text(render_commit_diagnostic_html(result), encoding="utf-8")
