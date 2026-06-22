#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from nano_reference_impl_v14 import build_demo_memory, demo_cases


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_reference_impl_v14_topic_induction_benchmark_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_reference_v14_topic_induction_benchmark_20260617.html"
)


def esc(value: Any) -> str:
    return html.escape(str(value))


def contains_keywords(text: str, keywords: list[str]) -> bool:
    lowered = text.lower()
    return all(keyword.lower() in lowered for keyword in keywords)


def evaluate_run(use_topic_hints: bool) -> dict[str, Any]:
    mem = build_demo_memory(use_topic_hints=use_topic_hints)
    rows: list[dict[str, Any]] = []
    for case in demo_cases():
        result = mem.retrieve(case.query, case.query_time)
        answer = str(result.get("answer", "") or "")
        answer_ok = contains_keywords(answer, case.expected_keywords)
        contract_ok = bool(result.get("contract_ok"))
        rows.append(
            {
                "case_id": case.case_id,
                "family": case.family,
                "query": case.query,
                "expected_keywords": case.expected_keywords,
                "answer": answer,
                "answer_ok": answer_ok,
                "contract_ok": contract_ok,
                "topic_count": len(mem.dossiers),
                "topics": sorted(mem.dossiers.keys()),
            }
        )
    summary = {
        "use_topic_hints": use_topic_hints,
        "topic_count": len(mem.dossiers),
        "answer_correct": sum(1 for row in rows if row["answer_ok"]),
        "contract_ok": sum(1 for row in rows if row["contract_ok"]),
        "cases": len(rows),
        "topics": sorted(mem.dossiers.keys()),
    }
    return {"summary": summary, "rows": rows}


def render_html(with_hints: dict[str, Any], without_hints: dict[str, Any]) -> str:
    def stat(summary: dict[str, Any]) -> str:
        return (
            f"<div class='stat'><span class='label'>topic_count</span><span class='value'>{summary['topic_count']}</span></div>"
            f"<div class='stat'><span class='label'>answer_correct</span><span class='value'>{summary['answer_correct']}/{summary['cases']}</span></div>"
            f"<div class='stat'><span class='label'>contract_ok</span><span class='value'>{summary['contract_ok']}/{summary['cases']}</span></div>"
        )

    rows_html = []
    for left, right in zip(with_hints["rows"], without_hints["rows"]):
        rows_html.append(
            "<tr>"
            f"<td><b>{esc(left['case_id'])}</b><br /><span class='muted'>{esc(left['family'])}</span></td>"
            f"<td>{esc(left['query'])}</td>"
            f"<td>{esc(left['answer'])}<br /><span class='muted'>contract={left['contract_ok']}</span></td>"
            f"<td>{esc(right['answer'])}<br /><span class='muted'>contract={right['contract_ok']}</span></td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Nano v14 Topic Induction Benchmark</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --line:#dbe3ee; --text:#18212f; --muted:#617184;
      --green:#0f766e; --amber:#b45309; --blue:#2563eb;
    }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
    .wrap{{max-width:1180px;margin:0 auto;padding:28px 18px 48px}}
    .hero,.section{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:22px 24px;margin-bottom:16px}}
    .grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}}
    .stats{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:14px}}
    .stat{{border:1px solid var(--line);border-radius:10px;padding:12px;background:#fbfcfe}}
    .label{{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}}
    .value{{font-size:22px;font-weight:700}}
    h1,h2{{margin:0 0 12px;line-height:1.25}}
    table{{width:100%;border-collapse:collapse}}
    th,td{{border-top:1px solid var(--line);padding:10px 8px;text-align:left;vertical-align:top}}
    th{{font-size:12px;color:var(--muted);background:#fafbfc}}
    code{{background:#f2f4f8;padding:1px 4px;border-radius:4px;font-size:12px}}
    .muted{{color:var(--muted)}}
    @media (max-width: 900px){{.grid,.stats{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory Nano v14 Topic Induction Benchmark</h1>
      <p>
        这页专门检查一个问题：<b>topic dossier 是否必须依赖手动 topic_hint 才能工作</b>。
        新版 nano 把默认 topic 归纳改成了更通用的相似度聚类，所以这里比较：
      </p>
      <ul>
        <li><code>with topic_hint</code>: 人工显式标注 topic</li>
        <li><code>without topic_hint</code>: 完全不传 hint，只靠 generic topic induction</li>
      </ul>
    </section>

    <section class="grid">
      <div class="section">
        <h2>With topic_hint</h2>
        <div class="stats">{stat(with_hints['summary'])}</div>
        <p class="muted">topics: {esc(", ".join(with_hints['summary']['topics']))}</p>
      </div>
      <div class="section">
        <h2>Without topic_hint</h2>
        <div class="stats">{stat(without_hints['summary'])}</div>
        <p class="muted">topics: {esc(", ".join(without_hints['summary']['topics']))}</p>
      </div>
    </section>

    <section class="section">
      <h2>Per-case comparison</h2>
      <table>
        <thead>
          <tr>
            <th>Case</th>
            <th>Query</th>
            <th>With topic_hint</th>
            <th>Without topic_hint</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows_html)}
        </tbody>
      </table>
    </section>
  </div>
</body>
</html>"""


def main() -> None:
    with_hints = evaluate_run(True)
    without_hints = evaluate_run(False)
    payload = {"with_hints": with_hints, "without_hints": without_hints}
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(with_hints, without_hints), encoding="utf-8")
    print(json.dumps({"json": str(OUT_JSON), "html": str(OUT_HTML)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
