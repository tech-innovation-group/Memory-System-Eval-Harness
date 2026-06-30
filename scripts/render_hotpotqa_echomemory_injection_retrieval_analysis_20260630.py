#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web")
FORMAL_DIR = ROOT / "runs/echomemory_hotpotqa_500_20260630_031739/echomemory_generic_qa"
FORMAL_CSV = FORMAL_DIR / "echomemory_hotpotqa_generic_qa_results.csv"
FORMAL_RUNNING = FORMAL_DIR / "running_summary.json"
FORMAL_STATUS = FORMAL_DIR / "generic_qa_status.json"
FORMAL_IMPORT_SUMMARY = FORMAL_DIR / "echomemory_import/echomemory_generic_import_summary.json"
PROBE3_DIR = ROOT / "runs/echomemory_hotpotqa_probe3_fix_20260630_095027/echomemory_generic_qa"
PROBE3_CSV = PROBE3_DIR / "echomemory_generic_qa_results.csv"
PROBE3_SUMMARY = PROBE3_DIR / "hotpotqa_answer_summary.json"
TOOLFIX_DIR = ROOT / "runs/echomemory_hotpotqa_toolpollution_fix_20260630_093647/echomemory_generic_qa"
TOOLFIX_CSV = TOOLFIX_DIR / "echomemory_generic_qa_results.csv"
COMPARE_HTML = ROOT / "web/static/generated-reports/hotpotqa_500_openviking_v044_vs_echomemory_latest.html"
OLD_GAP_HTML = ROOT / "docs/echomemory_hotpotqa_gap_analysis_20260630.html"
OUT_DOC = ROOT / "docs/hotpotqa_echomemory_injection_retrieval_analysis_20260630.html"
OUT_WEB = ROOT / "web/static/generated-reports/hotpotqa_echomemory_injection_retrieval_analysis_20260630.html"

POLLUTION_PATTERNS = [
    r"<memory_",
    r"<functioncall",
    r"<function\b",
    r"memory_search",
    r"let me search",
    r"let me retrieve",
    r"<｜DSML｜",
    r"根据记忆中的信息",
    r"让我搜索",
    r"based on the retrieved",
]

REPRESENTATIVE_QIDS = [
    "5a8c7595554299585d9e36b6",
    "5a8e3ea95542995a26add48d",
    "5a85b2d95542997b5ce40028",
    "5a8db19d5542994ba4e3dd00",
]


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def pct(n: float | int | None) -> str:
    if n is None:
        return "-"
    return f"{float(n) * 100:.2f}%"


def esc(value: Any) -> str:
    return html.escape(str(value))


def compact(text: str, limit: int = 220) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def is_polluted(response: str) -> bool:
    return any(re.search(pattern, response or "", re.I) for pattern in POLLUTION_PATTERNS)


def parse_json_list(raw: str) -> list[dict[str, Any]]:
    text = str(raw or "").strip()
    if not text or text in {"[]", "null", "(none)"}:
        return []
    try:
        data = json.loads(text)
    except Exception:
        return []
    return data if isinstance(data, list) else []


def formal_stats(rows: list[dict[str, str]]) -> dict[str, Any]:
    retrieval_status = Counter(row.get("retrieval_status") or "" for row in rows)
    health_status = Counter(row.get("health_status") or "" for row in rows)
    answer_status = Counter(row.get("answer_status") or "" for row in rows)
    evidence_source = Counter(row.get("final_evidence_source") or "" for row in rows)
    polluted = [row for row in rows if is_polluted(row.get("response") or "")]
    nonempty = sum(1 for row in rows if parse_json_list(row.get("relevant_memory") or ""))
    return {
        "rows": len(rows),
        "nonempty_relevant_memory_rows": nonempty,
        "polluted_rows": len(polluted),
        "polluted_qids": [row.get("question_id") or "" for row in polluted],
        "retrieval_status": dict(retrieval_status),
        "health_status": dict(health_status),
        "answer_status": dict(answer_status),
        "evidence_source": dict(evidence_source),
    }


def row_map(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row.get("question_id") or "": row for row in rows}


def extract_examples(rows_by_qid: dict[str, dict[str, str]], qids: list[str]) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for qid in qids:
        row = rows_by_qid.get(qid)
        if not row:
            continue
        examples.append(
            {
                "question_id": qid,
                "gold": row.get("answer") or "-",
                "response": row.get("response") or "-",
                "retrieval_status": row.get("retrieval_status") or "-",
                "retrieval_count": row.get("retrieval_count") or "-",
                "memory_hit_count": row.get("memory_hit_count") or "-",
                "answer_stage": row.get("answer_stage") or "-",
                "health_status": row.get("health_status") or "-",
                "polluted": is_polluted(row.get("response") or ""),
            }
        )
    return examples


def first_import_payload_info() -> dict[str, Any]:
    files = sorted((FORMAL_DIR / "echomemory_import").glob("*_messages.json"))
    if not files:
        return {}
    data = json.loads(files[0].read_text(encoding="utf-8"))
    messages = data if isinstance(data, list) else []
    first = messages[0] if messages else {}
    return {
        "file": str(files[0]),
        "message_count": len(messages),
        "first_role": first.get("role") if isinstance(first, dict) else "",
        "first_content_prefix": compact(first.get("content") if isinstance(first, dict) else "", 260),
    }


def import_summary_brief(data: dict[str, Any]) -> dict[str, Any]:
    records = data.get("records") or []
    if not isinstance(records, list):
        records = []
    complete = sum(1 for item in records if str(item.get("status") or "").endswith("DONE"))
    ready = sum(1 for item in records if item.get("qa_ready_after_commit"))
    avg_expected = round(sum(int(item.get("expected_messages") or 0) for item in records) / len(records), 2) if records else 0
    avg_submitted = round(sum(int(item.get("submitted_messages") or 0) for item in records) / len(records), 2) if records else 0
    return {
        "records": len(records),
        "done_records": complete,
        "qa_ready_after_commit": ready,
        "avg_expected_messages": avg_expected,
        "avg_submitted_messages": avg_submitted,
    }


def kv_row(label: str, value: str) -> str:
    return f"<div class='kv-row'><span>{esc(label)}</span><strong>{esc(value)}</strong></div>"


def table_from_examples(title: str, examples: list[dict[str, Any]]) -> str:
    rows = []
    for item in examples:
        rows.append(
            "<tr>"
            f"<td><code>{esc(item['question_id'])}</code></td>"
            f"<td>{esc(compact(item['gold'], 80))}</td>"
            f"<td>{esc(compact(item['response'], 180))}</td>"
            f"<td>{esc(item['retrieval_status'])}</td>"
            f"<td>{esc(item['retrieval_count'])}</td>"
            f"<td>{esc('yes' if item['polluted'] else 'no')}</td>"
            "</tr>"
        )
    return (
        f"<section><h2>{esc(title)}</h2>"
        "<table><thead><tr><th>question_id</th><th>Gold</th><th>Response</th><th>retrieval_status</th><th>retrieval_count</th><th>污染</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )


def render() -> str:
    formal_rows = load_csv(FORMAL_CSV)
    probe3_rows = load_csv(PROBE3_CSV)
    toolfix_rows = load_csv(TOOLFIX_CSV)
    formal_running = load_json(FORMAL_RUNNING)
    formal_status = load_json(FORMAL_STATUS)
    formal_import = load_json(FORMAL_IMPORT_SUMMARY)
    probe3_summary = load_json(PROBE3_SUMMARY)
    formal = formal_stats(formal_rows)
    formal_examples = extract_examples(row_map(formal_rows), REPRESENTATIVE_QIDS)
    probe3_examples = extract_examples(row_map(probe3_rows), [row.get("question_id") or "" for row in probe3_rows])
    toolfix_examples = extract_examples(row_map(toolfix_rows), [row.get("question_id") or "" for row in toolfix_rows])
    import_payload = first_import_payload_info()
    import_brief = import_summary_brief(formal_import)

    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>HotpotQA EchoMemory 注入/召回/低分原因分析</title>
  <style>
    :root {{
      --bg: #f7f5f0;
      --surface: #ffffff;
      --border: #e5ded2;
      --text: #111827;
      --muted: #6b7280;
      --blue: #2563eb;
      --green: #16a34a;
      --orange: #d97706;
      --red: #dc2626;
      --radius: 8px;
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      max-width: 1160px;
      margin: 0 auto;
      padding: 28px 20px 56px;
    }}
    section {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 20px 22px;
      margin-bottom: 14px;
    }}
    h1, h2, h3 {{ margin: 0 0 10px; }}
    h1 {{ font-size: 26px; }}
    h2 {{ font-size: 18px; }}
    h3 {{ font-size: 15px; }}
    p, li {{ color: var(--muted); }}
    p {{ margin: 0 0 10px; }}
    ul, ol {{ margin: 8px 0 0 18px; padding: 0; }}
    li + li {{ margin-top: 6px; }}
    code {{
      font-family: var(--mono);
      background: #f3f4f6;
      padding: 1px 5px;
      border-radius: 4px;
    }}
    pre {{
      margin: 10px 0 0;
      padding: 12px 14px;
      border-radius: 8px;
      background: #111827;
      color: #e5e7eb;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      font: 12px/1.5 var(--mono);
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }}
    .metric {{
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 12px;
      background: #fcfbf8;
    }}
    .metric span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
    }}
    .metric strong {{
      display: block;
      font-size: 16px;
    }}
    .kv {{
      display: grid;
      gap: 8px;
    }}
    .kv-row {{
      display: grid;
      grid-template-columns: 180px minmax(0, 1fr);
      gap: 12px;
      min-width: 0;
      align-items: start;
    }}
    .kv-row span {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
    }}
    .kv-row strong {{
      min-width: 0;
      font-size: 13px;
      font-weight: 650;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
    }}
    th, td {{
      text-align: left;
      vertical-align: top;
      padding: 10px 12px;
      border-top: 1px solid var(--border);
    }}
    th {{
      color: var(--muted);
      font-weight: 600;
      font-size: 12px;
    }}
    .note {{
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: #fcfbf8;
      color: var(--muted);
    }}
    .note.warn {{ border-color: #fed7aa; background: #fff7ed; }}
    .note.ok {{ border-color: #bbf7d0; background: #f0fdf4; }}
    .path {{ font-family: var(--mono); word-break: break-all; color: var(--text); }}
    @media (max-width: 960px) {{
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .kv-row {{ grid-template-columns: minmax(0, 1fr); gap: 4px; }}
    }}
    @media (max-width: 640px) {{
      .grid {{ grid-template-columns: minmax(0, 1fr); }}
    }}
  </style>
</head>
<body>
  <main>
    <section>
      <h1>HotpotQA EchoMemory 注入 / 召回 / 低分原因分析</h1>
      <p>这份报告只用当前还能直接复核的文件做结论：正式 500 目录里的导入消息、当前结果 CSV、running/import summary，以及两次代表性 probe。旧对比报告里的 EM/F1 只作为“当时运行快照”，不会和当前可复核事实混在一起。</p>
    </section>

    <section>
      <h2>一句话结论</h2>
      <ul>
        <li><strong>注入链路是通的。</strong> HotpotQA 每题 context 被拆成文档，再按单题 session 导入 EchoMemory；当前正式 500 目录里仍能直接看到每题的 <code>echomemory_import/*_messages.json</code>。</li>
        <li><strong>召回整体不是失效。</strong> 当前正式运行已写出 39 行，其中 <strong>{formal['nonempty_relevant_memory_rows']}/{formal['rows']}</strong> 行 <code>relevant_memory</code> 非空，<strong>{formal['retrieval_status'].get('ok', 0)}</strong> 行 <code>retrieval_status=ok</code>。</li>
        <li><strong>准确率低主要不是“没检索到”。</strong> 更像是两段问题叠加：一段是 answer-only 下的输出污染，一段是 bridge / multi-hop 题 second-hop 证据落点不稳。</li>
      </ul>
    </section>

    <section>
      <h2>当前可复核证据</h2>
      <div class="kv">
        {kv_row("正式 500 目录", str(FORMAL_DIR.parent))}
        {kv_row("当前正式结果 CSV", str(FORMAL_CSV))}
        {kv_row("当前 running summary", str(FORMAL_RUNNING))}
        {kv_row("当前 import summary", str(FORMAL_IMPORT_SUMMARY))}
        {kv_row("代表性 probe3", str(PROBE3_CSV))}
        {kv_row("对比页快照", str(COMPARE_HTML))}
        {kv_row("旧 gap 报告", str(OLD_GAP_HTML))}
      </div>
      <p class="note warn">注意：旧对比页和旧 gap 报告里引用的是当时的运行快照。现在真正还能直接复核的正式结果文件名是 <code>echomemory_hotpotqa_generic_qa_results.csv</code>，不是旧报告里写的那个 CSV 路径。</p>
    </section>

    <section>
      <h2>记忆注入流程</h2>
      <ol>
        <li><strong>拆题：</strong><code>hotpotqa_job_plan()</code> 从每题 <code>context</code> 生成 <code>memory_documents</code>。实现位置：<code>scripts/benchmark_adapter.py:786</code>，文档生成在 <code>collect_hotpotqa_documents()</code>（<code>:750</code>）。</li>
        <li><strong>转消息：</strong><code>import_messages_from_plan()</code> 把每篇文档包装成一条 <code>[benchmark memory]</code> 消息。实现位置：<code>scripts/echomemory_generic_qa.py:175</code>。</li>
        <li><strong>写入 EchoMemory：</strong> 运行器先完成 import / settle / repair，再把该题送进 QA；相关记录会回填到 <code>import_session_id</code>、<code>import_status</code>、<code>memory_injection_time_s</code>。实现位置：<code>scripts/echomemory_generic_qa.py:866</code>。</li>
        <li><strong>做召回：</strong><code>answer_question()</code> 完成 retrieval 后，把命中的 <code>hits</code> 同时写进 <code>relevant_memory</code> 和 qNNN <code>.recall.json</code>。实现位置：<code>scripts/echomemory_memory_qa.py:3050</code> 和 <code>:3088</code>。</li>
      </ol>
      <pre>HotpotQA JSON
  -> hotpotqa_job_plan()
  -> collect_hotpotqa_documents()
  -> import_messages_from_plan()
  -> EchoMemory import / settle / optional repair
  -> answer_question()
  -> relevant_memory + retrieval_count + qNNN.recall.json
  -> answer-only EM/F1</pre>
    </section>

    <section>
      <h2>当前注入是否正常</h2>
      <div class="grid">
        <article class="metric"><span>import records</span><strong>{import_brief.get('records', 0)}</strong></article>
        <article class="metric"><span>status=DONE</span><strong>{import_brief.get('done_records', 0)}</strong></article>
        <article class="metric"><span>qa_ready_after_commit</span><strong>{import_brief.get('qa_ready_after_commit', 0)}</strong></article>
        <article class="metric"><span>每题平均消息数</span><strong>{import_brief.get('avg_submitted_messages', 0)}</strong></article>
      </div>
      <p>当前正式目录里的 import summary 说明每题确实先走了文档注入。并且当前磁盘上还保留了每题的注入消息文件，例如：</p>
      <pre>{esc(json.dumps(import_payload, ensure_ascii=False, indent=2))}</pre>
      <p>这说明当前问题不是“HotpotQA 没有被注入”，而是“注入完成后，后续 retrieval / answer 阶段效果不理想”。</p>
    </section>

    <section>
      <h2>当前召回是否正常</h2>
      <div class="grid">
        <article class="metric"><span>已写出题数</span><strong>{formal['rows']}</strong></article>
        <article class="metric"><span>relevant_memory 非空</span><strong>{formal['nonempty_relevant_memory_rows']}/{formal['rows']}</strong></article>
        <article class="metric"><span>retrieval_status=ok</span><strong>{formal['retrieval_status'].get('ok', 0)}</strong></article>
        <article class="metric"><span>retrieval_status=empty</span><strong>{formal['retrieval_status'].get('empty', 0)}</strong></article>
      </div>
      <div class="kv" style="margin-top:12px">
        {kv_row("health_status", json.dumps(formal["health_status"], ensure_ascii=False))}
        {kv_row("final_evidence_source", json.dumps(formal["evidence_source"], ensure_ascii=False))}
        {kv_row("running status", str(formal_running.get("status")))}
        {kv_row("current stage", str(formal_status.get("stage")))}
      </div>
      <p class="note ok">结论：当前召回整体上是“在工作”，不是“整体失效”。39 行里 36 行都有非空 <code>relevant_memory</code>，说明大多数题都拿回了 evidence。</p>
      <p class="note warn">但“召回正常”不等于“召回质量已经够好”。当前 evidence source 以 <code>atom</code> 为主，但 bridge / multi-hop 题常常只把首跳实体或相关文档召回，第二跳需要的关键属性值并不稳定。</p>
    </section>

    {table_from_examples("正式 500 当前样本中的代表性问题", formal_examples)}
    {table_from_examples("probe3 修复后的代表性结果", probe3_examples)}
    {table_from_examples("tool-pollution fix 探针结果", toolfix_examples)}

    <section>
      <h2>为什么准确率低</h2>
      <table>
        <thead><tr><th>原因</th><th>当前证据</th><th>影响</th></tr></thead>
        <tbody>
          <tr>
            <td>最终答案污染</td>
            <td>当前正式 39 行里，按直接模式匹配，至少 <strong>{formal['polluted_rows']}</strong> 行输出里残留了 <code>memory_search</code>、DSML、函数调用，或者“根据记忆中的信息 / let me search”式文本。</td>
            <td>在 HotpotQA answer-only EM/F1 口径下，哪怕 evidence 是对的，这种输出也会直接掉分。</td>
          </tr>
          <tr>
            <td>second-hop 证据不稳</td>
            <td><code>5a8c7595554299585d9e36b6</code> 在 probe3 里已经能召回 <code>Shirley Temple</code> 文档，且该文档包含 <code>Chief of Protocol</code>，但最终仍答成 <code>United States ambassador to Ghana</code>。</td>
            <td>说明问题不是简单“没召回”，而是多候选属性值没有被正确选中。</td>
          </tr>
          <tr>
            <td>少数题确实空召回</td>
            <td><code>5a85b2d95542997b5ce40028</code> 在正式 500 当前样本里 <code>retrieval_count=0</code>，属于真实的 retrieval empty；但在 probe3 里同题已经变成 <code>retrieval_status=ok</code>、答案正确。</td>
            <td>说明 query / follow-up retrieval 的改进对部分题有效，但正式运行的旧样本里还保留着空召回问题。</td>
          </tr>
          <tr>
            <td>这条架构给 HotpotQA 额外加噪</td>
            <td>当前实现是“每题先写 EchoMemory，再等稳定，再检索回来”；不是直接在原始 context 上做问答。</td>
            <td>相比直接 document QA，多了一层 import / settle / rerank / prompt 对齐噪声，也显著拖慢速度。</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section>
      <h2>为什么说主因不是“完全没召回”</h2>
      <ul>
        <li>正式 500 当前样本：<strong>{formal['nonempty_relevant_memory_rows']}/{formal['rows']}</strong> 行 <code>relevant_memory</code> 非空。</li>
        <li>正式 500 当前样本：<strong>{formal['retrieval_status'].get('ok', 0)}</strong> 行 <code>retrieval_status=ok</code>，只有 <strong>{formal['retrieval_status'].get('empty', 0)}</strong> 行是 empty。</li>
        <li>probe3 当前已经拿到 <strong>{pct(probe3_summary.get('answer_em'))}</strong> / <strong>{pct(probe3_summary.get('answer_f1'))}</strong> 的 3 题 answer-only 结果，且三题全部 <code>retrieval_status=ok</code>。</li>
      </ul>
      <p>所以更准确的说法是：<strong>当前 EchoMemory 在 HotpotQA 上的问题是“召回 often works, but not stably enough for multi-hop answer selection”</strong>，而不是“召回系统整体坏了”。</p>
    </section>

    <section>
      <h2>速度侧结论</h2>
      <div class="grid">
        <article class="metric"><span>平均写入注入</span><strong>{formal_running.get('avg_memory_injection_time_s')}s</strong></article>
        <article class="metric"><span>平均索引等待</span><strong>{formal_running.get('avg_memory_settle_wait_time_s')}s</strong></article>
        <article class="metric"><span>平均 QA</span><strong>{formal_running.get('avg_qa_time_s')}s</strong></article>
        <article class="metric"><span>平均端到端</span><strong>{formal_running.get('avg_end_to_end_time_s')}s</strong></article>
      </div>
      <p>当前正式运行里，速度瓶颈仍然主要在 “写入记忆 + 等待异步稳定”，不是回答模型本身。这一点和旧 gap 报告里的结论是一致的，而且现在仍能被 <code>running_summary.json</code> 和 <code>generic_qa_status.json</code> 直接复核。</p>
    </section>

    <section>
      <h2>建议的下一步</h2>
      <ol>
        <li>继续压最终答案污染：把 tool-like / search-like 文本从最终 response 中彻底去掉。</li>
        <li>继续加强 second-hop query 组织：尤其是 bridge 题里的“实体 -> 属性” follow-up 查询。</li>
        <li>把正式 500 旧样本和 probe3 新样本按同题做 before/after 对照，专门看“空召回修复”和“仍然答错但已非污染”的两类题。</li>
        <li>如果目标是纯 HotpotQA answer-only 上限，需要重新评估“每题先 commit 到 EchoMemory 再搜回来”这条架构是否值得保留。</li>
      </ol>
    </section>
  </main>
</body>
</html>
"""
    return html_doc


def main() -> None:
    rendered = render()
    OUT_DOC.write_text(rendered, encoding="utf-8")
    OUT_WEB.parent.mkdir(parents=True, exist_ok=True)
    OUT_WEB.write_text(rendered, encoding="utf-8")
    print(OUT_DOC)
    print(OUT_WEB)


if __name__ == "__main__":
    main()
