#!/usr/bin/env python3
"""Build a shareable EchoMem develop + PR192 + PR199 evaluation report."""

from __future__ import annotations

import csv
import html
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NO_TOOLS = ROOT / "results_develop_pr192_pr199_no_tools" / "20260805_121254_591223"
TOOLS = ROOT / "results_develop_pr192_pr199_tools" / "20260805_124118_939251"
SERVER_LOG = Path("/tmp/echomem_develop_pr192_pr199_20260805/log/echomem.jsonl")
OUTPUT = ROOT / "reports" / "echomem_develop_pr192_pr199_20260805"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sanitize_summary(data: dict) -> dict:
    result = json.loads(json.dumps(data))
    identity = result.get("memory_identity")
    if isinstance(identity, dict):
        identity["auth_key"] = "[REDACTED]"
    return result


def import_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def copy_artifacts(source: Path, target: Path, summary: dict) -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / "summary.json").write_text(
        json.dumps(sanitize_summary(summary), ensure_ascii=False, indent=2),
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
        source_file = source / name
        if source_file.exists():
            shutil.copy2(source_file, target / name)


def esc(value: object) -> str:
    return html.escape(str(value))


def main() -> None:
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    (OUTPUT / "no_tools").mkdir(parents=True)
    (OUTPUT / "tools").mkdir(parents=True)

    no_tools = read_json(NO_TOOLS / "summary.json")
    tools = read_json(TOOLS / "summary.json")
    no_imports = import_rows(NO_TOOLS / "import_results.csv")
    tool_imports = import_rows(TOOLS / "import_results.csv")

    copy_artifacts(NO_TOOLS, OUTPUT / "no_tools", no_tools)
    copy_artifacts(TOOLS, OUTPUT / "tools", tools)
    if SERVER_LOG.exists():
        shutil.copy2(SERVER_LOG, OUTPUT / "echomem.jsonl")

    error_lines = []
    if SERVER_LOG.exists():
        for line in SERVER_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
            lowered = line.lower()
            if '"level":"error"' in lowered or "timed out" in lowered:
                error_lines.append(line)
    (OUTPUT / "server_errors.log").write_text(
        "\n".join(error_lines) + ("\n" if error_lines else ""), encoding="utf-8"
    )

    no_ok = sum(row.get("status") == "completed" for row in no_imports)
    tool_ok = sum(row.get("status") == "completed" for row in tool_imports)
    no_total = sum(float(row.get("elapsed_s") or 0) for row in no_imports)
    tool_total = sum(float(row.get("elapsed_s") or 0) for row in tool_imports)
    tool_audits = []
    audit_path = TOOLS / "tool_audits.jsonl"
    if audit_path.exists():
        tool_audits = [
            json.loads(line)
            for line in audit_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    tool_calls = sum(len(row.get("tool_calls") or []) for row in tool_audits)
    read_questions = sum(bool(row.get("messages_jsonl_reads")) for row in tool_audits)

    rows = f"""
      <tr><td>不带工具调用</td><td>{no_ok}/19</td><td>{no_total:.1f}s</td>
        <td>{no_tools.get("judge_correct")}/{no_tools.get("judge_graded")}</td>
        <td>{no_tools.get("accuracy", 0) * 100:.2f}%</td>
        <td>1 个 session 失败</td></tr>
      <tr><td>带 EchoMem MCP 工具</td><td>{tool_ok}/19</td><td>{tool_total:.1f}s</td>
        <td>{tools.get("judge_correct")}/{tools.get("judge_graded")}</td>
        <td>{tools.get("accuracy", 0) * 100:.2f}%</td>
        <td>全部成功</td></tr>
    """

    report = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EchoMem develop + PR192 + PR199 测试报告</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:1180px;margin:32px auto;padding:0 20px;color:#202124;line-height:1.55}}
h1{{margin-bottom:4px}}h2{{margin-top:30px;border-bottom:1px solid #ddd;padding-bottom:6px}}
.muted{{color:#666}}.callout{{padding:14px 16px;background:#fff4d6;border-left:4px solid #d99b00}}
.bad{{color:#b3261e;font-weight:600}}.good{{color:#137333;font-weight:600}}
table{{border-collapse:collapse;width:100%;margin:14px 0}}th,td{{border:1px solid #ddd;padding:8px;text-align:left;vertical-align:top}}th{{background:#f5f6f7}}
code,pre{{background:#f1f3f4}}code{{padding:2px 4px}}pre{{padding:12px;overflow:auto;font-size:12px}}
li{{margin:5px 0}}
</style>
</head>
<body>
<h1>EchoMem develop + PR192 + PR199 测试报告</h1>
<p class="muted">测试日期：2026-08-05　|　LoCoMo：conv-30　|　真实 DashScope 模型调用</p>

<h2>代码基线</h2>
<table>
<tr><th>项目</th><th>版本</th></tr>
<tr><td>EchoMem 分支</td><td><code>develop</code></td></tr>
<tr><td>develop commit</td><td><code>684bfef61846745c5fd9094a8757fbfbd8d1714f</code></td></tr>
<tr><td>PR192</td><td><code>b91be9883f5177db79404777053849eff4c2655b</code></td></tr>
<tr><td>PR199</td><td><code>88573f7740681b2c202c0083e47452583a35e72c</code></td></tr>
<tr><td>合并测试 HEAD</td><td><code>e6cdbfcc86f99eb25232bdc435ee916b2f8bf819</code></td></tr>
<tr><td>Eval Harness</td><td><code>v3_mcpTool</code> @ <code>524e4d47abe69fc64ac5ce83247994325b38ccfb</code></td></tr>
</table>

<h2>结论</h2>
<div class="callout">
<strong>带 MCP 工具调用轮次完成且稳定性更好：</strong>19/19 session 成功，注入 17 分 19 秒，
QA Judge 准确率 72.84%。不带工具调用轮次注入 24 分 39 秒，19 个 session 中 1 个失败，
失败原因为 atomic_engine 的 organized vectors 读取超时。第一轮结果不能作为无错误正式分数，
但可以作为缺陷复现证据。
</div>

<h2>测试配置</h2>
<ul>
<li>LLM：<code>deepseek-v4-flash</code>；Embedding：<code>text-embedding-v3</code>；DashScope OpenAI-compatible API。</li>
<li>Thinking：<code>disabled</code>。</li>
<li>EchoMem engine：<code>atomic_engine</code>。</li>
<li>每轮使用新 tenant/user，重新注入完整 19 个 session，然后执行 81 道 QA 和 Judge。</li>
<li>带工具轮次通过真实 EchoMem MCP（<code>127.0.0.1:8001/mcp</code>）执行工具循环。</li>
</ul>

<h2>结果对比</h2>
<table>
<tr><th>模式</th><th>注入成功</th><th>注入耗时</th><th>Judge</th><th>准确率</th><th>状态</th></tr>
{rows}
</table>
<p>带工具轮次共观察到 <code>{tool_calls}</code> 次工具调用，{read_questions}/81 道题读取了
相关 <code>current/messages.jsonl</code>。</p>

<h2>缺陷与异常</h2>
<ol>
<li><span class="bad">atomic_engine organized vectors 读取超时：</span>
第一轮 session_4 的 archive_002 在约 196.9 秒后失败，EchoMem 日志为
<code>organized vectors: The read operation timed out</code>，随后 commit status=failed。
这是本轮需要反馈给 EchoMem 的核心缺陷。</li>
<li><span class="bad">默认自动 commit 拆分导致注入变慢：</span>
EchoMem 默认字符阈值为 <code>1000</code>，一个原始 session 会被拆成多个 archive；
第一轮 session_1（28 条消息）耗时 133.6 秒。该现象不是客户端误报。</li>
<li><span class="good">未复现 ConnectError/ConnectionResetError：</span>
两轮测试的 QA 请求均完成，第二轮 19 个 session 全部 commit 成功。</li>
<li><span class="good">未发现 MCP HTTP 5xx：</span>
MCP 工具调用轮次完成 81/81 QA，工具审计和 transcript read 记录均已保存。</li>
</ol>

<h2>复现证据</h2>
<pre>第一轮 import_results.csv:
conv-30,session_4,...,failed,198.7,19,19,organized vectors: The read operation timed out

EchoMem 日志:
memory_extraction_failed engine_id=atomic_engine ...
root_error_message=organized vectors: The read operation timed out
commit_failed ... status=failed</pre>

<h2>附件</h2>
<ul>
<li><code>no_tools/</code>：无工具调用轮次的 summary、import、QA、Judge、diagnosis、run.log。</li>
<li><code>tools/</code>：MCP 工具调用轮次的 summary、import、QA、Judge、diagnosis、tool_audits、run.log。</li>
<li><code>echomem.jsonl</code>：两轮共用的 EchoMem 服务端日志。</li>
<li><code>server_errors.log</code>：从服务端日志筛出的错误/timeout 行。</li>
</ul>
</body>
</html>
"""
    (OUTPUT / "report.html").write_text(report, encoding="utf-8")
    (OUTPUT / "README.md").write_text(
        """# EchoMem develop + PR192 + PR199 测试报告

打开 `report.html` 查看结论。原始结果按 `no_tools/` 和 `tools/` 分类，服务端日志为
`echomem.jsonl`，错误筛选日志为 `server_errors.log`。报告中的身份 key 已脱敏。
""",
        encoding="utf-8",
    )
    print(OUTPUT / "report.html")


if __name__ == "__main__":
    main()
