#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web")
RUNS = ROOT / "runs"
OUTPUT = ROOT / "web/static/generated-reports/hotpotqa_openviking_vs_echomemory_deepseek30_20260622.html"

ECHO_RUN = RUNS / "echomemory_generic_qa_20260622_150608_92282f" / "echomemory_generic_qa"
OV_RUN = RUNS / "openviking_generic_qa_20260622_155611_b1b37f" / "openviking_generic_qa"

CASE_SAMPLES = [
    "5a8e3ea95542995a26add48d",  # Big Stone Gap
    "5a85ea095542994775f606a8",  # Animorphs
    "5a8c7595554299585d9e36b6",  # Chief of Protocol
    "5a8a3e745542996c9b8d5e70",  # Arena of Khazan
]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open() as handle:
        return list(csv.DictReader(handle))


def pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.2f}%"


def short_uri(uri: str) -> str:
    if not uri:
        return ""
    if len(uri) <= 120:
        return uri
    return f"{uri[:58]}...{uri[-52:]}"


def short_text(text: str, limit: int = 220) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else f"{text[:limit - 1]}..."


def parse_memories(value: str) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        data = json.loads(value)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def parse_layers(value: str) -> list[str]:
    if not value:
        return []
    try:
        data = json.loads(value)
        return [str(item) for item in data] if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def infer_title_from_uri(uri: str) -> str:
    marker = "document_"
    if marker not in uri:
        return ""
    tail = uri.split(marker, 1)[-1]
    if ".md" in tail:
        tail = tail.split(".md", 1)[0]
    if "_" in tail:
        tail = tail.split("_", 1)[-1]
    return tail.replace("-", " ")


def evidence_lines(row: dict[str, str], limit: int = 4) -> list[dict[str, str]]:
    lines: list[dict[str, str]] = []
    for mem in parse_memories(row.get("relevant_memory", ""))[:limit]:
        uri = str(mem.get("uri") or "")
        content = str(mem.get("content") or "")
        mem_type = str(mem.get("memory_type") or mem.get("source") or "")
        score = mem.get("score")
        title = infer_title_from_uri(uri)
        label = title or mem_type or uri
        lines.append(
            {
                "label": label,
                "uri": short_uri(uri),
                "score": "-" if score is None else f"{float(score):.3f}",
                "snippet": short_text(content, 240),
            }
        )
    return lines


def sample_overlap(
    echo_rows: dict[str, dict[str, str]],
    ov_rows: dict[str, dict[str, str]],
) -> tuple[dict[str, int], dict[str, list[dict[str, str]]]]:
    summary = {"both_correct": 0, "both_wrong": 0, "ov_only_correct": 0, "echo_only_correct": 0}
    examples = {key: [] for key in summary}
    for sample_id in sorted(set(echo_rows) & set(ov_rows)):
        echo_row = echo_rows[sample_id]
        ov_row = ov_rows[sample_id]
        echo_ok = echo_row.get("result") == "CORRECT"
        ov_ok = ov_row.get("result") == "CORRECT"
        if echo_ok and ov_ok:
            bucket = "both_correct"
        elif not echo_ok and not ov_ok:
            bucket = "both_wrong"
        elif ov_ok:
            bucket = "ov_only_correct"
        else:
            bucket = "echo_only_correct"
        summary[bucket] += 1
        if len(examples[bucket]) < 8:
            examples[bucket].append(
                {
                    "sample_id": sample_id,
                    "question": echo_row["question"],
                    "gold": echo_row["answer"],
                    "echo": echo_row["response"],
                    "ov": ov_row["response"],
                }
            )
    return summary, examples


def render_table_rows(rows: list[dict[str, str]]) -> str:
    body = []
    for row in rows:
        body.append(
            "<tr>"
            f"<td><code>{html.escape(row['sample_id'])}</code></td>"
            f"<td>{html.escape(row['question'])}</td>"
            f"<td>{html.escape(row['gold'])}</td>"
            f"<td>{html.escape(row['echo'])}</td>"
            f"<td>{html.escape(row['ov'])}</td>"
            "</tr>"
        )
    return "\n".join(body)


def render_evidence_list(items: list[dict[str, str]]) -> str:
    if not items:
        return "<div class='quote'>无</div>"
    parts = ["<div class='evidence-list'>"]
    for item in items:
        parts.append(
            "<div class='evidence'>"
            f"<div class='evidence-title'>{html.escape(item['label'])} <span class='muted'>score {html.escape(item['score'])}</span></div>"
            f"<div class='evidence-uri'><code>{html.escape(item['uri'])}</code></div>"
            f"<div class='evidence-snippet'>{html.escape(item['snippet'])}</div>"
            "</div>"
        )
    parts.append("</div>")
    return "\n".join(parts)


def run_link(path: Path) -> str:
    return f"file://{path}"


def build_html() -> str:
    echo_manifest = load_json(ECHO_RUN.parent / "manifest.json")
    echo_summary = load_json(ECHO_RUN / "summary.json")
    echo_official = load_json(ECHO_RUN / "hotpotqa_answer_summary.json")
    echo_judge = load_json(ECHO_RUN / "judge_summary.json")
    echo_rows_list = load_csv(ECHO_RUN / "echomemory_generic_qa_results.csv")
    echo_rows = {row["sample_id"]: row for row in echo_rows_list}

    ov_manifest = load_json(OV_RUN.parent / "manifest.json")
    ov_summary = load_json(OV_RUN / "summary.json")
    ov_official = load_json(OV_RUN / "hotpotqa_answer_summary.json")
    ov_judge = load_json(OV_RUN / "judge_summary.json")
    ov_rows_list = load_csv(OV_RUN / "openviking_generic_qa_results.csv")
    ov_rows = {row["sample_id"]: row for row in ov_rows_list}

    overlap_summary, overlap_examples = sample_overlap(echo_rows, ov_rows)

    echo_final_counter = Counter((row.get("final_evidence_source") or "none") for row in echo_rows_list)
    echo_unknown_count = sum("i do not know" in (row.get("response") or "").lower() for row in echo_rows_list)
    echo_atom_wrong = [
        row for row in echo_rows_list if row.get("final_evidence_source") == "atom" and row.get("result") != "CORRECT"
    ]
    ov_unknown_count = sum(
        (row.get("response") or "").strip().lower() in {"unknown", "i do not know.", "i do not know"}
        for row in ov_rows_list
    )
    ov_uri_types = Counter()
    for row in ov_rows_list:
        for mem in parse_memories(row.get("relevant_memory", ""))[:3]:
            uri = str(mem.get("uri") or "")
            if "document_" in uri:
                ov_uri_types["document"] += 1
            elif "overview" in uri:
                ov_uri_types["overview"] += 1
            else:
                ov_uri_types["other"] += 1

    case_blocks = []
    for sample_id in CASE_SAMPLES:
        echo_row = echo_rows[sample_id]
        ov_row = ov_rows[sample_id]
        recall_path = ECHO_RUN / f"q{int(echo_row['question_index'] or 0):03d}.recall.json"
        if not recall_path.exists():
            recall_path = None
        echo_recall = load_json(recall_path) if recall_path else {}
        case_blocks.append(
            {
                "sample_id": sample_id,
                "question": echo_row["question"],
                "gold": echo_row["answer"],
                "echo_result": echo_row["result"],
                "echo_response": echo_row["response"],
                "echo_final_source": echo_row.get("final_evidence_source") or "none",
                "echo_layers": ", ".join(parse_layers(echo_row.get("retrieval_layers_used", ""))) or "-",
                "echo_import": f"{echo_row.get('import_status') or '-'} / {echo_row.get('import_integrity') or '-'}",
                "echo_timing": f"injection {echo_row.get('memory_injection_time_s') or '-'} s, qa {echo_row.get('qa_time_s') or '-'} s",
                "echo_selected_count": echo_recall.get("selected_count", "-"),
                "echo_evidence": evidence_lines(echo_row),
                "ov_result": ov_row["result"],
                "ov_response": ov_row["response"],
                "ov_import": f"{ov_row.get('import_status') or '-'} / {ov_row.get('import_integrity') or '-'}",
                "ov_retrieval_count": ov_row.get("retrieval_count") or "-",
                "ov_memory_hit_count": ov_row.get("memory_hit_count") or "-",
                "ov_evidence": evidence_lines(ov_row),
            }
        )

    case_html_parts = []
    for case in case_blocks:
        case_html_parts.append(
            f"""
      <div class="case">
        <h3>{html.escape(case['question'])}</h3>
        <p><span class="muted">sample_id</span> <code>{html.escape(case['sample_id'])}</code> · <span class="muted">Gold</span> <strong>{html.escape(case['gold'])}</strong></p>
        <div class="case-grid">
          <div class="subcard">
            <h3>EchoMemory</h3>
            <div class="kv">
              <div class="k">结果</div><div>{html.escape(case['echo_result'])}</div>
              <div class="k">回答</div><div>{html.escape(case['echo_response'])}</div>
              <div class="k">最终证据源</div><div><code>{html.escape(case['echo_final_source'])}</code></div>
              <div class="k">检索层</div><div>{html.escape(case['echo_layers'])}</div>
              <div class="k">导入状态</div><div>{html.escape(case['echo_import'])}</div>
              <div class="k">耗时</div><div>{html.escape(case['echo_timing'])}</div>
              <div class="k">selected_count</div><div>{html.escape(str(case['echo_selected_count']))}</div>
            </div>
            {render_evidence_list(case['echo_evidence'])}
          </div>
          <div class="subcard">
            <h3>OpenViking</h3>
            <div class="kv">
              <div class="k">结果</div><div>{html.escape(case['ov_result'])}</div>
              <div class="k">回答</div><div>{html.escape(case['ov_response'])}</div>
              <div class="k">导入状态</div><div>{html.escape(case['ov_import'])}</div>
              <div class="k">retrieval_count</div><div>{html.escape(case['ov_retrieval_count'])}</div>
              <div class="k">memory_hit_count</div><div>{html.escape(case['ov_memory_hit_count'])}</div>
            </div>
            {render_evidence_list(case['ov_evidence'])}
          </div>
        </div>
      </div>
            """
        )
    case_html = "".join(case_html_parts)

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ov_duration_s = ov_manifest.get("duration_s")
    ov_duration_text = "-" if ov_duration_s is None else f"{float(ov_duration_s):.1f}s"
    echo_total_duration_s = echo_summary.get("total_end_to_end_time_s") or echo_manifest.get("duration_s")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>HotpotQA 对比：OpenViking vs EchoMemory</title>
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
      max-width: 1180px;
      margin: 0 auto;
      padding: 24px 18px 60px;
    }}
    .hero, .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }}
    .hero {{
      padding: 28px 24px;
      margin-bottom: 18px;
    }}
    .card {{
      padding: 20px 18px;
      margin-bottom: 16px;
    }}
    h1, h2, h3 {{ margin: 0; letter-spacing: 0; }}
    h1 {{ font-size: 30px; line-height: 1.2; margin-bottom: 10px; }}
    h2 {{ font-size: 22px; line-height: 1.25; margin-bottom: 14px; }}
    h3 {{ font-size: 17px; line-height: 1.35; margin-bottom: 10px; }}
    p {{ margin: 0 0 12px; }}
    .muted {{ color: var(--muted); }}
    .meta {{ font-size: 13px; color: var(--muted); }}
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
    .tag-row {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }}
    .tag {{
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #fafbfd;
      color: var(--muted);
      padding: 5px 10px;
      font-size: 12px;
    }}
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
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      text-align: left;
      vertical-align: top;
      border-top: 1px solid var(--line);
      padding: 10px 8px;
    }}
    th {{
      color: var(--muted);
      background: #fbfbfc;
      font-weight: 650;
    }}
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
      grid-template-columns: 138px 1fr;
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
    .evidence:first-child {{
      border-top: 0;
      padding-top: 0;
      margin-top: 0;
    }}
    .evidence-title {{
      font-size: 13px;
      font-weight: 650;
      margin-bottom: 4px;
    }}
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
      <h1>HotpotQA 对比：OpenViking vs EchoMemory</h1>
      <p>同一批 30 题、同一 answer/judge 模型 <code>deepseek-v4-flash</code>，对比两套记忆系统在 HotpotQA dev distractor 上的表现。报告重点不是只看总分，而是拆开看：记忆如何注入、检索命中了什么、为什么会答成 <code>I do not know</code>，以及哪些失分来自检索，哪些来自答案提取。</p>
      <div class="tag-row">
        <span class="tag">数据集: HotpotQA dev distractor</span>
        <span class="tag">题量: 30</span>
        <span class="tag">模型: deepseek-v4-flash</span>
        <span class="tag">生成时间: {html.escape(generated_at)}</span>
      </div>
    </section>

    <section class="card">
      <h2>结论先看</h2>
      <div class="stat-grid">
        <div class="stat">
          <div class="label">OpenViking 官方 Answer F1</div>
          <div class="value good">{pct(ov_official["answer_f1"])}</div>
        </div>
        <div class="stat">
          <div class="label">EchoMemory 官方 Answer F1</div>
          <div class="value">{pct(echo_official["answer_f1"])}</div>
        </div>
        <div class="stat">
          <div class="label">OpenViking Judge Accuracy</div>
          <div class="value good">{pct(ov_judge["accuracy"])}</div>
        </div>
        <div class="stat">
          <div class="label">EchoMemory Judge Accuracy</div>
          <div class="value">{pct(echo_judge["accuracy"])}</div>
        </div>
      </div>
      <div class="note" style="margin-top:14px;">
        这组同模型小样本里，OpenViking 明显更强：<strong>官方 answer-only F1 为 52.63%</strong>，EchoMemory 为 <strong>35.88%</strong>。两者差距不主要在模型本身，而在记忆注入与检索链路的稳定性和证据纯度。
      </div>
    </section>

    <section class="grid two">
      <div class="card">
        <h2>OpenViking 概况</h2>
        <div class="stat-grid">
          <div class="stat"><div class="label">Answer EM</div><div class="value">{pct(ov_official["answer_em"])}</div></div>
          <div class="stat"><div class="label">Answer F1</div><div class="value good">{pct(ov_official["answer_f1"])}</div></div>
          <div class="stat"><div class="label">Judge Correct</div><div class="value">{ov_judge["correct"]}/{ov_judge["count"]}</div></div>
          <div class="stat"><div class="label">总耗时</div><div class="value">{ov_duration_text}</div></div>
        </div>
        <ul style="margin-top:14px;">
          <li>检索健康度稳定，<code>retrieval_status ok = 30</code>，没有 API 错误。</li>
          <li>Top-3 检索项里几乎全是当前样本导入的 <code>document_*.md</code>，我们统计到的类型计数是 <code>{dict(ov_uri_types)}</code>。</li>
          <li>仍有 <code>{ov_unknown_count}</code> 题回答成 <code>unknown</code>，说明剩余问题更多是答案提取或桥接推理没完成，而不是完全没召回。</li>
        </ul>
      </div>
      <div class="card">
        <h2>EchoMemory 概况</h2>
        <div class="stat-grid">
          <div class="stat"><div class="label">Answer EM</div><div class="value">{pct(echo_official["answer_em"])}</div></div>
          <div class="stat"><div class="label">Answer F1</div><div class="value warn">{pct(echo_official["answer_f1"])}</div></div>
          <div class="stat"><div class="label">Judge Correct</div><div class="value">{echo_judge["correct"]}/{echo_judge["count"]}</div></div>
          <div class="stat"><div class="label">平均总耗时</div><div class="value">{float(echo_summary["avg_end_to_end_time_s"]):.1f}s</div></div>
        </div>
        <ul style="margin-top:14px;">
          <li>平均每题 <code>memory_injection_time</code> 就有 <code>{echo_summary["avg_memory_injection_time_s"]:.1f}s</code>，远高于问答本身。</li>
          <li>30 题累计端到端耗时约 <code>{float(echo_total_duration_s):.1f}s</code>，明显高于 OpenViking 的 <code>{ov_duration_text}</code>。</li>
          <li>{echo_final_counter['atom']}/30 题最终证据源落在 <code>atom</code>，只有 {echo_final_counter['segment_memory']}/30 题最终落在 <code>segment_memory</code>。</li>
          <li>共有 <code>{echo_unknown_count}</code> 题直接回答 <code>I do not know</code>；其中 <code>{len(echo_atom_wrong)}</code> 题同时满足“最终证据源是 atom 且最终答错”。</li>
        </ul>
      </div>
    </section>

    <section class="card">
      <h2>为什么 OpenViking 更高</h2>
      <div class="grid two">
        <div>
          <h3>1. 注入对象更干净</h3>
          <p>OpenViking 这轮 HotpotQA 基本是把每题的 10 篇文档当作当前样本的 benchmark source memory，检索命中的 URI 也稳定落在当前样本目录下的 <code>document_*.md</code>。它更像“当前题局部文档检索”。</p>
          <p class="muted">这能显著降低别题证据混进来的概率。</p>
        </div>
        <div>
          <h3>2. EchoMemory 的 atom 过强且可能跨题污染</h3>
          <p>EchoMemory 这轮里，很多题虽然当前样本的 segment memory 已经检索出来，但排在更前面的却是无关 atom。比如 <code>Big Stone Gap</code> 那题，top-2 atom 分别是 <code>Ed Wood (film)</code> 和 <code>Meet Corliss Archer</code>，都不是当前问题的目标证据。</p>
          <p class="muted">这更像是共享账户/workspace 下的全局 atom 被高权重召回，盖过了当前样本局部证据。</p>
        </div>
      </div>
      <div class="grid two" style="margin-top:14px;">
        <div>
          <h3>3. OpenViking 剩余失分更像“答案没抽出来”</h3>
          <p>OpenViking 也有失误，但它常常不是没找对文档，而是找到了文档却仍答成 <code>unknown</code>。这类问题属于答案抽取/桥接收束问题，通常比“证据被错题 atom 抢占”更容易修。</p>
        </div>
        <div>
          <h3>4. EchoMemory 的耗时主要耗在记忆整理，不是回答</h3>
          <p>EchoMemory 平均每题注入耗时 <code>{echo_summary["avg_memory_injection_time_s"]:.1f}s</code>，问答耗时 <code>{echo_summary["avg_qa_time_s"]:.1f}s</code>。这意味着它不仅分数更低，单位题目的吞吐也更差。</p>
        </div>
      </div>
    </section>

    <section class="card">
      <h2>样本级胜负结构</h2>
      <div class="stat-grid">
        <div class="stat"><div class="label">两边都对</div><div class="value">{overlap_summary['both_correct']}</div></div>
        <div class="stat"><div class="label">OpenViking 独赢</div><div class="value good">{overlap_summary['ov_only_correct']}</div></div>
        <div class="stat"><div class="label">EchoMemory 独赢</div><div class="value warn">{overlap_summary['echo_only_correct']}</div></div>
        <div class="stat"><div class="label">两边都错</div><div class="value">{overlap_summary['both_wrong']}</div></div>
      </div>
      <div class="grid two" style="margin-top:16px;">
        <div>
          <h3>OpenViking 独赢的典型题</h3>
          <table>
            <thead><tr><th>sample_id</th><th>问题</th><th>Gold</th><th>EchoMemory</th><th>OpenViking</th></tr></thead>
            <tbody>{render_table_rows(overlap_examples['ov_only_correct'][:8])}</tbody>
          </table>
        </div>
        <div>
          <h3>两边都错的题</h3>
          <table>
            <thead><tr><th>sample_id</th><th>问题</th><th>Gold</th><th>EchoMemory</th><th>OpenViking</th></tr></thead>
            <tbody>{render_table_rows(overlap_examples['both_wrong'][:5])}</tbody>
          </table>
        </div>
      </div>
      <p class="section-note">结构上最刺眼的是：OpenViking 独赢 12 题，EchoMemory 只独赢 1 题。也就是说，这不是一两道偶然题的波动，而是系统性优势。</p>
    </section>

    <section class="card">
      <h2>关键机理差异</h2>
      <div class="grid three">
        <div class="card" style="margin:0;">
          <h3>EchoMemory 证据源</h3>
          <ul>
            <li><code>final_evidence_source</code> 分布：{html.escape(str(dict(echo_final_counter)))}</li>
            <li>26/30 题最终落在 atom。</li>
            <li>14 道 atom-final 题最终答错。</li>
          </ul>
        </div>
        <div class="card" style="margin:0;">
          <h3>OpenViking 证据源</h3>
          <ul>
            <li>Top 检索结果基本都是当前样本 <code>document_*.md</code>。</li>
            <li>没有出现跨题 session 段或无关原子事实盖住当前题主证据的现象。</li>
            <li>更像“按题导入、按题检索”的封闭证据池。</li>
          </ul>
        </div>
        <div class="card" style="margin:0;">
          <h3>失败模式</h3>
          <ul>
            <li>EchoMemory：更常见 <code>I do not know</code> 或被无关 atom 带偏。</li>
            <li>OpenViking：少数题是检索已接近正确文档，但最终答案仍 <code>unknown</code>。</li>
            <li>前者偏召回/排序问题，后者偏答案抽取问题。</li>
          </ul>
        </div>
      </div>
    </section>

    <section class="card">
      <h2>四道典型题</h2>
      <p class="muted">下面这四题覆盖了最关键的几种差异：OpenViking 独赢、EchoMemory 独赢，以及“已召回但没答出来”的残余失败。</p>
      {case_html}
    </section>

    <section class="card">
      <h2>具体原因分析</h2>
      <div class="grid two">
        <div>
          <h3>EchoMemory 的主要失分原因</h3>
          <ul>
            <li><strong>atom 优先级过高</strong>。本轮 26/30 题最终证据源是 atom，而 atom 中存在明显的跨题内容，例如 <code>Big Stone Gap</code> 题 top-2 竟然是 <code>Ed Wood (film)</code> 和 <code>Meet Corliss Archer</code>。</li>
            <li><strong>当前样本 segment 已出现，但没有成为最终主证据</strong>。这导致模型即便“看到了对的段落”，也可能被前置的无关 atom 带偏，最终转成 <code>I do not know</code> 或抽错实体。</li>
            <li><strong>异步导入语义不够稳</strong>。CSV 里大量题目的 <code>import_status=ECHOMEMORY_IMPORT_INCOMPLETE</code>、<code>import_integrity=pending_async_memory</code>，说明它的评测使用了“消息已提交，但后台还在整理”的状态。</li>
            <li><strong>桥接题最受伤</strong>。OpenViking 独赢的 12 题里，大部分都是桥接题，需要把两个文档线索准确连起来。</li>
          </ul>
        </div>
        <div>
          <h3>OpenViking 的主要失分原因</h3>
          <ul>
            <li><strong>少数题是答案提取失败，不是召回失败</strong>。例如 <code>Tunnels and Trolls</code> 这题，它实际上已经把 <code>Tunnels &amp; Trolls</code>、<code>Ken St. Andre</code>、<code>Arena of Khazan</code> 都召回到了 top-5，但最终还是回答 <code>unknown</code>。</li>
            <li><strong>比较题偶尔会被表面共现误导</strong>。例如 <code>Random House Tower</code> 那题，OpenViking 直接答成了 <code>Yes</code>，更像是对“两个对象都和地产有关系”的浅层归纳，没有抓住 gold 要求的精确定义。</li>
            <li><strong>当前导入形式偏 document memory</strong>。这很稳，但也意味着它更依赖回答模型自己在 document 之间做桥接；如果桥接抽取提示不够强，就会出现“文档在，答案没出来”。</li>
          </ul>
        </div>
      </div>
    </section>

    <section class="card">
      <h2>对 EchoMemory 的改进建议</h2>
      <ol>
        <li>在 HotpotQA 这种按题隔离的评测里，先限制 atom 检索只来自当前 sample/session，至少做一次 sample 级过滤再排序。</li>
        <li>把当前 sample 的 segment memory 设成更高优先级，避免全局 atom 在初轮排序中压过本题文档段落。</li>
        <li>对 <code>pending_async_memory</code> 的状态增加更强的“当前题只读当前题提交内容”保障，不要让前面题的全局累计状态参与当前题的主证据决策。</li>
        <li>对 <code>I do not know</code> 进行 answerability gate 收紧：如果已命中当前 sample 的高分段落，就优先做 span extraction，不要过早放弃。</li>
        <li>参考 OpenViking 的做法，在 HotpotQA 模式下提供一个“document-only / sample-local retrieval”开关，专门屏蔽跨题全局记忆。</li>
      </ol>
    </section>

    <section class="card">
      <h2>产物与证据路径</h2>
      <ul>
        <li>EchoMemory run: <a href="{run_link(ECHO_RUN.parent / 'report.html')}">{html.escape(str(ECHO_RUN.parent / 'report.html'))}</a></li>
        <li>OpenViking run: <a href="{run_link(OV_RUN.parent / 'report.html')}">{html.escape(str(OV_RUN.parent / 'report.html'))}</a></li>
        <li>EchoMemory CSV: <a href="{run_link(ECHO_RUN / 'echomemory_generic_qa_results.csv')}">{html.escape(str(ECHO_RUN / 'echomemory_generic_qa_results.csv'))}</a></li>
        <li>OpenViking CSV: <a href="{run_link(OV_RUN / 'openviking_generic_qa_results.csv')}">{html.escape(str(OV_RUN / 'openviking_generic_qa_results.csv'))}</a></li>
        <li>EchoMemory 官方汇总: <a href="{run_link(ECHO_RUN / 'hotpotqa_answer_summary.json')}">{html.escape(str(ECHO_RUN / 'hotpotqa_answer_summary.json'))}</a></li>
        <li>OpenViking 官方汇总: <a href="{run_link(OV_RUN / 'hotpotqa_answer_summary.json')}">{html.escape(str(OV_RUN / 'hotpotqa_answer_summary.json'))}</a></li>
      </ul>
      <div class="footer">报告文件: <code>{html.escape(str(OUTPUT))}</code></div>
    </section>
  </div>
</body>
</html>
"""


def main() -> None:
    OUTPUT.write_text(build_html())
    print(OUTPUT)


if __name__ == "__main__":
    main()
