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
BASE_PATH = ROOT / "nano_modular_maincode_upgrade.py"
OUT_JSON = ROOT / "nano_graph_second_pass_contract_ablation_results.json"
OUT_HTML = ROOT / "nano_graph_second_pass_contract_ablation_report.html"
PUBLIC_HTML = Path("/Users/chx/locomo-eval-web/web/static/echomemory_nano_graph_second_pass_contract_ablation_20260615.html")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MOD = load_module(BASE_PATH, "echomemory_nano_modular_upgrade_contract_ablation")


@dataclass
class EvalCase:
    case_id: str
    query: str
    expected_keywords: list[str]
    note: str


def build_system() -> Any:
    stream = MOD.SessionStream()
    stream.append("user", "Gina joined Figma on 2024-01-05.", "2024-01-06T09:00:00+00:00")
    stream.append("user", "Gina married Alex on 2024-03-10.", "2024-03-11T09:00:00+00:00")
    stream.append("user", "Nora helped Gina prepare a Lisbon visa checklist on 2024-04-02.", "2024-04-03T09:00:00+00:00")
    stream.append("user", "Gina signed a Lisbon lease on 2024-05-08.", "2024-05-09T09:00:00+00:00")
    stream.append("user", "Gina left Figma on 2024-06-01.", "2024-06-02T09:00:00+00:00")
    stream.append("user", "Gina plans to move to Lisbon after leaving Figma.", "2024-06-03T09:00:00+00:00")
    stream.append("user", "Screenshot of lease contract showed Rua Augusta 14 Lisbon Lease Agreement.", "2024-05-09T11:00:00+00:00")
    stream.append("user", "Arrival photo showed Santa Apolonia Platform 4.", "2024-06-10T08:00:00+00:00")

    extractor = MOD.AtomExtractor()
    atoms = extractor.extract(stream.messages)
    stream.readiness.atoms_ready = True
    tree_blocks = MOD.TemporalTreeProjector().project(atoms)
    nodes, edges = MOD.GraphProjector().project(atoms)
    stream.readiness.tree_ready = True
    stream.readiness.graph_ready = True
    stream.readiness.qa_ready = True
    return {
        "stream": stream,
        "atoms": atoms,
        "tree_blocks": tree_blocks,
        "nodes": nodes,
        "edges": edges,
        "orchestrator": MOD.SearchOrchestrator(stream, atoms, tree_blocks, nodes, edges),
    }


def hit_layers(hits: list[dict[str, Any]]) -> set[str]:
    return {str(hit.get("layer", "")) for hit in hits}


def keyword_ok(hits: list[dict[str, Any]], expected_keywords: list[str]) -> bool:
    blob = "\n".join(str(hit.get("content", "")) for hit in hits[:4]).lower()
    return all(keyword.lower() in blob for keyword in expected_keywords)


def coverage_ratio(required_layers: list[str], hits: list[dict[str, Any]]) -> float:
    present = hit_layers(hits)
    if not required_layers:
        return 1.0
    matched = sum(1 for layer in required_layers if layer in present)
    return matched / len(required_layers)


def one_pass_search(system: dict[str, Any], query: str) -> dict[str, Any]:
    orchestrator = system["orchestrator"]
    intent = orchestrator.intent_classifier.classify(query)
    plan = orchestrator.planner.plan(query, intent)
    readiness = MOD.asdict(orchestrator.stream.readiness)
    if not orchestrator.stream.readiness.qa_ready:
        return {
            "query": query,
            "intent": MOD.asdict(intent),
            "plan": MOD.asdict(plan),
            "hits": [],
            "mode": "one_pass",
            "readiness": readiness,
            "decision": {"answer": "unknown", "confidence": 0.0, "note": "qa_ready=false"},
        }
    primary_hits = orchestrator._read(plan.primary_reader, query)
    merged = orchestrator.fusion.merge([primary_hits])
    need_expand, confidence, note = orchestrator.self_check.assess(plan, merged)
    answer = MOD.compose_answer(query, plan, merged, confidence)
    return {
        "query": query,
        "intent": MOD.asdict(intent),
        "plan": MOD.asdict(plan),
        "hits": [MOD.asdict(hit) for hit in merged[:6]],
        "mode": "one_pass",
        "readiness": readiness,
        "self_check": {
            "need_expand": need_expand,
            "confidence": round(confidence, 3),
            "note": note,
        },
        "decision": {
            "answer": answer,
            "confidence": round(confidence, 3),
            "note": "Primary reader only.",
        },
    }


def graph_second_pass_search(system: dict[str, Any], query: str) -> dict[str, Any]:
    orchestrator = system["orchestrator"]
    intent = orchestrator.intent_classifier.classify(query)
    plan = orchestrator.planner.plan(query, intent)
    readiness = MOD.asdict(orchestrator.stream.readiness)
    if not orchestrator.stream.readiness.qa_ready:
        return {
            "query": query,
            "intent": MOD.asdict(intent),
            "plan": MOD.asdict(plan),
            "hits": [],
            "mode": "graph_second_pass",
            "readiness": readiness,
            "decision": {"answer": "unknown", "confidence": 0.0, "note": "qa_ready=false"},
        }
    primary_hits = orchestrator._read(plan.primary_reader, query)
    merged = orchestrator.fusion.merge([primary_hits])
    need_expand, confidence, note = orchestrator.self_check.assess(plan, merged)
    second_pass_triggered = False
    if need_expand and plan.primary_reader != "graph" and "graph" in plan.supporting_readers:
        graph_hits = orchestrator._read("graph", query)
        merged = orchestrator.fusion.merge([primary_hits, graph_hits])
        need_expand, confidence, note = orchestrator.self_check.assess(plan, merged)
        second_pass_triggered = True
    answer = MOD.compose_answer(query, plan, merged, confidence)
    return {
        "query": query,
        "intent": MOD.asdict(intent),
        "plan": MOD.asdict(plan),
        "hits": [MOD.asdict(hit) for hit in merged[:6]],
        "mode": "graph_second_pass",
        "readiness": readiness,
        "self_check": {
            "need_expand": need_expand,
            "confidence": round(confidence, 3),
            "note": note,
            "second_pass_triggered": second_pass_triggered,
        },
        "decision": {
            "answer": answer,
            "confidence": round(confidence, 3),
            "note": "Graph second-pass added after self-check." if second_pass_triggered else "No graph second-pass needed.",
        },
    }


def evaluate_run(run: dict[str, Any], expected_keywords: list[str]) -> dict[str, Any]:
    required_layers = list(run["plan"].get("must_have_layers", []))
    hits = run.get("hits", [])
    return {
        "keyword_ok": keyword_ok(hits, expected_keywords),
        "required_layers": required_layers,
        "present_layers": sorted(hit_layers(hits)),
        "coverage_ratio": round(coverage_ratio(required_layers, hits), 3),
        "contract_ok": keyword_ok(hits, expected_keywords) and coverage_ratio(required_layers, hits) >= 1.0,
        "top_layer": hits[0]["layer"] if hits else "none",
    }


def run_eval() -> dict[str, Any]:
    system = build_system()
    cases = [
        EvalCase(
            case_id="temporal_leave_date",
            query="When did Gina leave Figma?",
            expected_keywords=["2024-06-01", "left Figma"],
            note="Primary tree hit already has the date, but the contract still wants event support.",
        ),
        EvalCase(
            case_id="temporal_join_date",
            query="When did Gina join Figma?",
            expected_keywords=["2024-01-05", "joined Figma"],
            note="Temporal date lookup should benefit from adding event evidence after tree-first retrieval.",
        ),
        EvalCase(
            case_id="temporal_helper_date",
            query="When did Nora help Gina prepare the Lisbon visa checklist?",
            expected_keywords=["2024-04-02", "Nora helped Gina"],
            note="Another temporal case where tree-only evidence is accurate but structurally incomplete.",
        ),
        EvalCase(
            case_id="temporal_lease_date",
            query="When did Gina sign the Lisbon lease?",
            expected_keywords=["2024-05-08", "signed a Lisbon lease"],
            note="Tests the same contract on a different event family.",
        ),
        EvalCase(
            case_id="relational_helper",
            query="Who helped Gina with the Lisbon visa checklist?",
            expected_keywords=["Nora", "visa checklist"],
            note="Control case: graph is already the primary backbone, so second-pass should not matter.",
        ),
        EvalCase(
            case_id="visual_lease",
            query="What did the screenshot of the lease contract show?",
            expected_keywords=["Rua Augusta 14", "Lease Agreement"],
            note="Another control case: visual evidence should already enter through graph/image nodes.",
        ),
    ]

    results: list[dict[str, Any]] = []
    summary = {
        "cases": len(cases),
        "one_pass_contract_ok": 0,
        "graph_second_pass_contract_ok": 0,
        "one_pass_keyword_ok": 0,
        "graph_second_pass_keyword_ok": 0,
        "improved_contract_cases": [],
        "second_pass_triggered_cases": [],
    }

    for case in cases:
        one_pass = one_pass_search(system, case.query)
        second_pass = graph_second_pass_search(system, case.query)
        one_eval = evaluate_run(one_pass, case.expected_keywords)
        second_eval = evaluate_run(second_pass, case.expected_keywords)

        if one_eval["keyword_ok"]:
            summary["one_pass_keyword_ok"] += 1
        if second_eval["keyword_ok"]:
            summary["graph_second_pass_keyword_ok"] += 1
        if one_eval["contract_ok"]:
            summary["one_pass_contract_ok"] += 1
        if second_eval["contract_ok"]:
            summary["graph_second_pass_contract_ok"] += 1
        if (not one_eval["contract_ok"]) and second_eval["contract_ok"]:
            summary["improved_contract_cases"].append(case.case_id)
        if second_pass.get("self_check", {}).get("second_pass_triggered"):
            summary["second_pass_triggered_cases"].append(case.case_id)

        results.append(
            {
                "case_id": case.case_id,
                "query": case.query,
                "expected_keywords": case.expected_keywords,
                "note": case.note,
                "one_pass": one_pass,
                "one_pass_eval": one_eval,
                "graph_second_pass": second_pass,
                "graph_second_pass_eval": second_eval,
            }
        )
    return {"summary": summary, "cases": results}


def esc(value: Any) -> str:
    return html.escape(str(value))


def render_html(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    rows = []
    for case in payload["cases"]:
        rows.append(
            f"""
            <tr>
              <td>{esc(case['case_id'])}</td>
              <td>{esc(case['query'])}</td>
              <td>{esc(', '.join(case['one_pass_eval']['required_layers']))}</td>
              <td>{esc(case['one_pass_eval']['top_layer'])} / {case['one_pass_eval']['coverage_ratio']:.2f} / {'yes' if case['one_pass_eval']['contract_ok'] else 'no'}</td>
              <td>{esc(case['graph_second_pass_eval']['top_layer'])} / {case['graph_second_pass_eval']['coverage_ratio']:.2f} / {'yes' if case['graph_second_pass_eval']['contract_ok'] else 'no'}</td>
              <td>{'yes' if case['graph_second_pass'].get('self_check', {}).get('second_pass_triggered') else 'no'}</td>
              <td>{esc(case['note'])}</td>
            </tr>
            """
        )

    sample_case = payload["cases"][0] if payload["cases"] else {}
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Graph Second-Pass Contract Ablation</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --text:#18212f; --muted:#5f6b7a; --line:#dde4ee;
      --blue:#2563eb; --blue-soft:#eaf2ff; --green:#0f9f6e; --green-soft:#eafaf4;
      --amber:#c77b00; --amber-soft:#fff7e8;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.65 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",sans-serif; }}
    .wrap {{ max-width:1160px; margin:0 auto; padding:28px 20px 48px; }}
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
    .tag {{ display:inline-block; padding:4px 10px; border-radius:999px; font-size:12px; background:var(--blue-soft); color:var(--blue); margin-right:6px; }}
    @media (max-width:980px) {{ .grid,.kpis {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <div class="tag">nano</div>
      <div class="tag">main-code shaped</div>
      <div class="tag">second-pass</div>
      <h1>EchoMemory Graph Second-Pass Contract Ablation</h1>
      <p>
        This is a deliberately narrow experiment. It does <b>not</b> claim that one extra graph pass solves all QA problems.
        It tests one sharper question:
        <b>when the planner says a temporal query should have both chronology evidence and event support, does a self-check-driven graph second-pass actually complete that evidence contract?</b>
      </p>
      <div class="kpis">
        <div class="kpi"><span class="label">Cases</span><span class="value">{summary['cases']}</span></div>
        <div class="kpi"><span class="label">One-pass Contract OK</span><span class="value">{summary['one_pass_contract_ok']}</span></div>
        <div class="kpi"><span class="label">Graph Second-pass Contract OK</span><span class="value">{summary['graph_second_pass_contract_ok']}</span></div>
        <div class="kpi"><span class="label">Improved Cases</span><span class="value">{len(summary['improved_contract_cases'])}</span></div>
      </div>
    </div>

    <div class="section">
      <h2>Interpretation</h2>
      <div class="grid">
        <div class="card">
          <h3>What is being measured</h3>
          <p>
            We judge each run by two criteria:
            keyword correctness and <b>layer-contract completeness</b>.
            For example, temporal queries in this nano planner want both
            <code>temporal_tree</code> and <code>event</code>.
          </p>
        </div>
        <div class="card">
          <h3>Why this is relevant</h3>
          <p>
            This mirrors the current main-code direction:
            self-check first diagnoses evidence weakness,
            then a conservative graph second-pass tries to补足 missing support.
          </p>
        </div>
        <div class="card">
          <h3>What this does not claim</h3>
          <p>
            It is not a general benchmark.
            It is a method ablation about evidence-shape repair,
            closer to a paper figure than to a product QA leaderboard.
          </p>
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
            <th>Required Layers</th>
            <th>One-pass</th>
            <th>Graph Second-pass</th>
            <th>Triggered</th>
            <th>Note</th>
          </tr>
        </thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>

    <div class="section">
      <h2>Summary</h2>
      <p>
        Keyword coverage stayed at <b>{summary['one_pass_keyword_ok']} / {summary['cases']}</b> for one-pass and
        <b>{summary['graph_second_pass_keyword_ok']} / {summary['cases']}</b> after graph second-pass.
        The bigger change is contract completeness:
        <b>{summary['one_pass_contract_ok']} / {summary['cases']}</b> ->
        <b>{summary['graph_second_pass_contract_ok']} / {summary['cases']}</b>.
      </p>
      <p>
        Improved cases: <code>{esc(', '.join(summary['improved_contract_cases']) or 'none')}</code><br/>
        Triggered second-pass cases: <code>{esc(', '.join(summary['second_pass_triggered_cases']) or 'none')}</code>
      </p>
    </div>

    <div class="section">
      <h2>Why this matters for the paper line</h2>
      <div class="grid">
        <div class="card">
          <h3>Self-RAG flavor</h3>
          <p>
            The point is not just “retrieve once and answer”.
            It is closer to the Self-RAG idea that evidence quality should decide whether the system keeps searching.
          </p>
        </div>
        <div class="card">
          <h3>MemoRAG / coarse-to-fine</h3>
          <p>
            One-pass tree retrieval is the coarse stage.
            Graph second-pass is the fine support stage that only runs when evidence shape is incomplete.
          </p>
        </div>
        <div class="card">
          <h3>LongMemEval-style auditability</h3>
          <p>
            This experiment explicitly measures evidence completeness, not just whether a surface keyword shows up.
            That is closer to memory-system evaluation than a raw QA scoreboard.
          </p>
        </div>
      </div>
    </div>

    <div class="section">
      <h2>Example Case</h2>
      <pre>{esc(json.dumps(sample_case, ensure_ascii=False, indent=2))}</pre>
    </div>
  </div>
</body>
</html>
"""


def main() -> None:
    payload = run_eval()
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_text = render_html(payload)
    OUT_HTML.write_text(html_text, encoding="utf-8")
    PUBLIC_HTML.write_text(html_text, encoding="utf-8")
    print(json.dumps({"ok": True, "json": str(OUT_JSON), "html": str(OUT_HTML), "public_html": str(PUBLIC_HTML)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
