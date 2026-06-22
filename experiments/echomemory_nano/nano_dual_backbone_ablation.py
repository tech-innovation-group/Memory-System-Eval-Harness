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
BASE_PATH = ROOT / "nano_tree_graph_dual_backbone.py"
OUT_JSON = ROOT / "nano_dual_backbone_ablation_results.json"
OUT_HTML = ROOT / "nano_dual_backbone_ablation_report.html"


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
    expected_top_layers: list[str]
    note: str


def esc(value: Any) -> str:
    return html.escape(str(value))


def setup_memory() -> Any:
    mod = load_module(BASE_PATH, "echomemory_dual_backbone_ablation")
    return mod.build_demo_memory()


def contains_keywords(hits: list[dict[str, Any]], expected_keywords: list[str]) -> bool:
    blob = "\n".join(str(hit.get("content", "")) for hit in hits[:4]).lower()
    return all(keyword.lower() in blob for keyword in expected_keywords)


def top_layer(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return "none"
    return str(hits[0].get("layer", ""))


def run_tree_only(mem: Any, query: str) -> dict[str, Any]:
    hits = [vars(hit) for hit in mem._dedup_and_sort(mem._search_tree(query))[:6]]
    return {
        "mode": "tree_only",
        "plan": {
            "family": mem.plan(query).family,
            "primary_backbone": "tree",
            "supporting_backbones": [],
            "notes": "Chronology-first retrieval without graph support.",
        },
        "hits": hits,
    }


def run_graph_only(mem: Any, query: str) -> dict[str, Any]:
    hits = [vars(hit) for hit in mem._dedup_and_sort(mem._search_graph(query))[:6]]
    return {
        "mode": "graph_only",
        "plan": {
            "family": mem.plan(query).family,
            "primary_backbone": "graph",
            "supporting_backbones": [],
            "notes": "Relation-first retrieval without temporal tree support.",
        },
        "hits": hits,
    }


def run_dual(mem: Any, query: str) -> dict[str, Any]:
    result = mem.search(query)
    return {
        "mode": "dual_backbone",
        "plan": result.get("plan", {}),
        "hits": result.get("hits", []),
    }


def evaluate_case(mem: Any, case: EvalCase) -> dict[str, Any]:
    tree_only = run_tree_only(mem, case.query)
    graph_only = run_graph_only(mem, case.query)
    dual = run_dual(mem, case.query)

    def judge(run: dict[str, Any]) -> dict[str, Any]:
        hits = run["hits"]
        actual_top = top_layer(hits)
        keyword_ok = contains_keywords(hits, case.expected_keywords)
        top_ok = actual_top in case.expected_top_layers
        return {
            "top_layer": actual_top,
            "keyword_ok": keyword_ok,
            "top_layer_ok": top_ok,
            "overall_ok": keyword_ok and top_ok,
        }

    return {
        "case_id": case.case_id,
        "query": case.query,
        "expected_keywords": case.expected_keywords,
        "expected_top_layers": case.expected_top_layers,
        "note": case.note,
        "tree_only": tree_only,
        "tree_only_judge": judge(tree_only),
        "graph_only": graph_only,
        "graph_only_judge": judge(graph_only),
        "dual_backbone": dual,
        "dual_backbone_judge": judge(dual),
    }


def render_html(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    rows = []
    for case in payload["cases"]:
        rows.append(
            f"""
            <tr>
              <td>{esc(case['case_id'])}</td>
              <td>{esc(case['query'])}</td>
              <td>{esc(', '.join(case['expected_top_layers']))}</td>
              <td>{esc(case['tree_only_judge']['top_layer'])} / {'yes' if case['tree_only_judge']['overall_ok'] else 'no'}</td>
              <td>{esc(case['graph_only_judge']['top_layer'])} / {'yes' if case['graph_only_judge']['overall_ok'] else 'no'}</td>
              <td>{esc(case['dual_backbone_judge']['top_layer'])} / {'yes' if case['dual_backbone_judge']['overall_ok'] else 'no'}</td>
              <td>{esc(case['note'])}</td>
            </tr>
            """
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Dual-Backbone Ablation</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --text:#18212f; --muted:#5f6b7a; --line:#dde4ee;
      --blue:#2563eb; --blue-soft:#eaf2ff; --green:#0f9f6e; --green-soft:#eafaf4; --amber:#c77b00; --amber-soft:#fff7e8;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.65 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",sans-serif; }}
    .wrap {{ max-width:1120px; margin:0 auto; padding:28px 20px 48px; }}
    .hero,.section {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:20px 22px; margin-bottom:16px; }}
    h1,h2 {{ margin:0 0 10px; }}
    p {{ margin:0 0 10px; }}
    .kpis {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-top:14px; }}
    .kpi {{ border:1px solid var(--line); border-radius:10px; padding:12px 14px; background:#fbfcff; }}
    .label {{ display:block; font-size:12px; color:var(--muted); margin-bottom:4px; }}
    .value {{ font-size:22px; font-weight:700; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th,td {{ border-top:1px solid var(--line); text-align:left; vertical-align:top; padding:10px 8px; }}
    th {{ background:#fbfcfe; color:var(--muted); font-size:12px; text-transform:uppercase; }}
    .grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; }}
    .card {{ border:1px solid var(--line); border-radius:10px; padding:14px 16px; background:#fff; }}
    code, pre {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
    pre {{ background:#f8fafc; border:1px solid var(--line); border-radius:8px; padding:12px; overflow:auto; font-size:12px; }}
    @media (max-width:980px) {{ .grid,.kpis {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>EchoMemory Dual-Backbone Ablation</h1>
      <p>
        This ablation tests the exact research claim behind the new CVPR-shaped method line:
        <b>a temporal tree and a relation graph solve different failure modes, and the combined backbone is more stable than either alone.</b>
      </p>
      <div class="kpis">
        <div class="kpi"><span class="label">Cases</span><span class="value">{esc(summary['cases'])}</span></div>
        <div class="kpi"><span class="label">Tree-only</span><span class="value">{esc(summary['tree_only_passed'])}</span></div>
        <div class="kpi"><span class="label">Graph-only</span><span class="value">{esc(summary['graph_only_passed'])}</span></div>
        <div class="kpi"><span class="label">Dual</span><span class="value">{esc(summary['dual_passed'])}</span></div>
      </div>
    </div>

    <div class="section">
      <h2>Interpretation</h2>
      <div class="grid">
        <div class="card">
          <h3>Tree-only</h3>
          <p>Usually strongest on chronology-heavy queries, but weaker on relation and visual grounding.</p>
        </div>
        <div class="card">
          <h3>Graph-only</h3>
          <p>Usually strongest on entity/event/image traversal, but weaker on chronology organization.</p>
        </div>
        <div class="card">
          <h3>Dual-backbone</h3>
          <p>Uses the planner to choose a primary backbone and supplements with the other one.</p>
        </div>
      </div>
    </div>

    <div class="section">
      <h2>Results</h2>
      <table>
        <thead>
          <tr>
            <th>Case</th>
            <th>Query</th>
            <th>Expected Top</th>
            <th>Tree-only</th>
            <th>Graph-only</th>
            <th>Dual-backbone</th>
            <th>Note</th>
          </tr>
        </thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>

    <div class="section">
      <h2>Example Case</h2>
      <pre>{esc(json.dumps(payload['cases'][0], ensure_ascii=False, indent=2))}</pre>
    </div>
  </div>
</body>
</html>
"""


def run_eval() -> dict[str, Any]:
    mem = setup_memory()
    cases = [
        EvalCase(
            case_id="temporal_join",
            query="When did Gina join Figma?",
            expected_keywords=["2023-01-12", "joined Figma"],
            expected_top_layers=["tree"],
            note="Temporal query should prefer chronology blocks.",
        ),
        EvalCase(
            case_id="relation_spouse",
            query="Who is Gina married to?",
            expected_keywords=["Alex", "married"],
            expected_top_layers=["entity", "event"],
            note="Relational query should enter via graph entities/events.",
        ),
        EvalCase(
            case_id="plan_after_event",
            query="What did Gina plan after leaving Figma?",
            expected_keywords=["move to Lisbon", "left Figma"],
            expected_top_layers=["event"],
            note="Needs event grounding plus temporal support.",
        ),
        EvalCase(
            case_id="visual_arrival",
            query="What was visible in Gina's arrival screenshot?",
            expected_keywords=["Lisbon Santa Apolonia", "08:42"],
            expected_top_layers=["image_evidence"],
            note="Visual query should use image evidence as first-class memory.",
        ),
    ]

    results = [evaluate_case(mem, case) for case in cases]
    payload = {
        "summary": {
            "cases": len(results),
            "tree_only_passed": sum(1 for r in results if r["tree_only_judge"]["overall_ok"]),
            "graph_only_passed": sum(1 for r in results if r["graph_only_judge"]["overall_ok"]),
            "dual_passed": sum(1 for r in results if r["dual_backbone_judge"]["overall_ok"]),
        },
        "cases": results,
    }
    return payload


def main() -> None:
    payload = run_eval()
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(payload), encoding="utf-8")
    print(f"Wrote {OUT_JSON}")
    print(f"Wrote {OUT_HTML}")


if __name__ == "__main__":
    main()
