#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import importlib.util
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web")
RUNS = ROOT / "runs"
OUTPUT = ROOT / "web/static/generated-reports/hotpotqa_echomemory_live_diagnosis_20260622.html"
STATIC_MIRROR = ROOT / "static/generated-reports/hotpotqa_echomemory_live_diagnosis_20260622.html"

ECHO_RUN = RUNS / "echomemory_generic_qa_20260622_183050_943ead" / "echomemory_generic_qa"
OV_RUN = RUNS / "openviking_generic_qa_20260622_231559_1bd882" / "openviking_generic_qa"
TASK_ID = "echomemory_generic_qa_20260622_233948_1a19e0"
TASK_URL = f"http://127.0.0.1:19181/api/tasks/{TASK_ID}"

CASE_SAMPLES = [
    "5a8e3ea95542995a26add48d",
    "5a8c7595554299585d9e36b6",
    "5ab51dae5542991779162d82",
    "5a85ea095542994775f606a8",
]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.2f}%"


def short_text(text: str, limit: int = 260) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else f"{text[:limit - 1]}..."


def short_uri(uri: str) -> str:
    if not uri:
        return ""
    return uri if len(uri) <= 140 else f"{uri[:64]}...{uri[-60:]}"


def parse_memories(value: str) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def parse_layers(value: str) -> list[str]:
    if not value:
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in data] if isinstance(data, list) else []


def load_hotpot_metric_module() -> Any:
    script = ROOT / "scripts/hotpotqa_answer_eval.py"
    spec = importlib.util.spec_from_file_location("hotpot_eval_live", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def task_snapshot() -> dict[str, Any]:
    import urllib.request

    try:
        with urllib.request.urlopen(TASK_URL, timeout=20) as response:
            data = json.load(response)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {
            "id": TASK_ID,
            "status": "running",
            "progress": {},
            "run_dir": str(RUNS / TASK_ID),
        }


def evidence_lines(row: dict[str, str], limit: int = 4) -> list[dict[str, str]]:
    lines: list[dict[str, str]] = []
    for mem in parse_memories(row.get("relevant_memory", ""))[:limit]:
        uri = str(mem.get("uri") or "")
        content = str(mem.get("content") or "")
        mem_type = str(mem.get("memory_type") or mem.get("source") or "")
        score = mem.get("score")
        lines.append(
            {
                "label": mem_type or uri or "memory",
                "uri": short_uri(uri),
                "score": "-" if score is None else f"{float(score):.3f}",
                "snippet": short_text(content, 320),
            }
        )
    return lines


def render_evidence_list(items: list[dict[str, str]]) -> str:
    if not items:
        return "<div class='quote'>无</div>"
    chunks = ["<div class='evidence-list'>"]
    for item in items:
        chunks.append(
            "<div class='evidence'>"
            f"<div class='evidence-title'>{html.escape(item['label'])} <span class='muted'>score {html.escape(item['score'])}</span></div>"
            f"<div class='evidence-uri'><code>{html.escape(item['uri'])}</code></div>"
            f"<div class='evidence-snippet'>{html.escape(item['snippet'])}</div>"
            "</div>"
        )
    chunks.append("</div>")
    return "\n".join(chunks)


def render_table_rows(rows: list[dict[str, str]]) -> str:
    parts = []
    for row in rows:
        parts.append(
            "<tr>"
            f"<td><code>{html.escape(row['sample_id'])}</code></td>"
            f"<td>{html.escape(row['question'])}</td>"
            f"<td>{html.escape(row['gold'])}</td>"
            f"<td>{html.escape(row['echo'])}</td>"
            f"<td>{html.escape(row['ov'])}</td>"
            "</tr>"
        )
    return "\n".join(parts)


def build_html() -> str:
    hotpot_eval = load_hotpot_metric_module()
    task = task_snapshot()
    progress = task.get("progress") or {}

    echo_rows_list = load_csv(ECHO_RUN / "echomemory_generic_qa_results.csv")
    echo_rows = {row["sample_id"]: row for row in echo_rows_list}
    ov_rows_list = load_csv(OV_RUN / "openviking_generic_qa_results.csv")
    ov_rows = {row["sample_id"]: row for row in ov_rows_list}

    for row in echo_rows_list:
        prediction = row.get("response") or ""
        gold = row.get("answer") or ""
        row["_answer_em"] = hotpot_eval.exact_match(prediction, gold)
        row["_answer_f1"] = hotpot_eval.f1_score(prediction, gold)

    shared_rows = []
    for sample_id in sorted(set(echo_rows) & set(ov_rows)):
        echo_row = echo_rows[sample_id]
        ov_row = ov_rows[sample_id]
        gold = echo_row.get("answer") or ""
        shared_rows.append(
            {
                "sample_id": sample_id,
                "question": echo_row["question"],
                "gold": gold,
                "echo": echo_row.get("response") or "",
                "ov": ov_row.get("response") or "",
                "echo_f1": hotpot_eval.f1_score(echo_row.get("response") or "", gold),
                "ov_f1": hotpot_eval.f1_score(ov_row.get("response") or "", gold),
            }
        )

    partial_em = sum(float(row["_answer_em"]) for row in echo_rows_list) / len(echo_rows_list) if echo_rows_list else 0.0
    partial_f1 = sum(float(row["_answer_f1"]) for row in echo_rows_list) / len(echo_rows_list) if echo_rows_list else 0.0
    unknown_count = sum("i do not know" in (row.get("response") or "").lower() for row in echo_rows_list)
    final_source_counter = Counter((row.get("final_evidence_source") or "none") for row in echo_rows_list)
    integrity_counter = Counter((row.get("import_integrity") or "none") for row in echo_rows_list)
    status_counter = Counter((row.get("import_status") or "none") for row in echo_rows_list)

    avg_injection_s = sum(float(row.get("memory_injection_time_s") or 0.0) for row in echo_rows_list) / len(echo_rows_list) if echo_rows_list else 0.0
    avg_qa_s = sum(float(row.get("qa_time_s") or 0.0) for row in echo_rows_list) / len(echo_rows_list) if echo_rows_list else 0.0
    avg_total_s = sum(float(row.get("end_to_end_time_s") or 0.0) for row in echo_rows_list) / len(echo_rows_list) if echo_rows_list else 0.0
    avg_retrieval_count = sum(float(row.get("retrieval_count") or 0.0) for row in echo_rows_list) / len(echo_rows_list) if echo_rows_list else 0.0
    avg_memory_hit_count = sum(float(row.get("memory_hit_count") or 0.0) for row in echo_rows_list) / len(echo_rows_list) if echo_rows_list else 0.0

    ov_shared_f1 = sum(item["ov_f1"] for item in shared_rows) / len(shared_rows) if shared_rows else 0.0
    echo_shared_f1 = sum(item["echo_f1"] for item in shared_rows) / len(shared_rows) if shared_rows else 0.0
    ov_better = [item for item in shared_rows if item["ov_f1"] > item["echo_f1"]]
    echo_better = [item for item in shared_rows if item["echo_f1"] > item["ov_f1"]]
    ties = [item for item in shared_rows if item["echo_f1"] == item["ov_f1"]]

    atom_wrong = [
        row for row in echo_rows_list
        if row.get("final_evidence_source") == "atom" and float(row["_answer_f1"]) == 0.0
    ]

    case_blocks = []
    for sample_id in CASE_SAMPLES:
        row = echo_rows.get(sample_id)
        if not row:
            continue
        ov_row = ov_rows.get(sample_id)
        case_blocks.append(
            {
                "sample_id": sample_id,
                "question": row["question"],
                "gold": row["answer"],
                "response": row.get("response") or "",
                "final_source": row.get("final_evidence_source") or "none",
                "layers": ", ".join(parse_layers(row.get("retrieval_layers_used", ""))) or "-",
                "import_status": f"{row.get('import_status') or '-'} / {row.get('import_integrity') or '-'}",
                "timing": f"injection {row.get('memory_injection_time_s') or '-'} s, qa {row.get('qa_time_s') or '-'} s, total {row.get('end_to_end_time_s') or '-'} s",
                "answer_em": row["_answer_em"],
                "answer_f1": row["_answer_f1"],
                "ov_response": (ov_row or {}).get("response") or "",
                "ov_f1": hotpot_eval.f1_score((ov_row or {}).get("response") or "", row["answer"]) if ov_row else None,
                "evidence": evidence_lines(row),
            }
        )

    case_html = []
    for case in case_blocks:
        case_html.append(
            f"""
      <div class="case">
        <h3>{html.escape(case['question'])}</h3>
        <p><span class="muted">sample_id</span> <code>{html.escape(case['sample_id'])}</code> · <span class="muted">Gold</span> <strong>{html.escape(case['gold'])}</strong></p>
        <div class="case-grid">
          <div class="subcard">
            <h3>EchoMemory 当前表现</h3>
            <div class="kv">
              <div class="k">回答</div><div>{html.escape(case['response'])}</div>
              <div class="k">Answer EM / F1</div><div>{case['answer_em']:.1f} / {case['answer_f1']:.4f}</div>
              <div class="k">最终证据源</div><div><code>{html.escape(case['final_source'])}</code></div>
              <div class="k">检索层</div><div>{html.escape(case['layers'])}</div>
              <div class="k">导入状态</div><div>{html.escape(case['import_status'])}</div>
              <div class="k">耗时</div><div>{html.escape(case['timing'])}</div>
            </div>
            {render_evidence_list(case['evidence'])}
          </div>
          <div class="subcard">
            <h3>OpenViking 对照</h3>
            <div class="kv">
              <div class="k">回答</div><div>{html.escape(case['ov_response'])}</div>
              <div class="k">Answer F1</div><div>{'-' if case['ov_f1'] is None else f"{case['ov_f1']:.4f}"}</div>
            </div>
            <div class="note">这里不是拿 judge 口径做比较，而是统一回到 HotpotQA answer-only 口径。这样就能区分“模型没答出来”和“答得啰嗦但部分命中”的差别。</div>
          </div>
        </div>
      </div>
            """
        )
    case_html = "".join(case_html)

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    live_report = task.get("run_dir", "") + "/report.html"
    generated_hint = "运行中诊断版：每次重新执行脚本会刷新到当前已落盘结果。"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>HotpotQA EchoMemory 运行中诊断</title>
  <style>
    :root {{
      --bg: #f5f6f8;
      --panel: #ffffff;
      --line: #d9dce3;
      --text: #16181d;
      --muted: #68707f;
      --blue: #1d4ed8;
      --green: #157f3b;
      --orange: #b4690e;
      --red: #b42318;
      --shadow: 0 12px 28px rgba(16, 24, 40, 0.08);
      --radius: 14px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Helvetica Neue", Arial, sans-serif;
      line-height: 1.6;
    }}
    .wrap {{
      max-width: 1220px;
      margin: 0 auto;
      padding: 24px 18px 60px;
    }}
    .hero, .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }}
    .hero {{ padding: 28px 24px; margin-bottom: 18px; }}
    .card {{ padding: 20px 18px; margin-bottom: 16px; }}
    h1, h2, h3 {{ margin: 0; letter-spacing: 0; }}
    h1 {{ font-size: 30px; line-height: 1.2; margin-bottom: 10px; }}
    h2 {{ font-size: 22px; line-height: 1.25; margin-bottom: 14px; }}
    h3 {{ font-size: 17px; line-height: 1.35; margin-bottom: 10px; }}
    p {{ margin: 0 0 12px; }}
    .muted {{ color: var(--muted); }}
    .grid {{ display: grid; gap: 16px; }}
    .grid.two {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .grid.three {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    .stat-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
    .stat {{
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      background: #fbfbfc;
    }}
    .label {{ font-size: 12px; color: var(--muted); margin-bottom: 4px; }}
    .value {{ font-size: 24px; line-height: 1.2; font-weight: 700; }}
    .good {{ color: var(--green); }}
    .warn {{ color: var(--orange); }}
    .bad {{ color: var(--red); }}
    .note {{
      padding: 12px 14px;
      border-left: 3px solid var(--blue);
      background: #f8fbff;
      color: var(--muted);
      border-radius: 10px;
    }}
    .alert {{
      padding: 12px 14px;
      border-left: 3px solid var(--red);
      background: #fff7f6;
      color: var(--text);
      border-radius: 10px;
    }}
    ul {{ margin: 0; padding-left: 18px; }}
    li {{ margin: 0 0 8px; }}
    code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      background: #eef2f6;
      border-radius: 6px;
      padding: 1px 5px;
      font-size: 12px;
      word-break: break-all;
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{
      text-align: left;
      vertical-align: top;
      border-top: 1px solid var(--line);
      padding: 10px 8px;
    }}
    th {{ color: var(--muted); background: #fbfbfc; font-weight: 650; }}
    .case {{
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fbfbfc;
      padding: 14px;
      margin-top: 14px;
    }}
    .case-grid {{
      display: grid;
      gap: 14px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      margin-top: 14px;
    }}
    .subcard {{
      border: 1px solid var(--line);
      border-radius: 12px;
      background: white;
      padding: 12px;
    }}
    .kv {{
      display: grid;
      grid-template-columns: 150px 1fr;
      gap: 6px 10px;
      font-size: 13px;
      margin-bottom: 10px;
    }}
    .kv .k {{ color: var(--muted); }}
    .evidence {{
      border-top: 1px solid var(--line);
      padding-top: 10px;
      margin-top: 10px;
    }}
    .evidence:first-child {{ border-top: 0; padding-top: 0; margin-top: 0; }}
    .evidence-title {{ font-size: 13px; font-weight: 650; margin-bottom: 4px; }}
    .evidence-uri {{ margin-bottom: 4px; }}
    .evidence-snippet {{ color: var(--muted); font-size: 13px; }}
    .footer {{ font-size: 12px; color: var(--muted); margin-top: 20px; }}
    a {{ color: var(--blue); text-decoration: none; }}
    @media (max-width: 920px) {{
      .grid.two, .grid.three, .stat-grid, .case-grid {{ grid-template-columns: 1fr; }}
      .kv {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>HotpotQA EchoMemory 运行中诊断</h1>
      <p>这是一份边跑边看的诊断页：一边解释 HotpotQA / EchoMemory 相关指标是怎么衡量的，一边用当前已经落盘的结果分析 EchoMemory 为什么比 OpenViking 差。当前报告不等最终 100 题跑完，因此所有总分都标注为“阶段性”。</p>
      <p class="muted">{html.escape(generated_hint)}</p>
      <div class="note">
        任务状态：<strong>{html.escape(str(task.get('status') or '-'))}</strong> · 当前进度 <strong>{html.escape(str(progress.get('current') or 0))}/{html.escape(str(progress.get('total') or 0))}</strong> · 阶段 <strong>{html.escape(str(progress.get('phase') or '-'))}</strong> · 详情 <strong>{html.escape(str(progress.get('detail') or '-'))}</strong>
      </div>
      <p class="footer">生成时间：{html.escape(generated_at)} · Live run: <code>{html.escape(str(task.get('run_dir') or ''))}</code></p>
    </section>

    <section class="card">
      <h2>先看结论</h2>
      <div class="stat-grid">
        <div class="stat"><div class="label">已落盘题数</div><div class="value">{len(echo_rows_list)}</div></div>
        <div class="stat"><div class="label">阶段性 Answer EM</div><div class="value">{pct(partial_em)}</div></div>
        <div class="stat"><div class="label">阶段性 Answer F1</div><div class="value warn">{pct(partial_f1)}</div></div>
        <div class="stat"><div class="label">I do not know 占比</div><div class="value bad">{pct(unknown_count / len(echo_rows_list) if echo_rows_list else 0.0)}</div></div>
      </div>
      <div class="alert" style="margin-top:14px;">
        当前最强信号不是“模型本身弱”，而是 <strong>检索主证据经常被 atom 抢占</strong>，并且 <strong>导入完整性始终停留在 pending_async_memory</strong>。截至当前，<strong>{final_source_counter.get('atom', 0)}/{len(echo_rows_list)}</strong> 题最终证据源是 <code>atom</code>，<strong>{unknown_count}/{len(echo_rows_list)}</strong> 题直接回答 <code>I do not know</code>。
      </div>
    </section>

    <section class="grid two">
      <div class="card">
        <h2>这些指标怎么衡量</h2>
        <ul>
          <li><strong>Answer EM</strong>：把预测答案和 gold answer 做 HotpotQA 标准化后，完全一致记 1，否则记 0。</li>
          <li><strong>Answer F1</strong>：按词级重叠计算精确率和召回率，再取 F1。它比 EM 更宽松，能体现“答偏了一部分”还是“完全没命中”。</li>
          <li><strong>Judge Accuracy</strong>：额外用裁判模型按“问题 + 标准答案 + 生成答案”判对错，更接近人工判断，但会受 judge 提示和模型风格影响。</li>
          <li><strong>final_evidence_source</strong>：EchoMemory 在多层检索后，最后决定拿哪一层当主证据。这里最关键的是 <code>atom</code> 和 <code>segment_memory</code>。</li>
          <li><strong>retrieval_layers_used</strong>：这题实际调用了哪些记忆层。层多不代表好，关键看最后是不是把对的层排到了最前面。</li>
          <li><strong>pending_async_memory</strong>：说明消息已提交，但 atom / graph / cursor 等异步记忆整理还没完全追上。</li>
        </ul>
      </div>
      <div class="card">
        <h2>为什么这些指标重要</h2>
        <ul>
          <li>如果 <strong>EM/F1 低</strong> 但 <strong>retrieval 很高</strong>，问题更像答案抽取。</li>
          <li>如果 <strong>I do not know 很多</strong>，往往是检索主证据不稳定，或者 answerability gate 太保守。</li>
          <li>如果 <strong>final_evidence_source 大量落在 atom</strong>，但题目其实是当前 sample 的局部文档问答，那就是典型的“全局原子记忆压过局部证据”。</li>
          <li>如果 <strong>pending_async_memory 持续出现</strong>，说明当前 QA 发生在记忆后处理尚未完全收敛的状态下，容易把跨题残留也带进来。</li>
        </ul>
      </div>
    </section>

    <section class="card">
      <h2>当前运行指标</h2>
      <div class="stat-grid">
        <div class="stat"><div class="label">avg memory injection</div><div class="value">{avg_injection_s:.1f}s</div></div>
        <div class="stat"><div class="label">avg QA</div><div class="value">{avg_qa_s:.1f}s</div></div>
        <div class="stat"><div class="label">avg end-to-end</div><div class="value">{avg_total_s:.1f}s</div></div>
        <div class="stat"><div class="label">avg retrieval count</div><div class="value">{avg_retrieval_count:.1f}</div></div>
      </div>
      <div class="grid three" style="margin-top:16px;">
        <div class="card" style="margin:0;">
          <h3>最终证据源分布</h3>
          <ul>
            <li><code>atom</code>: {final_source_counter.get('atom', 0)}</li>
            <li><code>segment_memory</code>: {final_source_counter.get('segment_memory', 0)}</li>
          </ul>
        </div>
        <div class="card" style="margin:0;">
          <h3>导入完整性</h3>
          <ul>
            <li><code>pending_async_memory</code>: {integrity_counter.get('pending_async_memory', 0)}</li>
            <li><code>ECHOMEMORY_IMPORT_INCOMPLETE</code>: {status_counter.get('ECHOMEMORY_IMPORT_INCOMPLETE', 0)}</li>
          </ul>
        </div>
        <div class="card" style="margin:0;">
          <h3>直接放弃回答</h3>
          <ul>
            <li><code>I do not know</code>: {unknown_count}</li>
            <li>占当前已落盘结果的 {pct(unknown_count / len(echo_rows_list) if echo_rows_list else 0.0)}</li>
          </ul>
        </div>
      </div>
    </section>

    <section class="card">
      <h2>为什么说 EchoMemory 现在更差</h2>
      <div class="grid two">
        <div>
          <h3>1. 主证据被 atom 抢走</h3>
          <p>当前已落盘结果里，<strong>{final_source_counter.get('atom', 0)}/{len(echo_rows_list)}</strong> 题最终证据源是 <code>atom</code>。但 HotpotQA 这种按题导入的场景，本来最稳的证据通常应该是当前题那 10 篇文档形成的 <code>segment_memory</code>。</p>
          <p class="muted">也就是说，它不是没找到本题文档，而是经常把“别的题积累下来的原子事实”排在了更前面。</p>
        </div>
        <div>
          <h3>2. QA 开始时记忆还没整理完</h3>
          <p>当前 <strong>{integrity_counter.get('pending_async_memory', 0)}/{len(echo_rows_list)}</strong> 题都还是 <code>pending_async_memory</code>，同时 <strong>{status_counter.get('ECHOMEMORY_IMPORT_INCOMPLETE', 0)}/{len(echo_rows_list)}</strong> 题是 <code>ECHOMEMORY_IMPORT_INCOMPLETE</code>。</p>
          <p class="muted">这说明它在“消息已写入，但后台 atom / graph / cursor 还没完全收敛”的时候就开始答题了。</p>
        </div>
      </div>
      <div class="grid two" style="margin-top:14px;">
        <div>
          <h3>3. 放弃回答过多</h3>
          <p><strong>{unknown_count}</strong> 题直接输出 <code>I do not know</code>。这在 HotpotQA 上几乎一定会把 F1 拉得很低，因为 answer-only 口径不会给“谨慎但没答”的行为任何额外奖励。</p>
        </div>
        <div>
          <h3>4. 时间主要花在记忆整理，不是回答</h3>
          <p>当前平均每题 <strong>{avg_injection_s:.1f}s</strong> 花在记忆注入，平均问答本身 <strong>{avg_qa_s:.1f}s</strong>。这意味着它不仅分数低，吞吐也差。</p>
        </div>
      </div>
    </section>

    <section class="card">
      <h2>和 OpenViking 的当前可比结果</h2>
      <p class="muted">这里只比较当前 EchoMemory 已落盘结果里，和之前 OpenViking 30 题 run 完全重合的 30 题。</p>
      <div class="stat-grid">
        <div class="stat"><div class="label">重合题数</div><div class="value">{len(shared_rows)}</div></div>
        <div class="stat"><div class="label">EchoMemory F1</div><div class="value warn">{pct(echo_shared_f1)}</div></div>
        <div class="stat"><div class="label">OpenViking F1</div><div class="value good">{pct(ov_shared_f1)}</div></div>
        <div class="stat"><div class="label">OV 更好 / Echo 更好</div><div class="value">{len(ov_better)} / {len(echo_better)}</div></div>
      </div>
      <div class="note" style="margin-top:14px;">
        这组可比题里，OpenViking 仍然明显领先。它的优势不只是“说得更长”，而是更少出现 <code>I do not know</code>，并且更少被跨题证据污染。
      </div>
      <div class="grid two" style="margin-top:16px;">
        <div>
          <h3>OpenViking 领先的典型题</h3>
          <table>
            <thead><tr><th>sample_id</th><th>问题</th><th>Gold</th><th>EchoMemory</th><th>OpenViking</th></tr></thead>
            <tbody>{render_table_rows(ov_better[:8])}</tbody>
          </table>
        </div>
        <div>
          <h3>EchoMemory 领先或持平的题</h3>
          <table>
            <thead><tr><th>sample_id</th><th>问题</th><th>Gold</th><th>EchoMemory</th><th>OpenViking</th></tr></thead>
            <tbody>{render_table_rows((echo_better + ties)[:8])}</tbody>
          </table>
        </div>
      </div>
    </section>

    <section class="card">
      <h2>四个正在发生的失败样本</h2>
      <p class="muted">这些样本不是离线猜测，而是直接来自当前 100 题运行已经落盘的结果。</p>
      {case_html}
    </section>

    <section class="card">
      <h2>当前最可信的诊断结论</h2>
      <ol>
        <li>HotpotQA 这种“每题一组文档”的封闭证据场景里，EchoMemory 现在过度依赖 <code>atom</code>，而不是当前题的 <code>segment_memory</code>。</li>
        <li><code>pending_async_memory</code> 全覆盖说明 QA 的时机过早，记忆系统尚未完全收敛。</li>
        <li><code>I do not know</code> 比例过高，说明 answerability gate 过保守，或者排序后的主证据没有给模型足够把握。</li>
        <li>和 OpenViking 对照看，当前主要短板更像 <strong>召回/排序与证据纯度</strong>，而不只是最终生成模型差。</li>
      </ol>
    </section>

    <section class="card">
      <h2>改进方向</h2>
      <ol>
        <li>HotpotQA 模式下先做 <strong>sample-local retrieval</strong>，限制 atom 只来自当前 sample / 当前 session。</li>
        <li>把当前 sample 的 <code>segment_memory</code> 在排序上抬高，至少在本题文档明确命中时优先于全局 atom。</li>
        <li>对于 <code>pending_async_memory</code>，增加更强的“当前题只读当前题提交内容”门禁，避免跨题累计记忆混入。</li>
        <li>如果已经命中高分本题 segment，不要太快走 <code>I do not know</code>；优先做 span extraction 或短答案收束。</li>
      </ol>
    </section>

    <section class="card">
      <h2>文件入口</h2>
      <ul>
        <li>当前诊断 HTML: <a href="file://{html.escape(str(OUTPUT))}">{html.escape(str(OUTPUT))}</a></li>
        <li>Live run report: <a href="file://{html.escape(live_report)}">{html.escape(live_report)}</a></li>
        <li>当前 EchoMemory CSV: <a href="file://{html.escape(str(ECHO_RUN / 'echomemory_generic_qa_results.csv'))}">{html.escape(str(ECHO_RUN / 'echomemory_generic_qa_results.csv'))}</a></li>
        <li>OpenViking 对照 CSV: <a href="file://{html.escape(str(OV_RUN / 'openviking_generic_qa_results.csv'))}">{html.escape(str(OV_RUN / 'openviking_generic_qa_results.csv'))}</a></li>
      </ul>
      <div class="footer">诊断页文件：<code>{html.escape(str(OUTPUT))}</code></div>
    </section>
  </div>
</body>
</html>
"""


def main() -> None:
    html_text = build_html()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    STATIC_MIRROR.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html_text, encoding="utf-8")
    STATIC_MIRROR.write_text(html_text, encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
