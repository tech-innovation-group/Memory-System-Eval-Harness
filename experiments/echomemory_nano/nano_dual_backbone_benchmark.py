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
OUT_JSON = ROOT / "nano_dual_backbone_benchmark_results.json"
OUT_HTML = Path("/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_dual_backbone_benchmark_20260613.html")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


BASE = load_module(BASE_PATH, "echomemory_nano_dual_backbone_base")
EvalCase = BASE.EvalCase


@dataclass
class RichEvalCase:
    case_id: str
    family: str
    query: str
    expected_keywords: list[str]
    expected_top_layers: list[str]
    note: str


def esc(value: Any) -> str:
    return html.escape(str(value))


def build_richer_memory() -> Any:
    mem = BASE.DualBackboneMemory()
    mem.append_text("Gina joined Figma on 2023-01-12.", "2023-01-13T09:00:00Z")
    mem.append_text("Gina met Nora at a relocation workshop on 2023-02-10.", "2023-02-11T11:00:00Z")
    mem.append_text("Gina married Alex on 2023-02-18.", "2023-02-19T11:00:00Z")
    mem.append_text("Gina left Figma on 2023-03-04.", "2023-03-05T10:00:00Z")
    mem.append_text("Gina plans to move to Lisbon after leaving Figma.", "2023-03-10T14:00:00Z")
    mem.append_text("Nora helped Gina prepare a Lisbon visa checklist on 2023-03-23.", "2023-03-24T09:30:00Z")
    mem.append_text("Gina signed a Lisbon lease on 2023-04-03.", "2023-04-04T08:40:00Z")
    mem.append_text("Gina planned to start a small design studio in May 2023.", "2023-04-07T18:00:00Z")
    mem.append_text("Gina visited Lisbon on 2023-03-21.", "2023-03-22T08:00:00Z")
    mem.append_image(
        caption="Phone screenshot from Lisbon arrival day.",
        ocr="Lisbon Santa Apolonia Platform 4 08:42",
        mention_time="2023-03-22T08:01:00Z",
        story_time="2023-03-21",
        linked_subject="Gina",
        tags=["lisbon", "station", "arrival"],
    )
    mem.append_image(
        caption="Photo of lease contract page.",
        ocr="Rua Augusta 14 Lisbon Lease Agreement",
        mention_time="2023-04-04T08:55:00Z",
        story_time="2023-04-03",
        linked_subject="Gina",
        tags=["lease", "contract", "lisbon"],
    )
    mem.run_hot_path()
    mem.run_cold_path()
    return mem


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
            "notes": "Graph-first retrieval without temporal hierarchy support.",
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


def judge(run: dict[str, Any], case: RichEvalCase) -> dict[str, Any]:
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


def evaluate_case(mem: Any, case: RichEvalCase) -> dict[str, Any]:
    tree_only = run_tree_only(mem, case.query)
    graph_only = run_graph_only(mem, case.query)
    dual = run_dual(mem, case.query)
    return {
        "case_id": case.case_id,
        "family": case.family,
        "query": case.query,
        "expected_keywords": case.expected_keywords,
        "expected_top_layers": case.expected_top_layers,
        "note": case.note,
        "tree_only": tree_only,
        "tree_only_judge": judge(tree_only, case),
        "graph_only": graph_only,
        "graph_only_judge": judge(graph_only, case),
        "dual_backbone": dual,
        "dual_backbone_judge": judge(dual, case),
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    families = sorted({item["family"] for item in results})
    per_family: list[dict[str, Any]] = []
    for family in families:
        subset = [item for item in results if item["family"] == family]
        per_family.append(
            {
                "family": family,
                "cases": len(subset),
                "tree_only_passed": sum(1 for item in subset if item["tree_only_judge"]["overall_ok"]),
                "graph_only_passed": sum(1 for item in subset if item["graph_only_judge"]["overall_ok"]),
                "dual_passed": sum(1 for item in subset if item["dual_backbone_judge"]["overall_ok"]),
            }
        )
    return {
        "cases": len(results),
        "tree_only_passed": sum(1 for item in results if item["tree_only_judge"]["overall_ok"]),
        "graph_only_passed": sum(1 for item in results if item["graph_only_judge"]["overall_ok"]),
        "dual_passed": sum(1 for item in results if item["dual_backbone_judge"]["overall_ok"]),
        "per_family": per_family,
    }


def build_cases() -> list[RichEvalCase]:
    return [
        RichEvalCase(
            case_id="t1_join_date",
            family="temporal",
            query="When did Gina join Figma?",
            expected_keywords=["2023-01-12", "joined Figma"],
            expected_top_layers=["tree"],
            note="Pure date lookup should enter via temporal hierarchy.",
        ),
        RichEvalCase(
            case_id="t2_lease_date",
            family="temporal",
            query="When did Gina sign the Lisbon lease?",
            expected_keywords=["2023-04-03", "signed a Lisbon lease"],
            expected_top_layers=["tree"],
            note="Specific event date should rank a day block first.",
        ),
        RichEvalCase(
            case_id="t3_april_activity",
            family="temporal",
            query="What did Gina do in April 2023?",
            expected_keywords=["signed a Lisbon lease", "design studio"],
            expected_top_layers=["tree"],
            note="Month-level temporal summary should come from the tree.",
        ),
        RichEvalCase(
            case_id="r1_spouse",
            family="relational",
            query="Who is Gina married to?",
            expected_keywords=["Alex", "married"],
            expected_top_layers=["entity", "event"],
            note="Entity relationship should prefer graph entry points.",
        ),
        RichEvalCase(
            case_id="r2_helper",
            family="relational",
            query="Who helped Gina with the visa checklist?",
            expected_keywords=["Nora", "visa checklist"],
            expected_top_layers=["entity", "event"],
            note="Support person should be recovered from relation/event links.",
        ),
        RichEvalCase(
            case_id="r3_company_left",
            family="relational",
            query="Which company did Gina leave?",
            expected_keywords=["Figma", "left Figma"],
            expected_top_layers=["entity", "event"],
            note="Company-object relation should prefer graph nodes over summaries.",
        ),
        RichEvalCase(
            case_id="tr1_plan_after_leave",
            family="temporal_relational",
            query="What did Gina plan after leaving Figma?",
            expected_keywords=["move to Lisbon", "left Figma"],
            expected_top_layers=["event"],
            note="Needs event grounding and temporal order together.",
        ),
        RichEvalCase(
            case_id="tr2_after_workshop",
            family="temporal_relational",
            query="What happened after Gina met Nora at the relocation workshop?",
            expected_keywords=["married Alex", "2023-02-18"],
            expected_top_layers=["event"],
            note="Temporal follow-up around a relational event should favor graph event nodes.",
        ),
        RichEvalCase(
            case_id="tr3_may_plan_source",
            family="temporal_relational",
            query="What plan did Gina form after signing the Lisbon lease?",
            expected_keywords=["design studio", "signed a Lisbon lease"],
            expected_top_layers=["event"],
            note="This is the most direct dual-backbone case in the toy benchmark.",
        ),
        RichEvalCase(
            case_id="v1_station_time",
            family="visual",
            query="What time was visible in Gina's arrival screenshot?",
            expected_keywords=["08:42", "Lisbon Santa Apolonia"],
            expected_top_layers=["image_evidence"],
            note="Visual grounding should treat OCR evidence as first-class memory.",
        ),
        RichEvalCase(
            case_id="v2_station_place",
            family="visual",
            query="Which place appeared in Gina's arrival screenshot?",
            expected_keywords=["Lisbon Santa Apolonia", "Platform 4"],
            expected_top_layers=["image_evidence"],
            note="Visual place lookup should not rely on flat text facts only.",
        ),
        RichEvalCase(
            case_id="v3_lease_street",
            family="visual",
            query="What street name was shown in the lease contract photo?",
            expected_keywords=["Rua Augusta 14", "Lease Agreement"],
            expected_top_layers=["image_evidence"],
            note="A second image case avoids overfitting to one screenshot.",
        ),
    ]


def render_html(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    family_rows = []
    for family in summary["per_family"]:
        family_rows.append(
            f"""
            <tr>
              <td>{esc(family['family'])}</td>
              <td>{esc(family['cases'])}</td>
              <td>{esc(family['tree_only_passed'])}</td>
              <td>{esc(family['graph_only_passed'])}</td>
              <td>{esc(family['dual_passed'])}</td>
            </tr>
            """
        )

    case_rows = []
    for case in payload["cases"]:
        case_rows.append(
            f"""
            <tr>
              <td>{esc(case['case_id'])}</td>
              <td>{esc(case['family'])}</td>
              <td>{esc(case['query'])}</td>
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
  <title>EchoMemory Nano Dual-Backbone Benchmark</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --text:#18212f; --muted:#607083; --line:#dbe3ee;
      --blue:#2563eb; --blue-soft:#eaf2ff; --green:#0f9f6e; --green-soft:#eaf9f3; --amber:#b76d00; --amber-soft:#fff6e6;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.68 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",sans-serif; }}
    .wrap {{ max-width:1180px; margin:0 auto; padding:28px 20px 48px; }}
    .hero,.section {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:20px 22px; margin-bottom:16px; }}
    .kpis,.cards {{ display:grid; gap:12px; }}
    .kpis {{ grid-template-columns:repeat(4,minmax(0,1fr)); margin-top:14px; }}
    .cards {{ grid-template-columns:repeat(3,minmax(0,1fr)); }}
    .kpi,.card {{ border:1px solid var(--line); border-radius:10px; background:#fbfcff; padding:12px 14px; }}
    .label {{ display:block; font-size:12px; color:var(--muted); margin-bottom:4px; }}
    .value {{ font-size:22px; font-weight:700; }}
    h1,h2,h3 {{ margin:0 0 10px; }}
    p {{ margin:0 0 10px; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th,td {{ border-top:1px solid var(--line); text-align:left; vertical-align:top; padding:10px 8px; }}
    th {{ background:#fbfcfe; color:var(--muted); font-size:12px; text-transform:uppercase; }}
    pre {{ overflow:auto; background:#f8fafc; border:1px solid var(--line); border-radius:8px; padding:12px; font-size:12px; }}
    @media (max-width:980px) {{ .kpis,.cards {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>EchoMemory Nano Dual-Backbone Benchmark</h1>
      <p>
        This is a controlled toy benchmark, not a full benchmark claim. Its purpose is to test the method intuition:
        <b>temporal tree and relation graph cover different failure modes, and a planner-routed dual-backbone is more stable than either alone.</b>
      </p>
      <div class="kpis">
        <div class="kpi"><span class="label">Cases</span><span class="value">{esc(summary['cases'])}</span></div>
        <div class="kpi"><span class="label">Tree-only</span><span class="value">{esc(summary['tree_only_passed'])}</span></div>
        <div class="kpi"><span class="label">Graph-only</span><span class="value">{esc(summary['graph_only_passed'])}</span></div>
        <div class="kpi"><span class="label">Dual-backbone</span><span class="value">{esc(summary['dual_passed'])}</span></div>
      </div>
    </div>

    <div class="section">
      <h2>Why this benchmark exists</h2>
      <div class="cards">
        <div class="card">
          <h3>Temporal failure mode</h3>
          <p>Graph-only systems often know the right entities but are clumsy on date and month-level navigation.</p>
        </div>
        <div class="card">
          <h3>Relational failure mode</h3>
          <p>Tree-only systems keep chronology but are weaker when the answer depends on relation traversal or event participation.</p>
        </div>
        <div class="card">
          <h3>Visual failure mode</h3>
          <p>Neither flat summaries nor plain fact nodes should replace first-class image evidence when the question is about OCR or visual content.</p>
        </div>
      </div>
    </div>

    <div class="section">
      <h2>Per-family results</h2>
      <table>
        <thead>
          <tr>
            <th>Family</th>
            <th>Cases</th>
            <th>Tree-only</th>
            <th>Graph-only</th>
            <th>Dual-backbone</th>
          </tr>
        </thead>
        <tbody>{''.join(family_rows)}</tbody>
      </table>
    </div>

    <div class="section">
      <h2>Case results</h2>
      <table>
        <thead>
          <tr>
            <th>Case</th>
            <th>Family</th>
            <th>Query</th>
            <th>Tree-only</th>
            <th>Graph-only</th>
            <th>Dual</th>
            <th>Note</th>
          </tr>
        </thead>
        <tbody>{''.join(case_rows)}</tbody>
      </table>
    </div>

    <div class="section">
      <h2>Interpretation</h2>
      <p>
        If this toy benchmark behaves as intended, the paper message becomes much cleaner:
        the right upgrade for EchoMemory is not "add more graph" or "add more summaries", but
        <b>planner-routed dual-backbone memory</b>.
      </p>
      <pre>{esc(json.dumps(payload['cases'][0], ensure_ascii=False, indent=2))}</pre>
    </div>
  </div>
</body>
</html>
"""


def run_eval() -> dict[str, Any]:
    mem = build_richer_memory()
    cases = build_cases()
    results = [evaluate_case(mem, case) for case in cases]
    return {
        "summary": summarize(results),
        "cases": results,
    }


def main() -> None:
    payload = run_eval()
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(payload), encoding="utf-8")
    print(f"Wrote {OUT_JSON}")
    print(f"Wrote {OUT_HTML}")


if __name__ == "__main__":
    main()
