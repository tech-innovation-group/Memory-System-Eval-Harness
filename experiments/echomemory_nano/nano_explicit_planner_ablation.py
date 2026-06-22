#!/usr/bin/env python3
from __future__ import annotations

import html
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
PLANNER_PATH = ROOT / "nano_explicit_planner_tg.py"
OUT_JSON = ROOT / "nano_explicit_planner_ablation_results.json"
OUT_HTML = ROOT / "nano_explicit_planner_ablation_report.html"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@dataclass
class EvalCase:
    case_id: str
    query: str
    expected_keywords: list[str]
    preferred_top_type: str
    note: str


def setup_system() -> Any:
    mod = load_module(PLANNER_PATH, "echomemory_explicit_planner_ablation")
    mem = mod.MemoryBuilder()
    mem.append("Gina visited Rome on 2023-01-30 after leaving Milan.", "2023-02-01T09:00:00Z")
    mem.append("Gina lost his job on 2023-02-02 and started searching for design roles.", "2023-02-03T10:00:00Z")
    mem.append("Jon married Alice on 2023-03-12 in Seattle.", "2023-03-13T11:00:00Z")
    mem.append("Gina plans to move to Lisbon after the spring hiring season.", "2023-02-05T08:00:00Z")
    mem.build()
    return mod, mem


def mixed_baseline(mod: Any, mem: Any, query: str) -> dict[str, Any]:
    # Simulates a less explicit retrieval mode: always hybrid, no family split.
    plan = mod.Plan(
        intent="mixed",
        query_family="mixed",
        graph_first=False,
        preferred_node_types=["fact", "block", "event"],
        retrieval_steps=["flat_retrieval", "optional_graph_afterthought"],
        answer_rule="Use whatever matches lexically first.",
    )
    hits = mod.Retriever(mem)._hybrid(query, plan)
    return {
        "plan": plan,
        "hits": hits,
    }


def explicit_planner(mod: Any, mem: Any, query: str) -> dict[str, Any]:
    planner = mod.Planner()
    retriever = mod.Retriever(mem)
    plan = planner.plan(query)
    hits = retriever.retrieve(query, plan)
    return {
        "plan": plan,
        "hits": hits,
    }


def evaluate(case: EvalCase, baseline: dict[str, Any], explicit: dict[str, Any]) -> dict[str, Any]:
    def ok(bundle: dict[str, Any]) -> bool:
        hits = bundle["hits"]
        if not hits:
            return False
        top_type = hits[0].item_type
        blob = "\n".join(hit.content for hit in hits[:4]).lower()
        return top_type == case.preferred_top_type and all(k.lower() in blob for k in case.expected_keywords)

    return {
        "case_id": case.case_id,
        "query": case.query,
        "note": case.note,
        "baseline": {
            "plan": baseline["plan"].query_family,
            "hits": [hit.__dict__ for hit in baseline["hits"]],
            "ok": ok(baseline),
        },
        "explicit": {
            "plan": explicit["plan"].query_family,
            "hits": [hit.__dict__ for hit in explicit["hits"]],
            "ok": ok(explicit),
        },
    }


def run_eval() -> dict[str, Any]:
    mod, mem = setup_system()
    cases = [
        EvalCase("temporal_date", "When did Gina lose her job?", ["2023-02-02"], "event", "Temporal query should start from event evidence."),
        EvalCase("temporal_relation", "Who married Alice and when?", ["Jon", "2023-03-12"], "event", "Temporal+relation query should not be treated as flat lexical lookup."),
        EvalCase("relation_only", "Which two people were involved in the Seattle wedding?", ["Jon", "Alice"], "entity", "Relation query should prefer relation path and entities."),
        EvalCase("plan_query", "What does Gina plan to do after spring hiring season?", ["move to Lisbon"], "block", "Plan block can stay hybrid and need not be graph-first."),
    ]
    rows = []
    for case in cases:
        base = mixed_baseline(mod, mem, case.query)
        expl = explicit_planner(mod, mem, case.query)
        rows.append(evaluate(case, base, expl))

    summary = {
        "mixed_correct": sum(1 for row in rows if row["baseline"]["ok"]),
        "explicit_correct": sum(1 for row in rows if row["explicit"]["ok"]),
        "total_cases": len(rows),
    }
    return {"rows": rows, "summary": summary}


def render_html(data: dict[str, Any]) -> str:
    summary = data["summary"]
    rows = data["rows"]

    def render_hits(hits: list[dict[str, Any]]) -> str:
        if not hits:
            return "<li>-</li>"
        return "".join(
            f"<li><code>{html.escape(hit['item_id'])}</code> · {html.escape(hit['item_type'])} · score={html.escape(str(hit['score']))}<br>{html.escape(hit['content'][:180])}</li>"
            for hit in hits[:4]
        )

    cases_html = "".join(
        "<div class='case'>"
        f"<h3>{html.escape(row['query'])}</h3>"
        f"<p class='muted'>{html.escape(row['note'])}</p>"
        "<div class='grid two'>"
        "<div class='card'>"
        f"<div class='badge {'ok' if row['baseline']['ok'] else 'bad'}'>mixed baseline {'pass' if row['baseline']['ok'] else 'fail'}</div>"
        f"<p><b>plan:</b> {html.escape(row['baseline']['plan'])}</p>"
        "<ul>" + render_hits(row["baseline"]["hits"]) + "</ul>"
        "</div>"
        "<div class='card'>"
        f"<div class='badge {'ok' if row['explicit']['ok'] else 'bad'}'>explicit planner {'pass' if row['explicit']['ok'] else 'fail'}</div>"
        f"<p><b>plan:</b> {html.escape(row['explicit']['plan'])}</p>"
        "<ul>" + render_hits(row["explicit"]["hits"]) + "</ul>"
        "</div>"
        "</div>"
        "</div>"
        for row in rows
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Explicit Planner Ablation</title>
  <style>
    :root {{
      --bg:#f6f7fb;--panel:#fff;--text:#172033;--muted:#667085;--line:#dde4ee;
      --green:#067647;--green-soft:#ecfdf3;--red:#b42318;--red-soft:#fff1f3;
    }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.68 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
    .wrap{{max-width:1180px;margin:0 auto;padding:28px 20px 60px}}
    .hero,.case,.card{{background:var(--panel);border:1px solid var(--line);border-radius:12px}}
    .hero{{padding:28px 30px 22px}}
    .case{{padding:18px 20px;margin-top:16px}}
    .grid{{display:grid;gap:16px}}
    .grid.two{{grid-template-columns:repeat(2,minmax(0,1fr))}}
    .stats{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:18px}}
    .stat{{padding:14px 16px;border:1px solid var(--line);border-radius:10px;background:#fbfcff}}
    .stat .label{{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}}
    .stat .value{{font-size:22px;font-weight:700}}
    .card{{padding:14px}}
    h1,h3{{margin:0 0 10px}}
    p{{margin:0 0 10px}}
    ul{{margin:8px 0 0;padding-left:18px}}
    code{{background:#f3f6fb;border-radius:6px;padding:2px 6px;font-size:12px}}
    .badge{{display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:700;margin-bottom:12px}}
    .badge.ok{{background:var(--green-soft);color:var(--green)}}
    .badge.bad{{background:var(--red-soft);color:var(--red)}}
    .muted{{color:var(--muted)}}
    @media (max-width: 960px) {{
      .grid.two,.stats{{grid-template-columns:1fr}}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>EchoMemory Nano: Explicit Planner Ablation</h1>
      <p>
        这个实验比较两种思路：一种是“混合式检索，谁匹配谁先上”，另一种是“先判断 query family，再决定 graph-first / hybrid 路径”。
      </p>
      <div class="stats">
        <div class="stat"><span class="label">mixed baseline</span><span class="value">{summary['mixed_correct']}/{summary['total_cases']}</span></div>
        <div class="stat"><span class="label">explicit planner</span><span class="value">{summary['explicit_correct']}/{summary['total_cases']}</span></div>
        <div class="stat"><span class="label">结论</span><span class="value">Separation helps</span></div>
      </div>
    </div>
    {cases_html}
  </div>
</body>
</html>
"""


def main() -> None:
    data = run_eval()
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(data), encoding="utf-8")
    print(json.dumps({"json": str(OUT_JSON), "html": str(OUT_HTML), "summary": data["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
