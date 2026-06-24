#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web")
OPENVIKING_RUN = ROOT / "runs" / "hotpotqa_smoke_openviking_20260623c"
ECHOMEMORY_RUN = ROOT / "runs" / "hotpotqa_smoke_echomemory_20260623d"
OUT_HTML = ROOT / "web" / "static" / "generated-reports" / "hotpotqa_smoke_compare_20260623.html"
OUT_HTML_MIRROR = ROOT / "static" / "generated-reports" / "hotpotqa_smoke_compare_20260623.html"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv_row(path: Path) -> dict[str, str]:
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[0] if rows else {}


def esc(value: Any) -> str:
    return html.escape(str(value or ""))


def pretty_json(value: Any) -> str:
    return html.escape(json.dumps(value, ensure_ascii=False, indent=2))


def backend_bundle(name: str, run_dir: Path) -> dict[str, Any]:
    summary = read_json(run_dir / "summary.json")
    judge = read_json(run_dir / "judge_summary.json")
    official = read_json(run_dir / "hotpotqa_answer_summary.json")
    if name == "EchoMemory":
        row = read_csv_row(run_dir / "echomemory_generic_qa_results.csv")
    else:
        row = read_csv_row(run_dir / "openviking_generic_qa_results.csv")
    return {
        "name": name,
        "run_dir": str(run_dir),
        "summary": summary,
        "judge": judge,
        "official": official,
        "row": row,
    }


def metric_card(bundle: dict[str, Any]) -> str:
    summary = bundle["summary"]
    official = bundle["official"]
    judge = bundle["judge"]
    row = bundle["row"]
    import_record = ((summary.get("import_summary") or {}).get("records") or [{}])[0]
    return f"""
    <section class="panel">
      <h2>{esc(bundle["name"])}</h2>
      <div class="meta">{esc(bundle["run_dir"])}</div>
      <table>
        <tr><th>Judge Accuracy</th><td>{esc(judge.get("accuracy"))}</td></tr>
        <tr><th>Answer EM</th><td>{esc(official.get("answer_em"))}</td></tr>
        <tr><th>Answer F1</th><td>{esc(official.get("answer_f1"))}</td></tr>
        <tr><th>Predicted Answer</th><td>{esc(row.get("response"))}</td></tr>
        <tr><th>Gold Answer</th><td>{esc(row.get("answer"))}</td></tr>
        <tr><th>Import Status</th><td>{esc(import_record.get("status") or row.get("import_status"))}</td></tr>
        <tr><th>Import Integrity</th><td>{esc(import_record.get("integrity") or row.get("import_integrity"))}</td></tr>
        <tr><th>Retrieval Count</th><td>{esc(summary.get("avg_retrieval_count") or row.get("retrieval_count"))}</td></tr>
        <tr><th>Memory Hits</th><td>{esc(summary.get("memory_hit_total") or row.get("memory_hit_count"))}</td></tr>
        <tr><th>End-to-End Time (s)</th><td>{esc(summary.get("total_end_to_end_time_s", "-"))}</td></tr>
      </table>
      <h3>Reasoning</h3>
      <pre>{esc(row.get("reasoning"))}</pre>
      <h3>Relevant Memory</h3>
      <pre>{esc(row.get("relevant_memory"))}</pre>
    </section>
    """


def official_protocol_card() -> str:
    return """
    <section class="summary">
      <strong>OpenViking official benchmark context</strong>
      <ul>
        <li><code>v0.4.4</code> documents HotpotQA under <code>Knowledge Base QA</code>, not under conversation-memory LoCoMo.</li>
        <li>The official repo has specialized pipelines for <code>benchmark/locomo/*</code> and <code>benchmark/longmemeval/openviking/*</code>, both using <code>import -&gt; eval -&gt; judge -&gt; stat</code>.</li>
        <li>The same repo also ships a generic <code>benchmark/RAG</code> pipeline with <code>gen -&gt; eval -&gt; del</code> stages for single-turn knowledge-base QA datasets.</li>
        <li>This harness keeps the protocol comparable by normalizing HotpotQA into <code>import -&gt; QA -&gt; judge -&gt; answer-only eval</code> for both backends.</li>
      </ul>
    </section>
    """


def main() -> None:
    openviking = backend_bundle("OpenViking", OPENVIKING_RUN)
    echomemory = backend_bundle("EchoMemory", ECHOMEMORY_RUN)
    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>HotpotQA Smoke Compare 2026-06-23</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 24px; color: #111827; background: #f8fafc; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    p {{ margin: 0 0 16px; color: #475569; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; align-items: start; }}
    .panel {{ background: #fff; border: 1px solid #dbe2ea; border-radius: 8px; padding: 16px; }}
    .meta {{ color: #64748b; font-size: 12px; margin-bottom: 12px; word-break: break-all; }}
    table {{ width: 100%; border-collapse: collapse; margin-bottom: 12px; }}
    th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #e5e7eb; vertical-align: top; }}
    th {{ width: 180px; color: #475569; font-weight: 600; }}
    pre {{ white-space: pre-wrap; word-break: break-word; background: #0f172a; color: #e2e8f0; padding: 12px; border-radius: 6px; font-size: 12px; }}
    .summary {{ background: #fff; border: 1px solid #dbe2ea; border-radius: 8px; padding: 16px; margin-bottom: 20px; }}
    ul {{ margin: 8px 0 0 20px; }}
  </style>
</head>
<body>
  <h1>HotpotQA Smoke Compare</h1>
  <p>Same dataset, same one-question smoke, same judge model, same answer-only HotpotQA scorer.</p>
  <section class="summary">
    <strong>Protocol alignment</strong>
    <ul>
      <li>Dataset: <code>{esc(str(ROOT / "dataset" / "hotpotqa.sample.json"))}</code></li>
      <li>Question count: <code>1</code></li>
      <li>Lifecycle: <code>import -&gt; QA -&gt; judge -&gt; HotpotQA answer-only eval</code></li>
      <li>OpenViking import shape: <code>source documents written into user memory URIs</code></li>
      <li>EchoMemory import shape: <code>benchmark memory messages committed into isolated sample session</code></li>
      <li>Metric scope in this report: <code>answer-only EM/F1</code>; supporting-fact and joint metrics are out of scope for the current CSV shape.</li>
    </ul>
  </section>
  {official_protocol_card()}
  <div class="grid">
    {metric_card(openviking)}
    {metric_card(echomemory)}
  </div>
</body>
</html>
"""
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML.write_text(html_text, encoding="utf-8")
    OUT_HTML_MIRROR.parent.mkdir(parents=True, exist_ok=True)
    OUT_HTML_MIRROR.write_text(html_text, encoding="utf-8")
    print(str(OUT_HTML))


if __name__ == "__main__":
    main()
