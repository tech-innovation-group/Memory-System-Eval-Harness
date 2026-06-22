#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nano_reference_impl_v14 import build_demo_memory as build_v14_demo_memory
from nano_reference_impl_v15 import build_demo_memory as build_v15_demo_memory


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_reference_impl_v15_cross_family_benchmark_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_reference_v15_cross_family_benchmark_20260617.html"
)


def esc(v: Any) -> str:
    return html.escape(str(v))


@dataclass(frozen=True)
class Case:
    case_id: str
    family: str
    query: str
    query_time: str
    expected_type: str
    expected: tuple[str, ...]
    why: str


CASES: tuple[Case, ...] = (
    Case(
        "cf1",
        "temporal",
        "When did Maya start the visa paperwork?",
        "2026-03-20",
        "exact",
        ("2026-03-02",),
        "Checks that three-clock temporal QA is still intact.",
    ),
    Case(
        "cf2",
        "relational",
        "Who helped Maya with the visa paperwork?",
        "2026-03-20",
        "contains_all",
        ("Maya", "Nora"),
        "Checks relation lookup did not regress while lifecycle logic was added.",
    ),
    Case(
        "cf3",
        "longitudinal",
        "How did the apartment lease situation evolve?",
        "2026-04-01",
        "contains_all",
        ("Rua Augusta 14", "2026-03-30"),
        "Checks that dossier/timeline answers still surface multi-step evolution.",
    ),
    Case(
        "cf4",
        "visual",
        "What was shown in the lease screenshot?",
        "2026-03-20",
        "contains_all",
        ("Rua Augusta 14", "2026-03-20"),
        "Checks image evidence grounding stays available.",
    ),
    Case(
        "cf5",
        "readiness",
        "Can you answer now?",
        "2026-03-20",
        "exact",
        ("ready",),
        "Checks lifecycle/readiness gate remains exposed.",
    ),
    Case(
        "cf6",
        "state",
        "What does Nora prefer?",
        "2026-04-10",
        "exact",
        ("coffee",),
        "Checks current-state selection after preference update.",
    ),
    Case(
        "cf7",
        "state",
        "What is Kai's badge number?",
        "2026-04-14",
        "exact",
        ("unknown_conflict",),
        "Checks unresolved same-time disagreement surfaces conflict instead of a confident wrong answer.",
    ),
)


def _grade(case: Case, answer: str) -> bool:
    norm = answer.strip()
    if case.expected_type == "exact":
        return norm.lower() == case.expected[0].lower()
    if case.expected_type == "contains_all":
        lowered = norm.lower()
        return all(token.lower() in lowered for token in case.expected)
    raise ValueError(case.expected_type)


def run() -> dict[str, Any]:
    memories = {
        "v14": build_v14_demo_memory(),
        "v15": build_v15_demo_memory(),
    }
    rows: list[dict[str, Any]] = []
    summary = {"cases": len(CASES), "v14_correct": 0, "v15_correct": 0, "improved_cases": []}
    for case in CASES:
        row: dict[str, Any] = {
            "case_id": case.case_id,
            "family": case.family,
            "query": case.query,
            "query_time": case.query_time,
            "expected": list(case.expected),
            "why": case.why,
        }
        for name, mem in memories.items():
            result = mem.retrieve(case.query, case.query_time)
            answer = str(result.get("answer", ""))
            ok = _grade(case, answer)
            row[name] = {
                "answer": answer,
                "ok": ok,
                "plan": result.get("plan", {}),
                "contract_ok": bool(result.get("contract_ok")),
                "missing_layers": list(result.get("missing_layers", [])),
            }
            summary[f"{name}_correct"] += int(ok)
        if (not row["v14"]["ok"]) and row["v15"]["ok"]:
            summary["improved_cases"].append(case.case_id)
        rows.append(row)
    return {"summary": summary, "rows": rows}


def render_html(report: dict[str, Any]) -> str:
    s = report["summary"]
    trs: list[str] = []
    for row in report["rows"]:
        trs.append(
            f"""
            <tr>
              <td><b>{esc(row['case_id'])}</b><br><span class="muted">{esc(row['family'])}</span></td>
              <td>{esc(row['query'])}<br><span class="muted">@ {esc(row['query_time'])}</span></td>
              <td>{esc(', '.join(row['expected']))}</td>
              <td>{esc(row['v14']['answer'])}<br><span class="pill {'ok' if row['v14']['ok'] else 'risk'}">{'ok' if row['v14']['ok'] else 'wrong'}</span></td>
              <td>{esc(row['v15']['answer'])}<br><span class="pill {'ok' if row['v15']['ok'] else 'risk'}">{'ok' if row['v15']['ok'] else 'wrong'}</span></td>
              <td>{esc(row['why'])}</td>
            </tr>
            """
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Nano v15 Cross-Family Benchmark</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --line:#dbe3ee; --text:#18212f; --muted:#617184; --blue:#2563eb;
      --green:#0f8a5f; --green-soft:#eaf8f1; --red:#c43d3d; --red-soft:#fff2f2;
    }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
    .wrap{{max-width:1240px;margin:0 auto;padding:28px 18px 48px}}
    .hero,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:22px 24px;margin-bottom:16px}}
    h1,h2{{margin:0 0 12px;line-height:1.25}}
    .muted{{color:var(--muted)}}
    .stats{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:14px}}
    .stat{{border:1px solid var(--line);border-radius:10px;padding:12px;background:#fbfcfe}}
    .value{{font-size:22px;font-weight:700}}
    .pill{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px}}
    .ok{{background:var(--green-soft);color:var(--green)}}
    .risk{{background:var(--red-soft);color:var(--red)}}
    table{{width:100%;border-collapse:collapse}}
    th,td{{border-top:1px solid var(--line);padding:10px 8px;text-align:left;vertical-align:top}}
    th{{font-size:12px;color:var(--muted);background:#fafbfc}}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>Nano v15 Cross-Family Benchmark</h1>
      <p class="muted">
        这组 benchmark 用同一批自然语言题单同时看老能力和新能力：temporal、relational、longitudinal、visual、readiness，再加 state / conflict。
        它回答的不是“v15 在写入治理题上有没有提升”，而是“<b>v15 是否在保住原能力的前提下补上 lifecycle 能力</b>”。
      </p>
      <div class="stats">
        <div class="stat"><div class="muted">v14 correct</div><div class="value">{s['v14_correct']}/{s['cases']}</div></div>
        <div class="stat"><div class="muted">v15 correct</div><div class="value">{s['v15_correct']}/{s['cases']}</div></div>
        <div class="stat"><div class="muted">improved cases</div><div class="value">{len(s['improved_cases'])}</div></div>
      </div>
      <p style="margin-top:12px"><b>结论：</b>如果这页成立，就能更有底气地说 `v15` 是主参考版升级，而不是只会做一类新题的分支技巧。</p>
    </section>
    <section class="panel">
      <h2>Per case</h2>
      <table>
        <thead>
          <tr>
            <th>Case</th>
            <th>Query</th>
            <th>Expected</th>
            <th>v14</th>
            <th>v15</th>
            <th>Why it matters</th>
          </tr>
        </thead>
        <tbody>{''.join(trs)}</tbody>
      </table>
    </section>
  </div>
</body>
</html>"""


def main() -> None:
    report = run()
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(str(OUT_JSON))
    print(str(OUT_HTML))


if __name__ == "__main__":
    main()
