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
<section><h2>原始证据</h2><ul>{artifacts}</ul>
<p class="muted">报告只引用运行目录内存在的文件，不写入 API key、密码或环境变量值。</p></section>
</main></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
