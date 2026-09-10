"""objective-suite.html 渲染：逐 profile 目标表 O1-O7 + 探针证据明细。

自包含 HTML：状态按 PASS/FAIL/TIMEOUT/INCONCLUSIVE 着色，探针证据以
``<details>`` 折叠展示检查项表与制品路径。只依据实际运行证据判定，缺少
部署控制或服务端指标时保持 INCONCLUSIVE。
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

_PROBE_LABELS = (
    ("capability_probe", "能力探针"),
    ("blackbox_contract_probe", "黑盒契约探针"),
    ("missing_cases", "PR397 黑盒一致性探针"),
    ("concurrent_commit", "并发 Commit 探针"),
    ("concurrency_topology", "用户与 Session 并发拓扑矩阵"),
    ("payload_boundary", "API/MCP 请求体与超长 Commit 边界"),
    ("fault_isolation", "单租户故障隔离探针"),
    ("limit_failure_sweep", "真实限流阶梯"),
    ("commit_recovery", "Commit 崩溃恢复探针"),
    ("fault_suite", "故障套件"),
)


def _check_detail(payload: dict[str, Any]) -> dict[str, Any]:
    checks = payload.get("checks")
    if not isinstance(checks, list) or not checks:
        return {}
    detail = checks[-1].get("detail") if isinstance(checks[-1], dict) else None
    if isinstance(detail, dict):
        return detail
    if isinstance(detail, str):
        try:
            parsed = json.loads(detail)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _probe_visual(key: str, payload: dict[str, Any]) -> str:
    detail = _check_detail(payload)
    if key == "concurrency_topology":
        matrix = detail.get("matrix") if isinstance(detail.get("matrix"), list) else []
        rows = []
        maximum = max(1.0, max((float(row.get("p95_ms") or 0) for row in matrix), default=1.0))
        for row in matrix:
            width = min(100.0, 100.0 * float(row.get("p95_ms") or 0) / maximum)
            rows.append(
                "<tr>"
                f"<td>{html.escape(str(row.get('requested_concurrency') or row.get('level')))}</td>"
                f"<td>{html.escape(str(row.get('topology')))}</td>"
                f"<td>{html.escape(str(row.get('actual_users')))} / {html.escape(str(row.get('requested_users')))}</td>"
                f"<td>{html.escape(str(row.get('peak_active_operations', '-')))}</td>"
                f"<td>{html.escape(str(row.get('completed_2xx')))} / {html.escape(str(row.get('offered')))}</td>"
                f"<td><div class='bar' style='width:{width:.1f}%'></div>{html.escape(str(row.get('p95_ms')))} ms</td>"
                f"<td>{html.escape(str(row.get('throughput_rps_2xx')))}</td>"
                f"<td>{html.escape(str(row.get('tenant_throughput_jain')))}</td>"
                f"<td><code>{html.escape(json.dumps(row.get('http_counts') or {}, ensure_ascii=False))}</code></td>"
                f"<td><code>{html.escape(json.dumps(row.get('drain') or {'reason': row.get('reason')}, ensure_ascii=False))}</code></td>"
                "</tr>"
            )
        if rows:
            return ("<table><thead><tr><th>并发档</th><th>拓扑</th><th>实际/请求用户</th>"
                    "<th>峰值在途操作</th><th>2xx/总请求</th><th>操作 P95（含超时）</th><th>2xx受理吞吐</th>"
                    "<th>受理 Jain（非完成公平性）</th><th>HTTP/传输分布</th><th>Commit 排空</th></tr></thead><tbody>"
                    + "".join(rows) + "</tbody></table>")
    if key == "payload_boundary":
        cases = detail.get("cases") if isinstance(detail.get("cases"), list) else []
        rows = []
        for row in cases:
            rows.append(
                "<tr>"
                f"<td>{html.escape(str(row.get('api')))}</td>"
                f"<td>{html.escape(str(row.get('encoding')))}</td>"
                f"<td>{html.escape(str(row.get('content_bytes')))}</td>"
                f"<td>{html.escape(str(row.get('wire_bytes')))}</td>"
                f"<td>{html.escape(str(row.get('http_status') or row.get('transport_error_type') or '-'))}</td>"
                f"<td>{html.escape(str(row.get('reason_code') or '-'))}</td>"
                f"<td>{html.escape(str(row.get('elapsed_ms')))} ms</td>"
                "</tr>"
            )
        if rows:
            return ("<table><thead><tr><th>API</th><th>编码</th><th>内容字节</th>"
                    "<th>Wire 字节</th><th>结果</th><th>原因类型</th><th>耗时</th>"
                    "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
                    f"<pre>{html.escape(json.dumps({'long_commit': detail.get('long_commit'), 'mcp_add_memory': detail.get('mcp_add_memory')}, ensure_ascii=False, indent=2))}</pre>")
    return ""


def _model_evidence(profile: dict[str, Any]) -> dict[str, Any]:
    preflight = profile.get("model_preflight") or profile.get("preflight") or {}
    engines = preflight.get("engines") if isinstance(preflight.get("engines"), list) else []
    verified_kinds = {
        str(engine.get("kind") or "") for engine in engines
        if engine.get("status") == "ok" and engine.get("model_supported") is True
    }
    verified = bool(preflight.get("ok")) and {"llm", "embedding"}.issubset(verified_kinds)
    if verified:
        status = "VERIFIED"
        reason = "LLM 与 Embedding 均完成真实 Provider 请求预检"
    elif preflight:
        status = "FAILED"
        reason = str(preflight.get("error") or "真实模型预检未通过")
    else:
        status = "MISSING"
        reason = "没有模型预检记录，不能判断是否调用真实模型"
    return {"status": status, "reason": reason, "preflight": preflight, "engines": engines}


def render_objective_suite_html(result: dict[str, Any]) -> str:
    """把 objective-suite.json 渲染为自包含 HTML 字符串。"""
    rows = []
    for profile in result.get("profiles") or []:
        for objective in profile.get("objectives") or []:
            rows.append(
                "<tr>"
                f"<td>{html.escape(str(profile.get('name')))}</td>"
                f"<td>{html.escape(str(objective.get('id')))} "
                f"{html.escape(str(objective.get('name')))}</td>"
                f"<td class='{html.escape(str(objective.get('status')).lower())}'>"
                f"{html.escape(str(objective.get('status')))}</td>"
                f"<td>{html.escape(str(objective.get('reason')))}"
                f"<br><code>{html.escape(json.dumps(objective.get('observed', {}), ensure_ascii=False, sort_keys=True))}</code></td>"
                f"<td>{html.escape(str(objective.get('owner') or '测试平台'))}</td>"
                f"<td><code>{html.escape(str(objective.get('evidence')))}</code></td>"
                "</tr>"
            )
    details = []
    model_sections = []
    model_evidence = [_model_evidence(profile) for profile in result.get("profiles") or []]
    for profile in result.get("profiles") or []:
        evidence = _model_evidence(profile)
        engine_rows = "".join(
            "<tr>"
            f"<td>{html.escape(str(engine.get('kind') or ''))}</td>"
            f"<td>{html.escape(str(engine.get('id') or ''))}</td>"
            f"<td>{html.escape(str(engine.get('model') or ''))}</td>"
            f"<td>{html.escape(str(engine.get('api_base') or ''))}</td>"
            f"<td>{html.escape(str(engine.get('status') or ''))}</td>"
            f"<td>{html.escape(str(engine.get('code') or ''))}</td>"
            "</tr>"
            for engine in evidence["engines"]
        ) or "<tr><td colspan='6'>没有真实模型调用明细</td></tr>"
        css_class = "pass" if evidence["status"] == "VERIFIED" else "fail"
        model_sections.append(
            f"<h3>{html.escape(str(profile.get('name')))}："
            f"<span class='{css_class}'>{html.escape(evidence['status'])}</span></h3>"
            f"<p>{html.escape(evidence['reason'])}</p>"
            f"<p class='muted'>配置指纹：<code>{html.escape(str(evidence['preflight'].get('digest') or '-'))}</code>；"
            f"预检尝试：{html.escape(str(evidence['preflight'].get('probe_attempts', '-')))}。"
            "API Key 不写入报告。</p>"
            "<table><thead><tr><th>类型</th><th>配置路径/用途</th><th>模型</th>"
            "<th>Endpoint</th><th>真实请求状态</th><th>HTTP</th></tr></thead>"
            f"<tbody>{engine_rows}</tbody></table>"
        )
        details.append(f"<h3>{html.escape(str(profile.get('name')))}</h3>")
        for key, label in _PROBE_LABELS:
            payload = profile.get(key)
            if not isinstance(payload, dict):
                continue
            checks_detail = payload.get("checks") or payload.get("cases") or []
            details.append(
                f"<details><summary>{label}："
                f"<strong>{html.escape(str(payload.get('status', '未返回')))}</strong>"
                "</summary>"
            )
            if payload.get("reason"):
                details.append(f"<p>{html.escape(str(payload['reason']))}</p>")
            if isinstance(checks_detail, list) and checks_detail:
                details.append(
                    "<table><thead><tr><th>检查项</th><th>状态</th><th>HTTP/耗时</th>"
                    "<th>说明</th></tr></thead><tbody>"
                )
                for item in checks_detail:
                    item = item if isinstance(item, dict) else {}
                    execution = item.get("execution") if isinstance(item.get("execution"), dict) else {}
                    nested = execution.get("result") if isinstance(execution.get("result"), dict) else {}
                    details.append(
                        "<tr>"
                        f"<td>{html.escape(str(item.get('name') or item.get('kind') or 'case'))}</td>"
                        f"<td>{html.escape(str(item.get('status') or nested.get('status') or ''))}</td>"
                        f"<td>{html.escape(str(item.get('http_status') or item.get('elapsed_s') or ''))}</td>"
                        f"<td>{html.escape(str(item.get('reason') or nested.get('reason') or ''))}</td>"
                        "</tr>"
                    )
                details.append("</tbody></table>")
            visual = _probe_visual(key, payload)
            if visual:
                details.append(visual)
            details.append(
                f"<p class='muted'>制品：<code>{html.escape(str(payload.get('path', '')))}</code></p></details>"
            )
    all_models_verified = bool(model_evidence) and all(
        evidence["status"] == "VERIFIED" for evidence in model_evidence
    )
    model_banner_class = "pass" if all_models_verified else "fail"
    model_banner = (
        "真实模型可用性预检已通过，但本报告没有压测期间模型调用证据，不能据此宣称负载使用了模型。"
        if all_models_verified else
        "未证明真实模型可用或被调用，本报告不能宣称使用了真实模型。"
    )
    return f"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>EchoMem 七项目标自动化验收</title>
<style>
body{{font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#17212b;background:#f5f7f8;margin:0}}
main{{max-width:1280px;margin:auto;padding:28px 18px 56px}}section{{background:#fff;border:1px solid #dfe6ea;padding:18px;margin-top:14px}}
h1{{margin:0 0 6px;font-size:25px}}.muted{{color:#687784}}table{{border-collapse:collapse;width:100%}}
th,td{{border-bottom:1px solid #e7ecef;padding:9px;text-align:left;vertical-align:top}}th{{background:#f7f9fa}}
.pass{{color:#197c62;font-weight:700}}.fail,.timeout{{color:#b6403b;font-weight:700}}.inconclusive{{color:#9a6a00;font-weight:700}}
code{{background:#f0f3f5;padding:2px 4px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f7f9fa;padding:10px}}
.bar{{height:6px;background:#247a68;margin:2px 0 4px;min-width:2px}}.scroll{{overflow:auto}}
</style><main>
<section><h1>EchoMem 七项目标自动化验收</h1>
<div class="muted">生成时间：{html.escape(str(result.get("created_at", "")))} · 真实 HTTP：是</div>
<p class="{model_banner_class}">{html.escape(model_banner)}</p>
<p>报告只依据实际运行证据判定；缺少部署控制或服务端指标时标记为 INCONCLUSIVE，不推断为通过。</p></section>
<section class="scroll"><h2>模型可用性预检（不是负载调用证明）</h2>
<p class="muted">“没有启用 mock”不等于调用了真实模型。下表只证明独立的 Provider 预检；负载期间调用需另有阶段日志或 Provider 指标。</p>
{"".join(model_sections)}</section>
<section class="scroll"><h2>逐 profile 目标状态</h2>
<table><thead><tr><th>Profile</th><th>目标</th><th>状态</th><th>说明</th><th>归属</th><th>证据</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></section>
<section class="scroll"><h2>内存泄漏诊断</h2>{"".join(_leak_sections(result))}</section>
<section class="scroll"><h2>探针与黑盒证据明细</h2>
<p class="muted">这里显示真实 HTTP 探针实际检查到的内容。没有真实输入、控制能力或服务端观测时，状态保持 INCONCLUSIVE。</p>
{"".join(details)}</section>
</main></html>"""



def _leak_sections(result: dict[str, Any]) -> list[str]:
    """渲染内存泄漏诊断（suite 收尾时由通用模块挂到 profile）。"""
    sections = []
    for profile in result.get("profiles") or []:
        leak = profile.get("memory_leak")
        if not isinstance(leak, dict):
            continue
        rows = []
        for item in leak.get("per_case") or []:
            meas = item.get("measurements") or {}
            slope = meas.get("slope_mb_per_min")
            rows.append(
                "<tr>"
                f"<td>{html.escape(str(item.get('case', '')))}</td>"
                f"<td>{html.escape(str(item.get('verdict', '')))}</td>"
                f"<td>{html.escape(str(slope if slope is not None else '-'))}</td>"
                f"<td>{html.escape(str(meas.get('projected_growth_mb_per_hour') or '-'))}</td>"
                f"<td>{html.escape(str(meas.get('window_s') or '-'))}</td>"
                f"<td>{html.escape(str(item.get('reason', '')))}</td>"
                "</tr>"
            )
        verdict = str(leak.get("verdict") or "INCONCLUSIVE")
        cls = {"PASS": "pass", "FAIL": "fail"}.get(verdict, "inconclusive")
        body = "".join(rows) or "<tr><td colspan='6' class='muted'>无 RSS 采样数据</td></tr>"
        sections.append(
            f"<h3>{html.escape(str(profile.get('name')))}："
            f"<span class='{cls}'>{html.escape(verdict)}</span>"
            f"<span class='muted'> {html.escape(str(leak.get('reason', '')))}</span></h3>"
            "<table><thead><tr><th>case</th><th>判定</th><th>斜率MB/min</th>"
            "<th>预计MB/h</th><th>窗口s</th><th>说明</th></tr></thead>"
            f"<tbody>{body}</tbody></table>"
        )
    if sections:
        sections.insert(0, "<p class='muted'>压测收尾自动诊断（RSS 斜率阈值 5 MB/min，"
                             "观测窗口 <600s 不判泄漏）。</p>")
    return sections


def write_objective_suite_html(result: dict[str, Any], path: Path) -> None:
    """把渲染结果写入 ``path``。"""
    path.write_text(render_objective_suite_html(result), encoding="utf-8")
