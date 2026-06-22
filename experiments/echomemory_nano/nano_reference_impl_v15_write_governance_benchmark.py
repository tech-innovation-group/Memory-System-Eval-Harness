#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nano_reference_impl_v14 import EchoMemoryNanoReferenceV14
from nano_reference_impl_v15 import EchoMemoryNanoReferenceV15


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_reference_impl_v15_write_governance_benchmark_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_reference_v15_write_governance_benchmark_20260617.html"
)


def esc(v: Any) -> str:
    return html.escape(str(v))


@dataclass(frozen=True)
class Case:
    case_id: str
    title: str
    steps: tuple[str, ...]
    query: str
    query_time: str
    expected: str
    why: str


CASES: tuple[Case, ...] = (
    Case(
        "wgq1",
        "Newer state should become the current answer",
        (
            "Ava lives in Boston on 2025-01-10.",
            "Ava moved to Seattle on 2025-03-01.",
        ),
        "Where does Ava live now?",
        "2025-03-10",
        "Seattle",
        "Current-state questions should follow the newest valid state, not blend all past states.",
    ),
    Case(
        "wgq2",
        "Older retrospective mention should not overwrite the current state",
        (
            "Ava moved to Seattle on 2025-03-01.",
            "Ava lived in Boston on 2024-09-01.",
        ),
        "Where does Ava live now?",
        "2025-04-02",
        "Seattle",
        "Write-time latest is unsafe when a later message talks about an older story-time state.",
    ),
    Case(
        "wgq3",
        "Preference query should support as-of answering",
        (
            "Nora prefers tea on 2025-01-15.",
            "Nora prefers coffee on 2025-05-01.",
        ),
        "What does Nora prefer?",
        "2025-02-10",
        "tea",
        "A memory system should answer as-of questions, not always the final snapshot.",
    ),
    Case(
        "wgq4",
        "Same-time disagreement should surface conflict",
        (
            "Kai badge number 3142 on 2025-03-01.",
            "Kai badge number 3147 on 2025-03-01.",
        ),
        "What is Kai's badge number?",
        "2025-03-03",
        "unknown_conflict",
        "Conflicting same-time evidence should not collapse into a confident single answer.",
    ),
)


def _build_memory(cls: type[Any], case: Case) -> Any:
    mem = cls()
    for idx, step in enumerate(case.steps):
        write_time = f"{case.query_time}T0{idx + 9}:00:00Z"
        mem.append_text(role="user", content=step, write_time=write_time)
    mem.build()
    return mem


def _grade(expected: str, observed: str) -> bool:
    exp = expected.strip().lower()
    obs = observed.strip().lower().rstrip(".")
    return obs == exp


def run() -> dict[str, Any]:
    variants = [
        ("v14", EchoMemoryNanoReferenceV14),
        ("v15", EchoMemoryNanoReferenceV15),
    ]
    rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {"v14_correct": 0, "v15_correct": 0, "cases": len(CASES), "improved_cases": []}
    for case in CASES:
        row: dict[str, Any] = {
            "case_id": case.case_id,
            "title": case.title,
            "query": case.query,
            "query_time": case.query_time,
            "expected": case.expected,
            "why": case.why,
        }
        for name, cls in variants:
            mem = _build_memory(cls, case)
            result = mem.retrieve(case.query, case.query_time)
            answer = str(result.get("answer", ""))
            ok = _grade(case.expected, answer)
            row[name] = {
                "answer": answer,
                "ok": ok,
                "plan": result.get("plan", {}),
                "missing_layers": result.get("missing_layers", []),
            }
            summary[f"{name}_correct"] += int(ok)
        if (not row["v14"]["ok"]) and row["v15"]["ok"]:
            summary["improved_cases"].append(case.case_id)
        rows.append(row)
    return {"summary": summary, "rows": rows}


def render_html(report: dict[str, Any]) -> str:
    s = report["summary"]
    body_rows = []
    for row in report["rows"]:
        body_rows.append(
            f"""
            <tr>
              <td><b>{esc(row['case_id'])}</b><br><span class="muted">{esc(row['title'])}</span></td>
              <td>{esc(row['expected'])}<br><span class="muted">{esc(row['query'])} @ {esc(row['query_time'])}</span></td>
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
  <title>EchoMemory Nano v15 Write Governance Benchmark</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --line:#dbe3ee; --text:#18212f; --muted:#617184; --blue:#2563eb;
      --green:#0f8a5f; --green-soft:#eaf8f1; --red:#c43d3d; --red-soft:#fff2f2;
    }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
    .wrap{{max-width:1200px;margin:0 auto;padding:28px 18px 48px}}
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
      <h1>Nano v15 Write Governance Benchmark</h1>
      <p class="muted">
        这组对照不是新的数据集，而是同一组通用状态更新 / 冲突案例，直接比较旧参考版 <b>v14</b> 和引入 lifecycle 治理后的 <b>v15</b>。
      </p>
      <div class="stats">
        <div class="stat"><div class="muted">v14 correct</div><div class="value">{s['v14_correct']}/{s['cases']}</div></div>
        <div class="stat"><div class="muted">v15 correct</div><div class="value">{s['v15_correct']}/{s['cases']}</div></div>
        <div class="stat"><div class="muted">improved cases</div><div class="value">{len(s['improved_cases'])}</div></div>
      </div>
      <p style="margin-top:12px"><b>重点：</b>这条提升不是靠数据集关键词，而是来自一个更一般的结构改动：<b>state lifecycle governance</b>。</p>
    </section>
    <section class="panel">
      <h2>Per case</h2>
      <table>
        <thead>
          <tr>
            <th>Case</th>
            <th>Expected</th>
            <th>v14</th>
            <th>v15</th>
            <th>Why it matters</th>
          </tr>
        </thead>
        <tbody>{''.join(body_rows)}</tbody>
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
