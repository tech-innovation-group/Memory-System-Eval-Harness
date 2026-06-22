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
OUT_JSON = ROOT / "nano_relation_backbone_ablation_results.json"
OUT_HTML = ROOT / "nano_relation_backbone_ablation_report.html"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@dataclass
class Case:
    case_id: str
    query: str
    expected_keywords: list[str]
    expected_top_layers: list[str]
    note: str


def build_memory() -> Any:
    mod = load_module(BASE_PATH, "echomemory_relation_backbone_ablation")
    return mod.build_demo_memory()


def top_layer(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return "none"
    return str(hits[0].get("layer", ""))


def contains_keywords(hits: list[dict[str, Any]], expected_keywords: list[str]) -> bool:
    blob = "\n".join(str(hit.get("content", "")) for hit in hits[:4]).lower()
    return all(keyword.lower() in blob for keyword in expected_keywords)


def run_tree_only(mem: Any, query: str) -> dict[str, Any]:
    hits = [vars(hit) for hit in mem._dedup_and_sort(mem._search_tree(query))[:6]]
    return {"mode": "tree_only", "hits": hits}


def run_graph_only(mem: Any, query: str) -> dict[str, Any]:
    hits = [vars(hit) for hit in mem._dedup_and_sort(mem._search_graph(query))[:6]]
    return {"mode": "graph_only", "hits": hits}


def run_dual(mem: Any, query: str) -> dict[str, Any]:
    result = mem.search(query)
    return {
        "mode": "dual_backbone",
        "hits": result.get("hits", []),
        "plan": result.get("plan", {}),
    }


def judge(case: Case, run: dict[str, Any]) -> dict[str, Any]:
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


def evaluate_case(mem: Any, case: Case) -> dict[str, Any]:
    tree_only = run_tree_only(mem, case.query)
    graph_only = run_graph_only(mem, case.query)
    dual = run_dual(mem, case.query)
    return {
        "case_id": case.case_id,
        "query": case.query,
        "expected_keywords": case.expected_keywords,
        "expected_top_layers": case.expected_top_layers,
        "note": case.note,
        "tree_only": tree_only,
        "tree_only_judge": judge(case, tree_only),
        "graph_only": graph_only,
        "graph_only_judge": judge(case, graph_only),
        "dual_backbone": dual,
        "dual_backbone_judge": judge(case, dual),
    }


def render_html(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    rows = []
    for case in payload["cases"]:
        rows.append(
            f"""
            <tr>
              <td>{html.escape(case['case_id'])}</td>
              <td>{html.escape(case['query'])}</td>
              <td>{html.escape(', '.join(case['expected_top_layers']))}</td>
              <td>{html.escape(case['tree_only_judge']['top_layer'])} / {'yes' if case['tree_only_judge']['overall_ok'] else 'no'}</td>
              <td>{html.escape(case['graph_only_judge']['top_layer'])} / {'yes' if case['graph_only_judge']['overall_ok'] else 'no'}</td>
              <td>{html.escape(case['dual_backbone_judge']['top_layer'])} / {'yes' if case['dual_backbone_judge']['overall_ok'] else 'no'}</td>
              <td>{html.escape(case['note'])}</td>
            </tr>
            """
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Relation Backbone Ablation</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --text:#18212f; --muted:#617184; --line:#dbe3ee;
      --shadow:0 10px 28px rgba(15,23,42,.08);
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.68 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",sans-serif; }}
    .wrap {{ max-width:1120px; margin:0 auto; padding:28px 20px 48px; }}
    .hero,.section {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:20px 22px; margin-bottom:16px; box-shadow:var(--shadow); }}
    h1,h2 {{ margin:0 0 10px; }}
    p {{ margin:0 0 10px; }}
    .kpis {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-top:14px; }}
    .kpi {{ border:1px solid var(--line); border-radius:10px; padding:12px 14px; background:#fbfcff; }}
    .label {{ display:block; font-size:12px; color:var(--muted); margin-bottom:4px; }}
    .value {{ font-size:22px; font-weight:700; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th,td {{ border-top:1px solid var(--line); text-align:left; vertical-align:top; padding:10px 8px; }}
    th {{ background:#fbfcfe; color:var(--muted); font-size:12px; text-transform:uppercase; }}
    pre {{ background:#f8fafc; border:1px solid var(--line); border-radius:8px; padding:12px; overflow:auto; font-size:12px; }}
    @media (max-width:980px) {{ .kpis {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>EchoMemory Relation Backbone Ablation</h1>
      <p>
        这组小实验和 anchored temporal ablation 成对：它只测 relation-heavy questions，
        看 <b>tree-only、graph-only、dual-backbone</b> 谁更适合作为 primary backbone。
      </p>
      <div class="kpis">
        <div class="kpi"><span class="label">Cases</span><span class="value">{summary['cases']}</span></div>
        <div class="kpi"><span class="label">Tree-only</span><span class="value">{summary['tree_only_passed']}</span></div>
        <div class="kpi"><span class="label">Graph-only</span><span class="value">{summary['graph_only_passed']}</span></div>
        <div class="kpi"><span class="label">Dual-backbone</span><span class="value">{summary['dual_passed']}</span></div>
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
      <h2>Interpretation</h2>
      <p>
        如果这组结果比 anchored temporal ablation 呈现出相反趋势，那就正好支持 paired claim：
        <b>temporal questions 应该以 tree 为主，relation-heavy questions 应该以 graph 为主。</b>
      </p>
    </div>

    <div class="section">
      <h2>Example</h2>
      <pre>{html.escape(json.dumps(payload['cases'][0], ensure_ascii=False, indent=2))}</pre>
    </div>
  </div>
</body>
</html>"""


def main() -> None:
    mem = build_memory()
    cases = [
        Case(
            case_id="spouse_relation",
            query="Who is Gina married to?",
            expected_keywords=["Alex", "married"],
            expected_top_layers=["entity", "event"],
            note="Spouse relation should prefer graph entry points rather than chronology blocks.",
        ),
        Case(
            case_id="plan_relation",
            query="What did Gina plan after leaving Figma?",
            expected_keywords=["move to Lisbon", "left Figma"],
            expected_top_layers=["entity", "event"],
            note="Plan-after-event is still relation-heavy because it depends on linking plan and departure event.",
        ),
        Case(
            case_id="company_left",
            query="Which company did Gina leave?",
            expected_keywords=["Figma", "left"],
            expected_top_layers=["event", "entity"],
            note="Company-of-departure is a relation grounded in a specific event edge.",
        ),
    ]

    evaluated = [evaluate_case(mem, case) for case in cases]
    payload = {
        "summary": {
            "cases": len(evaluated),
            "tree_only_passed": sum(1 for item in evaluated if item["tree_only_judge"]["overall_ok"]),
            "graph_only_passed": sum(1 for item in evaluated if item["graph_only_judge"]["overall_ok"]),
            "dual_passed": sum(1 for item in evaluated if item["dual_backbone_judge"]["overall_ok"]),
        },
        "cases": evaluated,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(payload), encoding="utf-8")
    print(json.dumps({"ok": True, "json": str(OUT_JSON), "html": str(OUT_HTML)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
