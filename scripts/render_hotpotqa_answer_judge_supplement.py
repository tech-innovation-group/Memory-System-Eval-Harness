#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web")
DEFAULT_DATASET = ROOT / "dataset/full/hotpotqa_dev_distractor.json"
WEB_REPORT_DIR = ROOT / "web/static/generated-reports"
STATIC_REPORT_DIR = ROOT / "static/generated-reports"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def maybe_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = load_json(path)
    return data if isinstance(data, dict) else {}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            data = json.loads(text)
            if isinstance(data, dict):
                rows.append(data)
    return rows


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="", errors="replace") as handle:
        return list(csv.DictReader(handle))


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def pct(value: Any) -> str:
    try:
        number = float(value)
    except Exception:
        return "-"
    return f"{number * 100:.2f}%"


def compact(text: Any, limit: int = 220) -> str:
    plain = " ".join(str(text or "").split())
    if len(plain) <= limit:
        return plain
    return plain[: limit - 1] + "..."


def sanitize_prediction_text(text: Any) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    value = value.replace("\r", "\n")
    for pattern in (
        r"<\|?DSML\|?[\s\S]*$",
        r"<｜DSML｜[\s\S]*$",
        r"<memory_search[\s\S]*$",
        r"<functioncall[\s\S]*$",
        r"<function[\s\S]*$",
        r"<invoke[\s\S]*$",
        r"<execute[\s\S]*$",
    ):
        value = re.sub(pattern, "", value, flags=re.I)
    value = re.sub(r"`{3}[\s\S]*?`{3}", "", value)
    value = re.sub(r"`[^`]*`", "", value)
    value = re.sub(r"\*\*(.*?)\*\*", r"\1", value)
    value = re.sub(r"\s+", " ", value).strip()
    lead_patterns = (
        r"^(based on (?:the )?(?:available|retrieved) memor(?:y|ies)[^.!?]*[.!?]\s*)",
        r"^(based on my (?:knowledge|memory)[^.!?]*[.!?]\s*)",
        r"^(i(?:'| a)?ll check memory[^.!?]*[.!?]\s*)",
        r"^(i(?:'| a)?ll check [^.!?]*[.!?]\s*)",
        r"^(i will check [^.!?]*[.!?]\s*)",
        r"^(i(?:'| a)?ll search[^.!?]*[.!?]\s*)",
        r"^(i will search[^.!?]*[.!?]\s*)",
        r"^(let me check[^.!?]*[.!?]\s*)",
        r"^(let me search[^.!?]*[.!?]\s*)",
        r"^(let me retrieve[^.!?]*[.!?]\s*)",
        r"^(let me look[^.!?]*[.!?]\s*)",
        r"^(searching for[^.!?]*[.!?]\s*)",
    )
    changed = True
    while changed and value:
        changed = False
        for pattern in lead_patterns:
            updated = re.sub(pattern, "", value, flags=re.I).strip()
            if updated != value:
                value = updated
                changed = True
    for phrase in (
        "让我搜索一下。",
        "让我搜索一下",
        "我来搜索一下。",
        "我来搜索一下",
        "让我查一下。",
        "让我查一下",
        "根据记忆中的信息，",
        "基于记忆中的信息，",
    ):
        value = value.replace(phrase, "").strip()
    value = re.sub(
        r"\bto (?:find|answer|confirm|check|verify)[^.!?]*(?:let me|i(?:'| a)?ll|i will)\s+(?:search|retrieve|look up|check)[^.!?]*[.!?]?",
        "",
        value,
        flags=re.I,
    ).strip()
    value = re.sub(
        r"\bi (?:know|found) from the retrieved memories that\s+",
        "",
        value,
        flags=re.I,
    ).strip()
    filtered_sentences = []
    for sentence in re.split(r"(?<=[.!?。！？])\s+", value):
        piece = sentence.strip()
        if not piece:
            continue
        lowered = piece.lower()
        if (
            re.search(r"\b(let me|i(?:'| a)?ll|i will)\s+(?:search|retrieve|look up|check)\b", lowered)
            or "search my memory" in lowered
            or "check memory" in lowered
            or "retrieved memories" in lowered
            or re.search(r"(让我|我来|我会).*(搜索|查询|检索|查一下)", piece)
            or re.search(r"(需要|还需|仍需).*(查询|搜索|检索|确认)", piece)
        ):
            continue
        filtered_sentences.append(piece)
    value = " ".join(filtered_sentences).strip()
    tail_patterns = (
        r"(?:however, )?(?:the )?retrieved memor(?:y|ies) do(?:es)? not [^.!?]*[.!?]?$",
        r"(?:therefore, )?i cannot confirm[^.!?]*[.!?]?$",
        r"(?:to be thorough, )?let me verify[^.!?]*[.!?]?$",
        r"(?:i )?need to (?:search|retrieve|look up|check)[^.!?]*[.!?]?$",
        r"(?:it )?requires? (?:search|retrieval|looking up)[^.!?]*[.!?]?$",
        r"(?:about|for) [^.!?]* need(?:s)? further (?:search|lookup|retrieval)[^.!?]*[.!?]?$",
        r"(?:关于|对于)[^。！？]*?(?:需要|还需|仍需)(?:进一步)?(?:查询|搜索|检索|确认)[^。！？]*[。！？]?$",
        r"(?:让我|我来)(?:继续)?(?:搜索|查询|检索|查一下)[^。！？]*[。！？]?$",
        r"(?:还需要|仍需要)(?:进一步)?(?:查询|搜索|检索|确认)[^。！？]*[。！？]?$",
    )
    changed = True
    while changed and value:
        changed = False
        for pattern in tail_patterns:
            updated = re.sub(pattern, "", value, flags=re.I).strip()
            if updated != value:
                value = updated
                changed = True
    value = re.sub(r"\b(?:need(?:s)?|requires?) to (?:search|retrieve|look up)[^.!?]*[.!?]?$", "", value, flags=re.I).strip()
    value = re.sub(r"\b(?:let me|i(?:'| a)?ll|i will) (?:search|retrieve|look up|check)[^.!?]*$", "", value, flags=re.I).strip()
    value = re.sub(r"(?:to find [^.!?]*, )?let me search[^.!?]*[.!?]?$", "", value, flags=re.I).strip()
    value = re.sub(r"(?:to answer [^.!?]*, )?i(?:'| a)?ll check memory[^.!?]*[.!?]?$", "", value, flags=re.I).strip()
    value = re.sub(r"\s+", " ", value).strip(" -:\n\t")
    return value


def parse_relevant_memory(raw: Any) -> list[dict[str, Any]]:
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "")
        title = ""
        title_match = re.search(r"(?:^|\n)\s*title:\s*(.+)", content, flags=re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()
        elif content.startswith("# "):
            title = content.splitlines()[0].replace("#", "", 1).strip()
        rows.append(
            {
                "uri": str(item.get("uri") or item.get("path") or ""),
                "score": item.get("score"),
                "memory_type": str(item.get("memory_type") or ""),
                "title": title,
                "snippet": compact(content, 180),
            }
        )
    return rows


def detect_csv(run_dir: Path) -> Path:
    candidates = [
        run_dir / "echomemory_hotpotqa_generic_qa_results.csv",
        run_dir / "echomemory_generic_qa_results.csv",
        run_dir / "openviking_hotpotqa_generic_qa_results.csv",
        run_dir / "openviking_generic_qa_results.csv",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    matches = sorted(run_dir.glob("*generic_qa_results*.csv"))
    if matches:
        return matches[0]
    raise SystemExit(f"no HotpotQA results CSV found in {run_dir}")


def answer_label(em: Any, f1: Any) -> str:
    try:
        em_value = float(em)
        f1_value = float(f1)
    except Exception:
        return "UNSCORED"
    if em_value >= 1.0:
        return "CORRECT"
    if f1_value > 0.0:
        return "PARTIAL"
    return "WRONG"


def run_answer_eval(csv_path: Path, dataset_path: Path, out_dir: Path) -> dict[str, Any]:
    script = ROOT / "scripts/hotpotqa_answer_eval.py"
    cmd = [
        sys.executable,
        str(script),
        "--csv",
        str(csv_path),
        "--reference",
        str(dataset_path),
        "--out-dir",
        str(out_dir),
        "--prediction-field",
        "response",
    ]
    subprocess.run(cmd, check=True, cwd=str(ROOT))
    summary_path = out_dir / "hotpotqa_answer_summary.json"
    if not summary_path.exists():
        raise SystemExit(f"answer eval did not create {summary_path}")
    return maybe_json(summary_path)


def build_judged_rows(
    source_rows: list[dict[str, str]],
    eval_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    eval_by_qid = {str(row.get("question_id") or ""): row for row in eval_rows}
    judged_rows: list[dict[str, Any]] = []
    for source in source_rows:
        qid = str(source.get("question_id") or source.get("sample_id") or "")
        eval_row = eval_by_qid.get(qid, {})
        em = eval_row.get("answer_em")
        f1 = eval_row.get("answer_f1")
        prediction = sanitize_prediction_text(eval_row.get("prediction") or source.get("response") or "")
        memories = parse_relevant_memory(source.get("relevant_memory"))
        top_memories = memories[:3]
        top_titles = " | ".join(item.get("title") or compact(item.get("uri"), 80) for item in top_memories if item.get("title") or item.get("uri"))
        top_uris = " | ".join(item.get("uri") or "" for item in top_memories if item.get("uri"))
        judged_rows.append(
            {
                "question_id": qid,
                "type": eval_row.get("type") or source.get("category") or "",
                "level": eval_row.get("level") or "",
                "question": eval_row.get("question") or source.get("question") or "",
                "gold_answer": eval_row.get("answer") or source.get("answer") or "",
                "prediction": prediction,
                "answer_eval_label": answer_label(em, f1),
                "answer_em": em,
                "answer_f1": f1,
                "retrieval_status": source.get("retrieval_status") or "",
                "health_status": source.get("health_status") or "",
                "answer_status": source.get("answer_status") or "",
                "retrieval_count": source.get("retrieval_count") or "",
                "memory_hit_count": source.get("memory_hit_count") or "",
                "final_evidence_source": source.get("final_evidence_source") or "",
                "retrieved_memory_count": len(memories),
                "top_memory_titles": top_titles,
                "top_memory_uris": top_uris,
                "response_tokens": source.get("answer_total_tokens") or "",
                "latency_s": source.get("time_cost") or source.get("qa_time_s") or "",
                "reasoning": source.get("reasoning") or "",
                "response_preview": compact(prediction, 300),
            }
        )
    return judged_rows


def build_retrieval_details(
    source_rows: list[dict[str, str]],
    eval_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    eval_by_qid = {str(row.get("question_id") or ""): row for row in eval_rows}
    details: list[dict[str, Any]] = []
    for source in source_rows:
        qid = str(source.get("question_id") or source.get("sample_id") or "")
        eval_row = eval_by_qid.get(qid, {})
        memories = parse_relevant_memory(source.get("relevant_memory"))
        prediction = sanitize_prediction_text(eval_row.get("prediction") or source.get("response") or "")
        details.append(
            {
                "question_id": qid,
                "question": eval_row.get("question") or source.get("question") or "",
                "gold_answer": eval_row.get("answer") or source.get("answer") or "",
                "prediction": prediction,
                "answer_em": eval_row.get("answer_em"),
                "answer_f1": eval_row.get("answer_f1"),
                "answer_eval_label": answer_label(eval_row.get("answer_em"), eval_row.get("answer_f1")),
                "retrieval_status": source.get("retrieval_status") or "",
                "health_status": source.get("health_status") or "",
                "final_evidence_source": source.get("final_evidence_source") or "",
                "retrieval_count": source.get("retrieval_count") or "",
                "memory_hit_count": source.get("memory_hit_count") or "",
                "memories": memories,
            }
        )
    return details


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise SystemExit(f"no judged rows to write into {path}")
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def label_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {"CORRECT": 0, "PARTIAL": 0, "WRONG": 0, "UNSCORED": 0}
    for row in rows:
        label = str(row.get("answer_eval_label") or "UNSCORED")
        counts[label] = counts.get(label, 0) + 1
    return counts


def render_html(
    run_dir: Path,
    source_csv: Path,
    judged_csv: Path,
    summary: dict[str, Any],
    judged_rows: list[dict[str, Any]],
    output_path: Path,
    retrieval_detail_path: Path,
) -> None:
    counts = label_counts(judged_rows)
    graded = int(summary.get("graded") or len(judged_rows))
    answer_em = summary.get("answer_em")
    answer_f1 = summary.get("answer_f1")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows_html = []
    for row in judged_rows:
        label = str(row.get("answer_eval_label") or "")
        row_class = {
            "CORRECT": "good",
            "PARTIAL": "warn",
            "WRONG": "bad",
        }.get(label, "")
        rows_html.append(
            f"""
            <tr class="{row_class}">
              <td>{esc(row.get("question_id"))}</td>
              <td>{esc(row.get("type"))}</td>
              <td>{esc(row.get("level"))}</td>
              <td title="{esc(row.get("question"))}">{esc(compact(row.get("question"), 120))}</td>
              <td title="{esc(row.get("gold_answer"))}">{esc(compact(row.get("gold_answer"), 80))}</td>
              <td title="{esc(row.get("prediction"))}">{esc(compact(row.get("prediction"), 120))}</td>
              <td>{esc(label)}</td>
              <td>{esc(pct(row.get("answer_em")))}</td>
              <td>{esc(pct(row.get("answer_f1")))}</td>
              <td>{esc(row.get("retrieval_status"))}</td>
              <td>{esc(row.get("health_status"))}</td>
              <td>{esc(row.get("retrieval_count"))}</td>
              <td>{esc(row.get("memory_hit_count"))}</td>
            </tr>
            """
        )

    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>HotpotQA Answer Judge Supplement</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f5f0;
      --panel: #ffffff;
      --border: #e5ded2;
      --text: #111827;
      --muted: #6b7280;
      --blue: #2563eb;
      --green: #16a34a;
      --orange: #d97706;
      --red: #dc2626;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
    }}
    .page {{
      max-width: 1440px;
      margin: 0 auto;
      padding: 24px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 28px;
      line-height: 1.2;
    }}
    p {{
      margin: 0;
      color: var(--muted);
      line-height: 1.5;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 16px;
      margin: 20px 0;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
      min-width: 0;
    }}
    .k {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .v {{
      font-size: 24px;
      font-weight: 700;
      line-height: 1.2;
    }}
    .good .v {{ color: var(--green); }}
    .warn .v {{ color: var(--orange); }}
    .bad .v {{ color: var(--red); }}
    .section {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
      margin-bottom: 16px;
    }}
    .meta-list {{
      display: grid;
      grid-template-columns: 180px 1fr;
      gap: 8px 16px;
      margin-top: 12px;
      font-size: 14px;
    }}
    .meta-list div:nth-child(odd) {{
      color: var(--muted);
    }}
    .path {{
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      min-width: 0;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      font-size: 13px;
    }}
    thead th {{
      position: sticky;
      top: 0;
      background: #faf8f4;
      color: var(--muted);
      font-weight: 600;
      text-align: left;
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
    }}
    tbody td {{
      padding: 10px 12px;
      border-bottom: 1px solid #efe7db;
      vertical-align: top;
    }}
    tbody tr.good td:nth-child(7) {{ color: var(--green); font-weight: 600; }}
    tbody tr.warn td:nth-child(7) {{ color: var(--orange); font-weight: 600; }}
    tbody tr.bad td:nth-child(7) {{ color: var(--red); font-weight: 600; }}
    .table-wrap {{
      overflow: auto;
      max-height: 70vh;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--panel);
    }}
    .note {{
      margin-top: 12px;
      font-size: 13px;
      color: var(--muted);
    }}
    @media (max-width: 1200px) {{
      .summary-grid {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    }}
    @media (max-width: 760px) {{
      .page {{ padding: 16px; }}
      .summary-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .meta-list {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <h1>HotpotQA Answer Judge Supplement</h1>
    <p>This supplement recomputes per-question HotpotQA answer-only EM/F1 from the current run CSV. It is not a supporting-facts or joint HotpotQA judge.</p>

    <div class="summary-grid">
      <div class="card">
        <div class="k">Graded Rows</div>
        <div class="v">{graded}</div>
      </div>
      <div class="card">
        <div class="k">Answer EM</div>
        <div class="v">{esc(pct(answer_em))}</div>
      </div>
      <div class="card">
        <div class="k">Answer F1</div>
        <div class="v">{esc(pct(answer_f1))}</div>
      </div>
      <div class="card good">
        <div class="k">Correct</div>
        <div class="v">{counts.get("CORRECT", 0)}</div>
      </div>
      <div class="card warn">
        <div class="k">Partial</div>
        <div class="v">{counts.get("PARTIAL", 0)}</div>
      </div>
      <div class="card bad">
        <div class="k">Wrong</div>
        <div class="v">{counts.get("WRONG", 0)}</div>
      </div>
    </div>

    <section class="section">
      <p>Generated at {esc(timestamp)}</p>
        <div class="meta-list">
        <div>Run Directory</div><div class="path">{esc(run_dir)}</div>
        <div>Source CSV</div><div class="path">{esc(source_csv)}</div>
        <div>Judged CSV</div><div class="path">{esc(judged_csv)}</div>
        <div>Summary JSON</div><div class="path">{esc(run_dir / "hotpotqa_answer_summary.json")}</div>
        <div>Eval Rows JSONL</div><div class="path">{esc(run_dir / "hotpotqa_answer_eval_rows.jsonl")}</div>
        <div>Retrieval Detail HTML</div><div class="path"><a href="{esc(retrieval_detail_path.name)}">{esc(retrieval_detail_path.name)}</a></div>
      </div>
      <p class="note">Label rule: CORRECT = EM 1.0, PARTIAL = EM 0 but F1 greater than 0, WRONG = F1 0. Retrieved memories are no longer embedded into the judged CSV; only top titles and URIs remain there.</p>
    </section>

    <section class="section">
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th style="width: 160px;">question_id</th>
              <th style="width: 110px;">type</th>
              <th style="width: 90px;">level</th>
              <th style="width: 260px;">question</th>
              <th style="width: 160px;">gold</th>
              <th style="width: 260px;">prediction</th>
              <th style="width: 90px;">label</th>
              <th style="width: 80px;">EM</th>
              <th style="width: 80px;">F1</th>
              <th style="width: 110px;">retrieval</th>
              <th style="width: 110px;">health</th>
              <th style="width: 90px;">hits</th>
              <th style="width: 90px;">memories</th>
            </tr>
          </thead>
          <tbody>
            {''.join(rows_html)}
          </tbody>
        </table>
      </div>
    </section>
  </div>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_text, encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def render_retrieval_detail_html(
    rows: list[dict[str, Any]],
    output_path: Path,
) -> None:
    cards: list[str] = []
    for row in rows:
        memories = row.get("memories") or []
        memory_items = []
        for index, item in enumerate(memories, 1):
            memory_items.append(
                f"""
                <div class="memory-item">
                  <div class="memory-meta">
                    <span>#{index}</span>
                    <span>{esc(item.get("memory_type") or "-")}</span>
                    <span>{esc(item.get("score"))}</span>
                  </div>
                  <div class="memory-title">{esc(item.get("title") or "(untitled)")}</div>
                  <div class="memory-uri" title="{esc(item.get('uri'))}">{esc(item.get("uri"))}</div>
                  <pre>{esc(item.get("snippet"))}</pre>
                </div>
                """
            )
        cards.append(
            f"""
            <section class="case">
              <div class="case-head">
                <div>
                  <h2>{esc(row.get("question_id"))}</h2>
                  <p>{esc(row.get("question"))}</p>
                </div>
                <div class="pill {esc(str(row.get('answer_eval_label') or '').lower())}">{esc(row.get("answer_eval_label"))}</div>
              </div>
              <div class="case-grid">
                <div><span>Gold</span><strong>{esc(row.get("gold_answer"))}</strong></div>
                <div><span>Prediction</span><strong>{esc(compact(row.get("prediction"), 180))}</strong></div>
                <div><span>EM / F1</span><strong>{esc(pct(row.get("answer_em")))} / {esc(pct(row.get("answer_f1")))}</strong></div>
                <div><span>Retrieval</span><strong>{esc(row.get("retrieval_status"))} / {esc(row.get("health_status"))}</strong></div>
              </div>
              <div class="memory-list">
                {''.join(memory_items) if memory_items else '<div class="empty">No retrieved memories saved for this row.</div>'}
              </div>
            </section>
            """
        )
    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>HotpotQA Retrieval Details</title>
  <style>
    :root {{
      --bg: #f7f5f0;
      --panel: #ffffff;
      --border: #e5ded2;
      --text: #111827;
      --muted: #6b7280;
      --green: #16a34a;
      --orange: #d97706;
      --red: #dc2626;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }}
    .page {{ max-width: 1280px; margin: 0 auto; padding: 24px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    p {{ margin: 0; color: var(--muted); }}
    .case {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-top: 16px; }}
    .case-head {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }}
    .case-head h2 {{ margin: 0 0 6px; font-size: 16px; }}
    .pill {{ border-radius: 999px; padding: 4px 10px; font-size: 12px; font-weight: 600; border: 1px solid var(--border); }}
    .pill.correct {{ color: var(--green); border-color: color-mix(in srgb, var(--green) 40%, white); }}
    .pill.partial {{ color: var(--orange); border-color: color-mix(in srgb, var(--orange) 40%, white); }}
    .pill.wrong {{ color: var(--red); border-color: color-mix(in srgb, var(--red) 40%, white); }}
    .case-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 14px; }}
    .case-grid span {{ display: block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }}
    .case-grid strong {{ font-size: 14px; }}
    .memory-list {{ display: grid; gap: 12px; margin-top: 14px; }}
    .memory-item {{ border: 1px solid var(--border); border-radius: 8px; padding: 12px; background: #fcfbf8; }}
    .memory-meta {{ display: flex; gap: 12px; color: var(--muted); font-size: 12px; margin-bottom: 6px; flex-wrap: wrap; }}
    .memory-title {{ font-weight: 600; margin-bottom: 4px; }}
    .memory-uri {{ font-size: 12px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    pre {{ margin: 10px 0 0; white-space: pre-wrap; word-break: break-word; background: #0f172a; color: #e2e8f0; padding: 12px; border-radius: 6px; font-size: 12px; }}
    .empty {{ color: var(--muted); font-size: 13px; padding: 8px 0; }}
    @media (max-width: 960px) {{
      .case-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <h1>HotpotQA Retrieval Details</h1>
    <p>Full retrieved-memory payloads have been moved out of the judged CSV into this detail page.</p>
    {''.join(cards)}
  </div>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Supplement a HotpotQA run with answer-only per-question judge CSV and HTML.")
    parser.add_argument("--run-dir", required=True, help="Path to the run subdirectory that contains the results CSV.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET), help="HotpotQA reference JSON path.")
    parser.add_argument("--html-name", default="", help="Optional report filename override.")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    dataset_path = Path(args.dataset).expanduser().resolve()
    if not run_dir.exists():
        raise SystemExit(f"run dir does not exist: {run_dir}")
    if not dataset_path.exists():
        raise SystemExit(f"dataset does not exist: {dataset_path}")

    source_csv = detect_csv(run_dir)
    summary = run_answer_eval(source_csv, dataset_path, run_dir)
    eval_rows = load_jsonl(run_dir / "hotpotqa_answer_eval_rows.jsonl")
    source_rows = load_csv(source_csv)
    judged_rows = build_judged_rows(source_rows, eval_rows)
    retrieval_details = build_retrieval_details(source_rows, eval_rows)

    judged_csv = run_dir / "hotpotqa_answer_judged_rows.csv"
    write_csv(judged_csv, judged_rows)
    retrieval_jsonl = run_dir / "hotpotqa_retrieval_details.jsonl"
    write_jsonl(retrieval_jsonl, retrieval_details)

    report_name = args.html_name.strip() or f"{run_dir.parent.name}_hotpotqa_answer_judge_supplement.html"
    detail_report_name = report_name.replace(".html", "_retrieval_details.html")
    web_output = WEB_REPORT_DIR / report_name
    static_output = STATIC_REPORT_DIR / report_name
    detail_web_output = WEB_REPORT_DIR / detail_report_name
    detail_static_output = STATIC_REPORT_DIR / detail_report_name
    render_retrieval_detail_html(retrieval_details, detail_web_output)
    detail_static_output.parent.mkdir(parents=True, exist_ok=True)
    detail_static_output.write_text(detail_web_output.read_text(encoding="utf-8"), encoding="utf-8")
    render_html(run_dir, source_csv, judged_csv, summary, judged_rows, web_output, detail_web_output)
    static_output.parent.mkdir(parents=True, exist_ok=True)
    static_output.write_text(web_output.read_text(encoding="utf-8"), encoding="utf-8")

    result = {
        "run_dir": str(run_dir),
        "source_csv": str(source_csv),
        "judged_csv": str(judged_csv),
        "summary_json": str(run_dir / "hotpotqa_answer_summary.json"),
        "eval_rows_jsonl": str(run_dir / "hotpotqa_answer_eval_rows.jsonl"),
        "retrieval_jsonl": str(retrieval_jsonl),
        "web_report": str(web_output),
        "static_report": str(static_output),
        "web_retrieval_report": str(detail_web_output),
        "static_retrieval_report": str(detail_static_output),
        "graded": summary.get("graded"),
        "answer_em": summary.get("answer_em"),
        "answer_f1": summary.get("answer_f1"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
