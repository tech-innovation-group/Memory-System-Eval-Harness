#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from nano_reference_impl_v14 import build_demo_memory


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_reference_impl_v14_answerability_benchmark_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_reference_v14_answerability_benchmark_20260617.html"
)


def esc(v: Any) -> str:
    return html.escape(str(v))


def legacy_answer(query: str, result: dict[str, Any]) -> str:
    if not result.get("contract_ok"):
        return "unknown"
    plan = result.get("plan", {})
    hits = result.get("hits", [])
    if not hits:
        return "unknown"
    top = hits[0]
    if plan.get("family") == "temporal":
        for hit in hits:
            if hit.get("story_time"):
                return str(hit.get("story_time"))
        return "unknown"
    if plan.get("family") == "relational":
        joined = "\n".join(hit.get("content", "") for hit in hits[:4])
        if "who helped" in query.lower():
            return "Nora" if "Nora" in joined else "unknown"
        return str(top.get("content", "")).split("\n", 1)[0]
    if plan.get("family") == "longitudinal":
        return str(top.get("content", "")).split("\n", 1)[0]
    if plan.get("family") == "visual":
        return str(top.get("content", "")).split("\n", 1)[0]
    return str(top.get("content", "")).split("\n", 1)[0]


def run_eval() -> dict[str, Any]:
    mem = build_demo_memory(use_topic_hints=False)
    cases = [
        {"case_id": "temporal", "query": "When did Maya start the visa paperwork?", "expected": "2026-03-02"},
        {"case_id": "relational", "query": "Who helped Maya with the visa paperwork?", "expected": "Nora"},
        {"case_id": "longitudinal", "query": "How did the apartment lease situation evolve?", "expected": "Rua Augusta 14"},
        {"case_id": "visual", "query": "What was shown in the lease screenshot?", "expected": "Rua Augusta 14"},
        {"case_id": "unsupported", "query": "Which company invited Nora to Lisbon?", "expected": "unknown"},
        {"case_id": "readiness", "query": "Can you answer now?", "expected": "ready"},
    ]
    rows: list[dict[str, Any]] = []
    summary = {
        "cases": len(cases),
        "legacy_correct": 0,
        "enforced_correct": 0,
        "improved_cases": [],
    }
    for case in cases:
        result = mem.retrieve(case["query"], "2026-03-20")
        legacy = legacy_answer(case["query"], result)
        enforced = str(result.get("answer", ""))
        expected = case["expected"].lower()
        legacy_ok = expected in legacy.lower() if expected != "unknown" else legacy.lower() == "unknown"
        enforced_ok = expected in enforced.lower() if expected != "unknown" else enforced.lower() == "unknown"
        summary["legacy_correct"] += int(legacy_ok)
        summary["enforced_correct"] += int(enforced_ok)
        if (not legacy_ok) and enforced_ok:
            summary["improved_cases"].append(case["case_id"])
        rows.append(
            {
                "case_id": case["case_id"],
                "query": case["query"],
                "expected": case["expected"],
                "legacy": legacy,
                "legacy_ok": legacy_ok,
                "enforced": enforced,
                "enforced_ok": enforced_ok,
                "plan": result.get("plan", {}),
                "contract_ok": result.get("contract_ok"),
                "missing_layers": result.get("missing_layers", []),
            }
        )
    return {"summary": summary, "rows": rows}


def render_html(report: dict[str, Any]) -> str:
    s = report["summary"]
    rows = report["rows"]
    tr = []
    for row in rows:
        tr.append(
            "<tr>"
            f"<td><b>{esc(row['case_id'])}</b><br><span class='muted'>{esc(row['query'])}</span></td>"
            f"<td>{esc(row['expected'])}</td>"
            f"<td>{esc(row['legacy'])}<br><span class='muted'>{'ok' if row['legacy_ok'] else 'wrong'}</span></td>"
            f"<td>{esc(row['enforced'])}<br><span class='muted'>{'ok' if row['enforced_ok'] else 'wrong'}</span></td>"
            f"<td>{esc(row['plan'].get('family', ''))}<br><span class='muted'>contract={row['contract_ok']}, missing={esc(', '.join(row['missing_layers']) or '-')}</span></td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Nano Answerability Benchmark</title>
  <style>
    :root{{--bg:#f6f8fc;--panel:#fff;--line:#dbe3ee;--text:#18212f;--muted:#617184;--blue:#2563eb}}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
    .wrap{{max-width:1180px;margin:0 auto;padding:28px 18px 48px}}
    .hero,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:22px 24px;margin-bottom:16px}}
    h1,h2{{margin:0 0 12px;line-height:1.25}}
    .muted{{color:var(--muted)}}
    .stats{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:14px}}
    .stat{{border:1px solid var(--line);border-radius:10px;padding:12px;background:#fbfcfe}}
    .label{{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}}
    .value{{font-size:22px;font-weight:700}}
    table{{width:100%;border-collapse:collapse}}
    th,td{{border-top:1px solid var(--line);padding:10px 8px;text-align:left;vertical-align:top}}
    th{{font-size:12px;color:var(--muted);background:#fafbfc}}
    .note{{border-left:4px solid var(--blue);background:#f7fbff;padding:12px 14px;border-radius:8px}}
    @media (max-width: 900px){{.stats{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>Answerability Benchmark</h1>
      <p class="muted">对比 old-style “只要有证据就尽量答” 和 enforced answerability gate。这里的重点是：contract_ok 不等于 answerable。</p>
      <div class="stats">
        <div class="stat"><span class="label">legacy_correct</span><span class="value">{s['legacy_correct']}/{s['cases']}</span></div>
        <div class="stat"><span class="label">enforced_correct</span><span class="value">{s['enforced_correct']}/{s['cases']}</span></div>
        <div class="stat"><span class="label">improved_cases</span><span class="value">{len(s['improved_cases'])}</span></div>
      </div>
      <div class="note" style="margin-top:12px">
        这条结果特别适合写进论文：<b>如果没有 answerability gate，系统可能会在表面 contract 完整时仍给出不相关答案。</b>
      </div>
    </section>

    <section class="panel">
      <h2>Per case</h2>
      <table>
        <thead>
          <tr>
            <th>Case</th>
            <th>Expected</th>
            <th>Legacy</th>
            <th>Enforced</th>
            <th>Plan / Contract</th>
          </tr>
        </thead>
        <tbody>{''.join(tr)}</tbody>
      </table>
    </section>
  </div>
</body>
</html>"""


def main() -> None:
    report = run_eval()
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(str(OUT_JSON))
    print(str(OUT_HTML))


if __name__ == "__main__":
    main()
