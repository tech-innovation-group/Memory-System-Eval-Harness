#!/usr/bin/env python3
"""Build an auditable HTML report for a PR29 six-metric run.

The report deliberately distinguishes:

* a scenario that was configured;
* a scenario that actually produced samples; and
* a metric that has enough evidence for PASS.

It never upgrades missing evidence to PASS and keeps links to the raw JSON/CSV
files next to the generated report.
"""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
from typing import Any


OBJECTIVES = (
    ("O1", "最大 DAU / 热用户量"),
    ("O2", "单租户故障隔离"),
    ("O3", "多租户公平性"),
    ("O4", "Search 优先级"),
    ("O5", "202 Commit 崩溃恢复"),
    ("O6", "四元组可观测性"),
)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def esc(value: Any) -> str:
    return html.escape("-" if value in (None, "") else str(value))


def link(path: Path, report: Path, label: str) -> str:
    if not path.is_file():
        return ""
    return (
        f"<li><a href='{html.escape(os.path.relpath(path, report.parent))}'>"
        f"{esc(label)}</a></li>"
    )


def status(value: Any) -> str:
    text = str(value or "INCONCLUSIVE").upper()
    css = {
        "PASS": "pass",
        "FAIL": "fail",
        "INCONCLUSIVE": "warn",
        "BLOCKED": "warn",
    }.get(text, "neutral")
    return f"<span class='badge {css}'>{esc(text)}</span>"


def json_text(value: Any) -> str:
    if value in (None, "", {}, []):
        return "-"
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, help="objective-suite result directory")
    parser.add_argument("-o", "--output", type=Path, default=None)
    args = parser.parse_args()

    root = args.root.resolve()
    output = (args.output or root / "pr29-six-metric-report.html").resolve()
    suite = load(root / "objective-suite.json")
    profile = (suite.get("profiles") or [{}])[0]
    objectives = {
        str(item.get("id")): item
        for item in profile.get("objectives") or []
        if isinstance(item, dict)
    }
    formal = root / "4U8G" / "formal"
    formal_manifest = load(formal / "suite.json")
    auth = formal_manifest.get("auth_preflight") or {}
    scenarios = []
    for name in formal_manifest.get("scenarios") or []:
        path = formal / str(name) / "repeat-01" / "server-observe"
        summary = load(path / "summary.json")
        metrics = summary.get("metrics") or {}
        search = metrics.get("search") or {}
        commit = metrics.get("commit") or {}
        scenarios.append(
            (
                name,
                summary.get("status") or "not-run",
                search.get("submitted", 0),
                search.get("succeeded", 0),
                commit.get("submitted", 0),
                commit.get("completed", 0),
                search.get("latency", {}).get("p95_s"),
            )
        )

    cards = "".join(
        f"<article class='card'><div class='id'>{esc(ident)}</div>"
        f"<h3>{esc(title)}</h3>{status(objectives.get(ident, {}).get('status'))}"
        f"<p>{esc(objectives.get(ident, {}).get('reason'))}</p></article>"
        for ident, title in OBJECTIVES
    )
    scenario_rows = "".join(
        "<tr>"
        f"<td>{esc(name)}</td><td>{status(state)}</td>"
        f"<td>{esc(search_submitted)}/{esc(search_succeeded)}</td>"
        f"<td>{esc(commit_submitted)}/{esc(commit_completed)}</td>"
        f"<td>{esc(p95)} s</td></tr>"
        for name, state, search_submitted, search_succeeded,
        commit_submitted, commit_completed, p95 in scenarios
    )
    artifact_paths = (
        (root / "objective-suite.json", "objective-suite.json"),
        (formal / "suite.json", "4U8G/formal/suite.json"),
        (root / "4U8G" / "capability-probe.json", "capability-probe.json"),
        (root / "4U8G" / "commit-recovery.json", "commit-recovery.json"),
        (formal / "fairness-bounded/repeat-01/server-observe/search_results.csv",
         "fairness Search CSV"),
        (formal / "fairness-bounded/repeat-01/server-observe/commit_results.csv",
         "fairness Commit CSV"),
        (formal / "search-priority-blackbox/repeat-01/server-observe/search_results.csv",
         "priority Search CSV"),
        (formal / "search-priority-blackbox/repeat-01/server-observe/commit_results.csv",
         "priority Commit CSV"),
    )
    artifacts = "".join(link(path, output, label) for path, label in artifact_paths)
    configured = formal_manifest.get("scenarios") or []
    coverage = f"{len(scenarios)}/{len(configured)}"
    model = load(root / "echomem-config-real-4u8g.json")
    llm = ((model.get("model") or {}).get("llm") or {}).get("model", "-")
    embedding = ((model.get("model") or {}).get("embedding") or {}).get("model", "-")
    observed_rows = "".join(
        f"<tr><td>{esc(ident)}</td><td>{status(item.get('status'))}</td>"
        f"<td>{esc(item.get('reason'))}</td>"
        f"<td><code>{esc(json_text(item.get('observed')))}</code></td></tr>"
        for ident, _title in OBJECTIVES
        for item in [objectives.get(ident, {})]
    )
    module_rows = [
        (
            "认证 / 多租户",
            f"有效凭据 {auth.get('passed', 0)}/{auth.get('tenant_count', 0)}；"
            "其余租户返回 401。",
            "测试平台输入与部署凭据",
            "补齐独立租户凭据后才能继续 8/16/32 档位和故障旁观租户测试。",
        ),
        (
            "容量阶梯",
            "capacity-2、capacity-4 有 Search 样本；更高档位未形成有效失败边界。",
            "测试平台场景 / 4U8G 资源",
            "继续执行 8、16、32，记录达到 SLO 的最后一档和下一档真实失败。",
        ),
        (
            "检索 / Search",
            "本轮有真实 Search 延迟，但 seed recall 预验证未形成热记忆证据。",
            "测试数据准备与 EchoMem 检索链路",
            "先保证 seed Commit 完成并用 marker Search 验证命中，再判定热缓存准确率和优先级。",
        ),
        (
            "路由 / 调度",
            "公平窗口有 4 租户 Commit/Search 竞争；Priority 窗口有真实并发，但热记忆前提不足。",
            "测试平台负载 + EchoMem 调度",
            "保留到达/完成时间、在途 Commit 和 Search P95，避免只凭客户端返回判定优先级。",
        ),
        (
            "Commit / 持久化",
            "202、kill-9、重启、completed、history/archive/cursor 顺序对账均有证据。",
            "EchoMem 现有接口 + 测试平台恢复探针",
            "增加重复样本；同幂等键虽返回同 archive，但 replayed 标记为 false，需单独确认契约。",
        ),
        (
            "Metrics / 可观测性",
            "lane 指标族存在，但实际 lane 四元组不完整，fan-out 指标无实样本。",
            "EchoMem /metrics 与测试平台触发负载",
            "用真实拒绝、等待和引擎 fan-out 负载触发每个指标，再采集完整四元组。",
        ),
        (
            "故障控制面",
            "未配置真实单租户依赖故障控制 URL/命令，O2 无前后 P95 配对数据。",
            "部署侧控制面 + 测试平台采集",
            "提供只作用于目标租户的 500/429/timeout/connection-refused 控制，并保留时间线。",
        ),
    ]
    module_html = "".join(
        f"<tr><td><b>{esc(module)}</b></td><td>{esc(evidence)}</td>"
        f"<td>{esc(owner)}</td><td>{esc(action)}</td></tr>"
        for module, evidence, owner, action in module_rows
    )

    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PR29 4U8G 六项指标报告</title>
<style>
:root{{--ink:#172a35;--muted:#667983;--line:#d8e2e7;--bg:#f4f7f8;--teal:#176b87;--green:#147a61;--red:#b6423d;--amber:#9a6b00}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1320px;margin:auto;padding:28px 20px 60px}}h1{{margin:0;font-size:28px}}h2{{margin:0 0 14px;font-size:20px}}h3{{margin:5px 0;font-size:15px}}
.sub,.muted{{color:var(--muted)}}section{{background:#fff;border:1px solid var(--line);padding:18px;margin:14px 0}}
.cards{{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}}.card{{background:#fbfcfc;border-top:4px solid var(--amber);padding:12px;min-height:142px}}
.card p{{color:var(--muted);font-size:12px}}.id{{font-weight:800;color:var(--teal);font-size:12px}}
.badge{{display:inline-block;padding:2px 7px;border-radius:3px;font-weight:700;font-size:12px}}
.pass{{color:var(--green);background:#e5f4ee}}.fail{{color:var(--red);background:#fae9e7}}.warn{{color:var(--amber);background:#fff3d2}}.neutral{{color:var(--muted);background:#edf1f2}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{background:#f2f6f7;font-size:12px}}
.callout{{border-left:4px solid var(--teal);background:#eef7f9;padding:12px 14px}}ul{{padding-left:20px}}a{{color:var(--teal)}}
@media(max-width:950px){{.cards{{grid-template-columns:repeat(2,1fr)}}}}@media(max-width:520px){{.cards{{grid-template-columns:1fr}}main{{padding:18px 10px}}}}
</style></head><body><main>
<h1>PR29 4U8G 六项指标真实黑盒测试</h1>
<p class="sub">生成时间：{esc(suite.get("created_at"))} · 场景覆盖：{coverage} · 默认不含 soak</p>
<div class="callout"><b>阅读规则：</b>配置了场景不等于已经完成测试；有 HTTP 返回不等于有记忆召回证据。
缺少故障控制、热记忆或完整指标样本时，保留为 INCONCLUSIVE。</div>
<section><h2>六项指标结论</h2><div class="cards">{cards}</div></section>
<section><h2>运行环境</h2><table>
<tr><th>项目</th><th>值</th></tr>
<tr><td>测试平台</td><td>Memory-System-Eval-Harness PR29</td></tr>
<tr><td>实例</td><td>4 vCPU / 8 GiB，4U8G</td></tr>
<tr><td>LLM</td><td>{esc(llm)}（真实模型）</td></tr>
<tr><td>Embedding</td><td>{esc(embedding)}（真实模型）</td></tr>
<tr><td>独立凭据</td><td>{esc(auth.get("passed", 0))}/{esc(auth.get("tenant_count", 0))}</td></tr>
</table></section>
<section><h2>场景明细</h2><p class="muted">Search 和 Commit 均按“提交数/成功或完成数”展示，P95 只来自真实请求。</p>
<table><thead><tr><th>场景</th><th>运行状态</th><th>Search</th><th>Commit</th><th>Search P95</th></tr></thead>
<tbody>{scenario_rows or "<tr><td colspan='5'>没有场景结果</td></tr>"}</tbody></table></section>
<section><h2>六项指标原始观测</h2><p class="muted">这里保留验收器实际使用的 observed 字段，便于复核容量档位、Jain、恢复和指标覆盖。</p>
<table><thead><tr><th>指标</th><th>状态</th><th>判定说明</th><th>观测数据</th></tr></thead>
<tbody>{observed_rows}</tbody></table></section>
<section><h2>按 config.json 模块归因</h2>
<table><thead><tr><th>模块</th><th>当前真实证据</th><th>归属</th><th>下一步</th></tr></thead>
<tbody>{module_html}</tbody></table>
<div class="callout"><b>归因原则：</b>没有真实故障控制、热记忆命中或指标样本时，只能说明测试前提/证据缺失；
不能直接写成 EchoMem 内部未实现。只有 HTTP 404 或真实服务行为明确违反契约时，才建议修改 EchoMem。</div></section>
<section><h2>原始证据</h2><ul>{artifacts}</ul>
<p class="muted">报告只引用运行目录内存在的文件，不写入 API key、密码或环境变量值。</p></section>
</main></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
