#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def num(value: Any) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def pct(value: Any) -> str:
    try:
        return f"{float(value or 0) * 100:.2f}%"
    except Exception:
        return "-"


def verdict_badge(row: dict[str, str]) -> tuple[str, str]:
    result = str(row.get("result") or "").strip().upper()
    if result == "CORRECT":
        return "correct", "CORRECT"
    if result == "WRONG":
        return "wrong", "WRONG"
    return "pending", "PENDING"


def render_report(summary: dict[str, Any], judge: dict[str, Any], rows: list[dict[str, str]], title: str, output_path: Path) -> str:
    total_answer_prompt = sum(num(row.get("answer_prompt_tokens")) for row in rows)
    total_answer_completion = sum(num(row.get("answer_completion_tokens")) for row in rows)
    total_answer_tokens = sum(num(row.get("answer_total_tokens")) for row in rows)
    total_retrieval_tokens_est = sum(num(row.get("retrieval_tokens_est")) for row in rows)
    total_hits = sum(num(row.get("memory_hit_count")) for row in rows)

    table_rows = []
    for index, row in enumerate(rows, 1):
        badge_class, badge_text = verdict_badge(row)
        table_rows.append(
            f"""
            <tr>
              <td>{index}</td>
              <td>{esc(row.get("question_id") or row.get("sample_id"))}</td>
              <td>{esc(row.get("question"))}</td>
              <td>{esc(row.get("answer"))}</td>
              <td>{esc(row.get("response"))}</td>
              <td><span class="badge {badge_class}">{badge_text}</span></td>
              <td>{esc(row.get("health_status"))}</td>
              <td>{num(row.get("memory_hit_count"))}</td>
              <td>{num(row.get("answer_total_tokens"))}</td>
              <td>{esc(row.get("time_cost"))}</td>
            </tr>
            """
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)}</title>
  <style>
    :root {{
      --bg:#ffffff; --ink:#111827; --muted:#4b5563; --line:#e5e7eb; --soft:#f9fafb;
      --good:#166534; --good-bg:#f0fdf4; --bad:#b91c1c; --bad-bg:#fef2f2; --pending:#92400e; --pending-bg:#fffbeb;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:var(--ink); line-height:1.55; }}
    header {{ padding:28px 24px 18px; border-bottom:1px solid var(--line); }}
    main {{ max-width:1280px; margin:0 auto; padding:20px 24px 44px; }}
    h1 {{ margin:0 0 6px; font-size:28px; }}
    h2 {{ margin:28px 0 12px; font-size:20px; }}
    p, li {{ margin:6px 0; font-size:14px; }}
    code {{ font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; background:var(--soft); padding:2px 5px; border-radius:4px; }}
    .small {{ color:var(--muted); font-size:12px; }}
    .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }}
    .card {{ border:1px solid var(--line); border-radius:8px; padding:14px; background:#fff; }}
    .stat {{ font-size:28px; font-weight:700; margin-top:8px; }}
    .label {{ color:var(--muted); font-size:12px; text-transform:uppercase; }}
    table {{ width:100%; border-collapse:collapse; margin-top:12px; }}
    th, td {{ border:1px solid var(--line); padding:8px 10px; text-align:left; vertical-align:top; font-size:13px; }}
    th {{ background:var(--soft); position:sticky; top:0; }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:8px; }}
    .badge {{ display:inline-block; padding:3px 8px; border-radius:999px; font-size:12px; font-weight:600; }}
    .badge.correct {{ color:var(--good); background:var(--good-bg); }}
    .badge.wrong {{ color:var(--bad); background:var(--bad-bg); }}
    .badge.pending {{ color:var(--pending); background:var(--pending-bg); }}
    @media (max-width:1000px) {{ .grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
    @media (max-width:700px) {{ header,main {{ padding-left:16px; padding-right:16px; }} .grid {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
<header>
  <div class="small">生成时间：{esc(datetime.now().isoformat(timespec="seconds"))}</div>
  <h1>{esc(title)}</h1>
  <p class="small">输出路径：<code>{esc(output_path)}</code></p>
</header>
<main>
  <div class="grid">
    <section class="card"><div class="label">Judge Accuracy</div><div class="stat">{pct(judge.get("accuracy", summary.get("accuracy")))}</div><p>{esc(judge.get("correct", summary.get("correct", 0)))}/{esc(judge.get("count", summary.get("rows", len(rows))))}</p></section>
    <section class="card"><div class="label">Answer Tokens</div><div class="stat">{total_answer_tokens}</div><p>prompt {total_answer_prompt} / completion {total_answer_completion}</p></section>
    <section class="card"><div class="label">Import Tokens</div><div class="stat">{num(summary.get("import_total_tokens"))}</div><p>LLM import total</p></section>
    <section class="card"><div class="label">Search Intent Tokens</div><div class="stat">{num(summary.get("search_intent_total_tokens"))}</div><p>strict token log</p></section>
  </div>

  <div class="grid" style="margin-top:12px">
    <section class="card"><div class="label">Rows</div><div class="stat">{len(rows)}</div><p>memory hits total {total_hits}</p></section>
    <section class="card"><div class="label">Retrieval Tokens Est</div><div class="stat">{total_retrieval_tokens_est}</div><p>context injection estimate</p></section>
    <section class="card"><div class="label">Workspace</div><p><code>{esc(summary.get("workspace"))}</code></p></section>
    <section class="card"><div class="label">CSV</div><p><code>{esc(summary.get("output_csv"))}</code></p></section>
  </div>

  <h2>Per Question</h2>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Question ID</th>
          <th>Question</th>
          <th>Gold</th>
          <th>Response</th>
          <th>Judge</th>
          <th>Health</th>
          <th>Memory Hits</th>
          <th>Answer Tokens</th>
          <th>Time</th>
        </tr>
      </thead>
      <tbody>
        {''.join(table_rows)}
      </tbody>
    </table>
  </div>
</main>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Render EchoMemory generic benchmark report HTML from CSV/summary/judge artifacts.")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--summary", default="")
    parser.add_argument("--judge-summary", default="")
    parser.add_argument("--title", default="EchoMemory Generic Benchmark Report")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    csv_path = Path(args.csv).expanduser().resolve()
    summary_path = Path(args.summary).expanduser().resolve() if args.summary else (csv_path.parent / "summary.json")
    judge_path = Path(args.judge_summary).expanduser().resolve() if args.judge_summary else (csv_path.parent / "judge_summary.json")
    output_path = Path(args.output).expanduser().resolve() if args.output else (csv_path.parent / f"{csv_path.stem}_report.html")

    rows = read_csv(csv_path)
    summary = read_json(summary_path)
    judge = read_json(judge_path)
    output_path.write_text(render_report(summary, judge, rows, args.title, output_path), encoding="utf-8")
    print(json.dumps({"output": str(output_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
