"""Canonical HTML renderer for the EchoMem executable test plan.

The plan renderer is intentionally separate from measurement aggregation: a plan
describes what will be sent and what evidence is required, while an observation
report describes what was actually received. Both artifacts stay inside the
repository acceptance reporting boundary.
"""

from __future__ import annotations

import html
import json
import math
from pathlib import Path
from typing import Any


def write_test_plan_report(plan: dict[str, Any], path: Path) -> None:
    """Write a self-contained, visual, evidence-oriented M1-M6 plan report."""

    def esc(value: Any) -> str:
        if value is None or value == "":
            return "未采集"
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        return html.escape(str(value))

    def table(
        rows: list[dict[str, Any]],
        columns: list[tuple[str, str]],
        *,
        min_width: int = 0,
    ) -> str:
        head = "".join(f"<th>{esc(label)}</th>" for _, label in columns)
        if rows:
            body = "".join(
                "<tr>"
                + "".join(f"<td>{esc(row.get(key))}</td>" for key, _ in columns)
                + "</tr>"
                for row in rows
            )
        else:
            body = f"<tr><td colspan='{len(columns)}'>暂无数据</td></tr>"
        style = f" style='min-width:{int(min_width)}px'" if min_width else ""
        return (
            f"<div class='scroll'><table{style}><thead><tr>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table></div>"
        )

    def bullets(items: list[Any], *, ordered: bool = False) -> str:
        tag = "ol" if ordered else "ul"
        content = "".join(f"<li>{esc(item)}</li>" for item in items)
        return f"<{tag}>{content or '<li>暂无</li>'}</{tag}>"

    def badge(value: Any) -> str:
        value_text = str(value or "计划")
        classes = {
            "PLAN_READY": "plan-ready",
            "PLAN_READY_WITH_GAPS": "plan-ready-with-gaps",
            "MEASURED": "measured",
            "PARTIAL": "partial",
            "BLOCKED": "blocked",
            "计划": "plan-ready-with-gaps",
        }
        return f"<span class='state {classes.get(value_text, 'unknown')}'>{esc(value_text)}</span>"

    def bars(points: list[dict[str, Any]], *, title: str, color: str = "teal") -> str:
        numeric: list[tuple[dict[str, Any], float]] = []
        for point in points:
            try:
                value = float(point.get("value"))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value >= 0:
                numeric.append((point, value))
        maximum = max((value for _, value in numeric), default=1.0) or 1.0
        rendered = []
        for point, value in numeric:
            width = min(100.0, max(0.0, value / maximum * 100.0))
            display = point.get("display")
            if display is None:
                display = value
            rendered.append(
                f"<div class='plan-bar'><span>{esc(point.get('label'))}</span>"
                f"<i><b class='{esc(color)}' style='width:{width:.2f}%'></b></i>"
                f"<strong>{esc(display)}</strong></div>"
            )
        return (
            f"<h3>{esc(title)}</h3>"
            + ("".join(rendered) or "<p>暂无可绘制数值；这里不填充推断值。</p>")
        )

    def flow(steps: list[dict[str, Any]]) -> str:
        chunks = []
        for index, step in enumerate(steps):
            chunks.append(
                f"<div class='flow-step'><b>{esc(step.get('label'))}</b>"
                f"<small>{esc(step.get('detail'))}</small></div>"
            )
            if index < len(steps) - 1:
                chunks.append("<span class='flow-arrow' aria-hidden='true'>-&gt;</span>")
        return "<div class='flow'>" + "".join(chunks) + "</div>" if chunks else ""

    profile = plan.get("profile") or {}
    state = plan.get("execution_state") or {}
    current = plan.get("current_evidence") or {}
    parameters = plan.get("concurrency_parameters") or {}
    metrics = plan.get("metrics") or {}
    resource = profile.get("resource_limits") or {}
    models = profile.get("models") or {}
    m1_levels = profile.get("m1_concurrency_levels") or [1, 8, 16, 64]
    m1_levels_label = "、".join(f"C={value}" for value in m1_levels)
    execution_matrix = plan.get("execution_matrix") or {}
    execution_section = (
        "<section id='execution-matrix'><h2>完整测试执行清单</h2>"
        f"<p class='notice'><b>执行规则：</b>{esc(execution_matrix.get('rule') or '完整测试必须执行所有 M1-M6 单元。')}</p>"
        + table(
            execution_matrix.get("rows") or [],
            [
                ("unit", "执行单元"),
                ("metric", "指标"),
                ("scenario", "场景"),
                ("status", "当前状态"),
                ("evidence", "已有证据"),
                ("next_step", "完成还需"),
            ],
            min_width=1800,
        )
        + f"<h3>所有执行单元共同保留的字段</h3>{bullets(execution_matrix.get('common_fields') or [])}"
        + f"<h3>固定执行顺序</h3>{bullets(execution_matrix.get('order') or [], ordered=True)}"
        + "</section>"
    )

    profile_rows = [
        {"key": "测试平台 profile", "value": profile.get("name"), "source": profile.get("source")},
        {"key": "EchoMem Endpoint", "value": profile.get("base_url"), "source": "profile.base_url"},
        {"key": "专用资源容器", "value": profile.get("resource_container"), "source": "profile.resource_container"},
        {"key": "资源限制", "value": f"CPU {resource.get('cpu', '未采集')}；内存 {resource.get('memory', '未采集')}", "source": "Docker/运行证据"},
        {"key": "LLM", "value": models.get("llm"), "source": "实际配置/预检"},
        {"key": "Embedding", "value": models.get("embedding"), "source": "profile + 预检"},
        {"key": "M1 当前接线", "value": f"topology={profile.get('m1_topologies')}; levels={profile.get('m1_concurrency_levels')}", "source": "profile"},
        {"key": "计划正式 M1 档位", "value": m1_levels_label, "source": "本方案"},
        {"key": "Soak", "value": "关闭；不属于本轮六项数据", "source": "运行约定"},
        {"key": "日志", "value": "DEBUG + JSON；窗口内保留白名单阶段", "source": "EchoMem 配置"},
    ]
    state_rows = [
        {"item": item.get("item"), "value": item.get("value"), "meaning": item.get("meaning")}
        for item in state.get("rows", [])
    ]

    metric_cards = "".join(
        f"<article class='metric-card'><div class='metric-code'>{esc(metric.get('code'))}</div>"
        f"<h3>{esc(metric.get('name'))}</h3><p>{esc(metric.get('reflects'))}</p>"
        f"{badge(metric.get('state', '计划'))}</article>"
        for metric in metrics
    )

    metric_sections = []
    for metric in metrics:
        case_rows = table(
            metric.get("cases") or [],
            [
                ("id", "用例"),
                ("goal", "目的"),
                ("actors", "租户/用户/Session"),
                ("load", "Search / Commit 负载"),
                ("window", "窗口与排空"),
                ("evidence", "必须留下的证据"),
            ],
            min_width=1250,
        )
        field_rows = table(
            metric.get("fields") or [],
            [
                ("field", "字段"),
                ("meaning", "含义"),
                ("denominator", "分母/口径"),
                ("source", "来源"),
            ],
            min_width=1100,
        )
        module_rows = table(
            metric.get("modules") or [],
            [("module", "责任模块"), ("observe", "看什么"), ("improve", "建议方向")],
            min_width=1000,
        )
        links = metric.get("evidence") or []
        links_html = "<ul>" + "".join(f"<li><code>{esc(item)}</code></li>" for item in links) + "</ul>"
        chart_html = bars(
            metric.get("chart") or [],
            title=str(metric.get("chart_title") or "计划负载形状"),
            color=str(metric.get("chart_color") or "teal"),
        )
        metric_sections.append(
            f"<section id='{esc(metric.get('code'))}' class='metric-section'>"
            f"<div class='section-kicker'>{esc(metric.get('code'))} · {badge(metric.get('state', '计划'))}</div>"
            f"<h2>{esc(metric.get('name'))}</h2>"
            f"<p class='purpose'><b>它反映什么：</b>{esc(metric.get('reflects'))}</p>"
            f"<div class='method'><b>怎么测：</b>{esc(metric.get('method'))}</div>"
            f"<p><b>本项与其他指标的边界：</b>{esc(metric.get('boundary'))}</p>"
            f"{flow(metric.get('flow') or [])}"
            f"<div class='visual-panel'>{chart_html}</div>"
            f"<h3>场景与用例</h3>{case_rows}"
            f"<h3>输出字段与分母</h3>{field_rows}"
            f"<div class='split'><div><h3>计算/判定</h3>{bullets(metric.get('formulas') or [])}</div>"
            f"<div><h3>完成条件与明确阻塞</h3>{bullets(metric.get('gaps') or [])}</div></div>"
            f"<h3>EchoMem 责任模块与改进方向</h3>{module_rows}"
            f"<h3>原始证据索引</h3>{links_html}</section>"
        )

    current_rows = current.get("rows") or []
    comparison = current.get("comparison") or {}
    timing = current.get("timing") or {}
    timing_rows = timing.get("rows") or []
    timing_section = (
        "<section><h2>当前 M1-M3 阶段耗时证据</h2>"
        f"<p class='notice'>{esc(timing.get('note') or '未提供 EchoMem JSON 阶段耗时快照；不能用端到端耗时相减推导模块耗时。')}</p>"
        + table(
            timing_rows,
            [
                ("event", "事件"),
                ("stage", "阶段"),
                ("observations", "样本数"),
                ("p50_ms", "P50 ms"),
                ("p95_ms", "P95 ms"),
                ("p99_ms", "P99 ms"),
                ("max_ms", "最大 ms"),
            ],
            min_width=1050,
        )
        + "<p class='muted'>责任判断：Commit/atomic extraction 的 P95 若达到分钟级，优先检查 LLM thinking、Provider 响应和原子引擎；HTTP/Recall engine 仅在真实样本显示同量级排队时才作为主瓶颈。M2/M3 没有运行样本时明确显示暂无数据。</p></section>"
        if timing_rows
        else ""
    )
    current_section = (
        "<section><h2>当前已有证据快照（不是完整六项结果）</h2>"
        f"<p class='notice'>{esc(current.get('summary') or '没有传入历史证据目录；本页只展示测试方案。')}</p>"
        f"<p><b>来源：</b><code>{esc(current.get('source'))}</code></p>"
        + (
            table(
                current_rows,
                [
                    ("concurrency", "目标并发"),
                    ("peak_inflight", "实际峰值在途"),
                    ("stage_observations", "memory_profile 样本"),
                    ("stage_p95_ms", "阶段 P95(ms)"),
                    ("search_sent", "Search 已发"),
                    ("http_errors", "HTTP/传输错误"),
                    ("fact_hits", "事实命中"),
                    ("quality_total", "事实命中分母"),
                    ("search_p95_ms", "Search P95(ms)"),
                    ("status_counts", "HTTP 状态"),
                ],
                min_width=1050,
            )
            if current_rows
            else ""
        )
        + bars(
            [
                {"label": "C=16 memory_profile P95", "value": comparison.get("p95_16_ms"), "display": f"{comparison.get('p95_16_ms')} ms"},
                {"label": "C=64 memory_profile P95", "value": comparison.get("p95_64_ms"), "display": f"{comparison.get('p95_64_ms')} ms"},
            ],
            title="已有阶段样本：16 -> 64（只画真实快照）",
            color="amber",
        )
        + f"<p><b>阶段放大倍数：</b>{esc(comparison.get('ratio'))}；<b>是否可比：</b>{esc(comparison.get('ready'))}。</p>"
        + bullets(current.get("caveats") or [])
        + "</section>"
    )

    parameter_rows = table(
        parameters.get("rows") or [],
        [
            ("layer", "层级"),
            ("parameter", "EchoMem 参数路径"),
            ("current", "当前配置"),
            ("source", "当前值来源"),
            ("small_default", "4U8G small 默认对照"),
            ("c64_start", "C=64 调优起点"),
            ("action", "处理方式"),
            ("explanation", "为什么"),
        ],
        min_width=1900,
    )
    topology_rows = table(
        parameters.get("topologies") or [],
        [
            ("topology", "拓扑"),
            ("total_inflight", "总在途"),
            ("per_tenant", "单租户并发"),
            ("meaning", "含义"),
        ],
        min_width=900,
    )
    reference_rows = table(
        parameters.get("references") or [],
        [("name", "参考"), ("source", "来源"), ("values", "参数摘要"), ("meaning", "使用边界")],
        min_width=1200,
    )
    parameter_section = (
        "<section id='concurrency-parameters'><h2>2. C=64 EchoMem 服务端参数审计</h2>"
        f"<p class='purpose'><b>参数范围：</b>{esc(parameters.get('scope'))}</p>"
        f"<p class='notice'><b>先看结论：</b>{esc(parameters.get('interpretation'))}</p>"
        f"<div class='split'><div><h3>默认基线</h3><p>{esc(parameters.get('baseline_label'))}</p></div>"
        f"<div><h3>调优组</h3><p>{esc(parameters.get('tuning_label'))}</p></div></div>"
        f"<p><b>配置来源：</b><code>{esc(parameters.get('config_source'))}</code></p>"
        f"<div class='visual-panel'>{bars(parameters.get('chart') or [], title='关键闸门：默认 / 当前 / C=64 起点', color='teal')}</div>"
        "<h3>同为 64 在途的三种拓扑</h3>"
        "<p>租户数、单租户并发、QPS 和总在途请求是四个不同维度；测试报告必须分别记录。</p>"
        f"{topology_rows}"
        "<h3>逐项参数、当前值与放开建议</h3>"
        f"{parameter_rows}"
        "<h3>调优顺序</h3>"
        f"{bullets(parameters.get('rules') or [], ordered=True)}"
        "<h3>不要这样改</h3>"
        f"{bullets(parameters.get('do_not_change') or [])}"
        "<h3>无密钥配置片段（仅作实验起点）</h3>"
        f"<pre>{esc(parameters.get('config_snippet'))}</pre>"
        "<h3>历史参数参考</h3>"
        f"{reference_rows}</section>"
    )

    contract_html = "".join(
        f"<details><summary>{esc(item.get('title'))}</summary>"
        f"<p>{esc(item.get('description'))}</p>"
        f"{table(item.get('rows') or [], item.get('columns') or [('item', '项目'), ('value', '要求')], min_width=900)}"
        "</details>"
        for item in plan.get("contracts") or []
    )
    command_html = "".join(
        f"<article class='command'><h3>{esc(item.get('title'))}</h3>"
        f"<p>{esc(item.get('when'))}</p><pre>{esc(item.get('command'))}</pre></article>"
        for item in plan.get("commands") or []
    )
    improvement_rows = table(
        plan.get("improvements") or [],
        [
            ("module", "模块"),
            ("observed_or_risk", "现象/风险"),
            ("change", "修改建议"),
            ("benefit", "预期收益"),
            ("rerun", "验证方式"),
        ],
        min_width=1250,
    )
    artifact_rows = table(
        plan.get("artifacts") or [],
        [("file", "产物"), ("purpose", "用途"), ("required", "是否必需")],
        min_width=900,
    )

    css = """
*{box-sizing:border-box}
body{margin:0;background:#f3f6f7;color:#1b2930;font:14px/1.65 system-ui,-apple-system,sans-serif;letter-spacing:0}
main{max-width:1380px;margin:0 auto;padding:22px}
h1{font-size:30px;line-height:1.2;margin:5px 0 12px}h2{font-size:21px;margin:5px 0 12px}h3{font-size:16px;margin:18px 0 8px}p{margin:8px 0}
.hero{background:#fff;border:1px solid #ccd9dc;border-top:5px solid #176b66;padding:22px}
.eyebrow,.section-kicker,.metric-code{font-size:12px;font-weight:750;letter-spacing:.04em;text-transform:uppercase;color:#557078}
.status-line{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.state{display:inline-block;border:1px solid #b8c8cc;padding:2px 8px;border-radius:12px;font-size:12px;font-weight:700;color:#36535b;background:#eef3f4}
.plan-ready,.measured{color:#0e685c;background:#e4f2ed;border-color:#9ecabf}.plan-ready-with-gaps,.partial,.blocked{color:#8a5d00;background:#fff2d9;border-color:#e1bd71}.unknown{color:#7a4c43;background:#fae8e4;border-color:#d9a49a}
.flow{display:flex;gap:8px;align-items:stretch;flex-wrap:wrap;margin:14px 0}.flow-step{min-width:145px;flex:1;background:#edf4f4;border:1px solid #bfd1d2;padding:9px 11px}.flow-step small{display:block;color:#52666c;margin-top:3px}.flow-arrow{align-self:center;color:#7a8b90;font-size:18px}
.metric-cards{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:14px 0}.metric-card{background:#fff;border:1px solid #d1dde0;border-top:3px solid #176b66;padding:13px;min-height:145px}.metric-card h3{margin:3px 0 6px}.metric-card p{color:#4d6269}
.metric-section,section{background:#fff;border:1px solid #d1dde0;padding:18px;margin-top:12px}.purpose{font-size:15px;color:#314b53}.method{background:#eff5f5;border-left:4px solid #4a8782;padding:10px 13px}.notice{background:#fff6df;border-left:4px solid #c28a21;padding:10px 13px}
.visual-panel{background:#fafcfc;border:1px solid #e0e8e9;padding:10px 14px;margin:12px 0}.plan-bar{display:grid;grid-template-columns:270px minmax(120px,1fr) 125px;gap:10px;align-items:center;margin:8px 0}.plan-bar i{height:13px;background:#e2eaeb;display:block}.plan-bar b{height:100%;display:block;background:#176b66}.plan-bar b.amber{background:#c28a21}.plan-bar b.red{background:#bb5846}.plan-bar strong{text-align:right;font-variant-numeric:tabular-nums}
.scroll{overflow:auto}table{width:100%;border-collapse:collapse}th,td{text-align:left;vertical-align:top;padding:8px;border-bottom:1px solid #e0e7e8}th{background:#edf3f4;white-space:nowrap;color:#314b53}
code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}code{overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f0f3f4;border:1px solid #d9e2e4;padding:12px;margin:8px 0}
.split{display:grid;grid-template-columns:1fr 1fr;gap:18px}.split>div{border-top:2px solid #d5e0e1;padding-top:4px}.command-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.command{border:1px solid #d5dfe1;padding:12px;background:#fbfcfc}.command h3{margin:0}.small,.muted{font-size:12px;color:#61757b}
details{border-top:1px solid #dfe7e8;padding:10px 0}summary{cursor:pointer;color:#176b66;font-weight:700}
@media(max-width:820px){main{padding:10px}.metric-cards,.split,.command-grid{grid-template-columns:1fr}.plan-bar{grid-template-columns:1fr}.plan-bar strong{text-align:left}.flow-arrow{display:none}.flow-step{min-width:100%}}
"""

    title = plan.get("title") or "EchoMem 六项压测方案"
    conclusion = plan.get("overall_conclusion") or "方案已定义；真实结果必须由新的同版本运行产生。"
    generated_at = plan.get("generated_at") or "未记录"
    html_doc = (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{esc(title)}</title><style>{css}</style></head><body><main>"
        f"<header class='hero'><div class='eyebrow'>EchoMem / M1-M6 executable test plan</div>"
        f"<h1>{esc(title)}</h1><div class='status-line'>{badge(plan.get('status'))}"
        f"<span class='small'>生成时间：{esc(generated_at)}</span></div>"
        f"<p class='purpose'><b>总体结论：</b>{esc(conclusion)}</p>"
        "<p class='notice'><b>阅读规则：</b>本页是执行方案，不把计划数量、服务端配置或历史 HTML 数字当成新的性能证据。真实运行必须保留计划、已发、未发、错误、pending、质量和排空分母。</p></header>"
        f"<div class='metric-cards'>{metric_cards}</div>"
        "<section><h2>1. 当前执行边界</h2>"
        f"<p>{esc(plan.get('scope') or '默认命令运行 M1-M3；full 才运行 M1-M6。Soak 关闭。')}</p>"
        f"{table(profile_rows, [('key', '事实'), ('value', '当前值/计划值'), ('source', '来源')], min_width=950)}"
        f"<h3>当前代码/配置状态</h3>{table(state_rows, [('item', '检查项'), ('value', '当前状态'), ('meaning', '解释')], min_width=950)}"
        "</section>"
        + execution_section
        + current_section
        + timing_section
        + parameter_section
        + "<section><h2>3. 一次运行的执行顺序</h2>"
        + flow(plan.get("workflow") or [])
        + table(
            plan.get("schedule") or [],
            [
                ("phase", "阶段"),
                ("scope", "内容"),
                ("duration", "计划时长"),
                ("output", "检查点产物"),
                ("gate", "推进条件"),
            ],
            min_width=1100,
        )
        + "<p class='muted'>时长是编排估计，不是服务保证。Commit 返回 202 后继续轮询；窗口结束仍 pending 要保留并明确写出，不用短 deadline 截断。</p></section>"
        + "".join(metric_sections)
        + "<section><h2>10. 横向证据契约</h2>"
        + "<p>以下规则适用于所有指标；阶段耗时必须来自 EchoMem 自己的结构化日志或 Prometheus 窗口增量，禁止用端到端耗时相减推导内部模块。</p>"
        + contract_html
        + "</section>"
        + f"<section><h2>11. 启动命令</h2><p>先在测试平台仓库根目录执行。命令只显示环境变量名，不把 key 写入 profile、日志或报告。</p><div class='command-grid'>{command_html}</div></section>"
        + f"<section><h2>12. 产物与交付检查</h2>{artifact_rows}<p><b>发布前检查：</b></p>{bullets(plan.get('delivery_checks') or [], ordered=True)}</section>"
        + f"<section><h2>13. EchoMem 模块改进建议</h2><p>下面是与本方案直接对应的工程改进方向；它们是建议，不代表已经修改或已经验证收益。</p>{improvement_rows}</section>"
        + "<footer class='small' style='padding:18px 2px'>本页由测试平台仓库内的 canonical plan renderer 生成；无外部 CDN、无 API key、无伪造测量值。</footer>"
        + "</main></body></html>"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_doc, encoding="utf-8")
