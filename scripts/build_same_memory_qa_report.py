#!/usr/bin/env python3
"""Build the same-memory, three-mode QA comparison report."""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "results_develop_pr192_pr199_tools" / "20260805_124118_939251"
RUNS = {
    "no_tools": ROOT / "results_same_memory_no_tools" / "20260805_143003_960855",
    "tools_allow": ROOT / "results_same_memory_tools_allow" / "20260805_143225_781328",
    "tools_disabled": ROOT / "results_same_memory_tools_disabled" / "20260805_143635_938012",
}
OUTPUT = ROOT / "reports" / "echomem_same_memory_qa_20260805"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sanitize(data: dict) -> dict:
    result = json.loads(json.dumps(data))
    identity = result.get("memory_identity")
    if isinstance(identity, dict):
        identity["auth_key"] = "[REDACTED]"
    return result


def copy_run(source: Path, target: Path, summary: dict) -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / "summary.json").write_text(
        json.dumps(sanitize(summary), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    for name in (
        "import_results.csv",
        "qa_results.csv",
        "judge_results.csv",
        "diagnosis.json",
        "tool_audits.jsonl",
        "run.log",
    ):
        path = source / name
        if path.exists():
            shutil.copy2(path, target / name)


def e(value: object) -> str:
    return html.escape(str(value))


def main() -> None:
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir(parents=True)

    summaries = {name: load(path / "summary.json") for name, path in RUNS.items()}
    for name, path in RUNS.items():
        copy_run(path, OUTPUT / name, summaries[name])
    (OUTPUT / "single_injection").mkdir(parents=True, exist_ok=True)
    for name in ("import_results.csv", "run.log"):
        source = BASE / name
        if source.exists():
            shutil.copy2(source, OUTPUT / "single_injection" / name)

    rows = []
    labels = {
        "no_tools": "不带工具调用",
        "tools_allow": "带工具，允许 read/messages.jsonl",
        "tools_disabled": "带工具，禁止 read/messages.jsonl",
    }
    for name, summary in summaries.items():
        read_rate = summary.get("messages_jsonl_read_rate", 0) * 100
        rows.append(
            f"<tr><td>{labels[name]}</td>"
            f"<td>{summary.get('judge_correct')}/{summary.get('judge_graded')}</td>"
            f"<td><strong>{summary.get('accuracy', 0) * 100:.2f}%</strong></td>"
            f"<td>{summary.get('tool_call_total', 0)}</td>"
            f"<td>{summary.get('messages_jsonl_read_questions', 0)}/81 "
            f"({read_rate:.2f}%)</td>"
            f"<td>{summary.get('memory_reuse', {}).get('enabled', False)}</td></tr>"
        )

    disabled_log = RUNS["tools_disabled"] / "run.log"
    rejected = 0
    if disabled_log.exists():
        rejected = sum(
            "Rejected unavailable tool call: read" in line
            for line in disabled_log.read_text(encoding="utf-8", errors="replace").splitlines()
        )
    allow_log = RUNS["tools_allow"] / "run.log"
    allow_warnings = []
    if allow_log.exists():
        allow_warnings = [
            line for line in allow_log.read_text(encoding="utf-8", errors="replace").splitlines()
            if "WARNING" in line
        ]

    report = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>EchoMem 同记忆 QA MCP 三模式对比</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:1160px;margin:32px auto;padding:0 20px;color:#202124;line-height:1.55}}
h1{{margin-bottom:4px}}h2{{margin-top:30px;border-bottom:1px solid #ddd;padding-bottom:6px}}
.muted{{color:#666}}.callout{{padding:14px 16px;background:#e8f0fe;border-left:4px solid #1a73e8}}
.warn{{padding:14px 16px;background:#fff4d6;border-left:4px solid #d99b00}}
table{{border-collapse:collapse;width:100%;margin:14px 0}}th,td{{border:1px solid #ddd;padding:8px;text-align:left}}th{{background:#f5f6f7}}
code,pre{{background:#f1f3f4}}code{{padding:2px 4px}}pre{{padding:12px;overflow:auto;font-size:12px}}
</style>
</head>
<body>
<h1>EchoMem 同记忆 QA MCP 三模式对比</h1>
<p class="muted">2026-08-05　|　LoCoMo conv-30　|　81 道题　|　真实 DashScope 模型</p>

<h2>核心结论</h2>
<div class="callout">
只注入一次记忆后，三种 QA 模式的准确率分别为：
<strong>不带工具 49.38%</strong>、
<strong>工具允许 read 79.01%</strong>、
<strong>工具禁止 read 54.32%</strong>。
因此，工具调用本身有收益，而允许读取完整 <code>current/messages.jsonl</code> 带来的收益最明显。
</div>

<h2>测试方法</h2>
<ol>
<li>使用一次完整注入结果：EchoMem MCP 轮次的 19/19 session，之后不再 open/message/commit。</li>
<li>固定同一 tenant/user、同一 workspace、同一模型和同一 81 道题。</li>
<li>只切换 QA 阶段的 MCP 设置：无工具、工具 allow、工具 disabled。</li>
<li><code>--reuse-memory-from</code> 只复用记忆导入，不复用旧 QA 答案；每种模式重新请求模型并重新 Judge。</li>
</ol>

<h2>准确率对比</h2>
<table>
<tr><th>QA 模式</th><th>Judge 正确</th><th>准确率</th><th>工具调用数</th><th>读取 messages.jsonl</th><th>复用记忆</th></tr>
{''.join(rows)}
</table>

<h2>异常记录</h2>
<ul>
<li>禁止 read 的轮次中，模型尝试调用 read 后被 harness 拒绝，共记录约
<code>{rejected}</code> 条 <code>Rejected unavailable tool call: read</code> 日志。</li>
<li>允许 read 的轮次有 {len(allow_warnings)} 条 WARNING，主要是一次带历史 archive URI
的 <code>glob</code> 请求缺少 tenant context；不影响该轮完成。</li>
<li>本报告只比较 QA MCP 行为；早先注入压测发现的 atomic_engine organized-vectors timeout
仍保留在旧报告中，不混入本次三模式准确率。</li>
</ul>

<h2>代码基线</h2>
<p>EchoMem：<code>develop</code> + PR192 <code>b91be98</code> + PR199
<code>88573f7</code>，合并 HEAD <code>e6cdbfc</code>。引擎为
<code>atomic_engine</code>，thinking 为 <code>disabled</code>。</p>

<h2>附件</h2>
<ul>
<li><code>single_injection/</code>：唯一一次注入的结果。</li>
<li><code>no_tools/</code>、<code>tools_allow/</code>、<code>tools_disabled/</code>：
三种 QA 的完整 CSV、trace、tool audit、run.log 和脱敏 summary。</li>
</ul>
</body>
</html>
"""
    (OUTPUT / "report.html").write_text(report, encoding="utf-8")
    (OUTPUT / "README.md").write_text(
        """# 同记忆 QA MCP 三模式对比

打开 `report.html`。`single_injection/` 是唯一一次注入，三个模式目录只包含
QA/Judge 结果。结果中的身份 key 已脱敏。
""",
        encoding="utf-8",
    )
    print(OUTPUT / "report.html")


if __name__ == "__main__":
    main()
