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
OUT_JSON = ROOT / "nano_type_aware_second_pass_ablation_results.json"
OUT_HTML = ROOT / "nano_type_aware_second_pass_ablation_report.html"
PUBLIC_HTML = Path("/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_type_aware_second_pass_ablation_20260615.html")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MOD = load_module(BASE_PATH, "echomemory_nano_modular_type_aware_second_pass")


@dataclass
class EvalCase:
    case_id: str
    query: str
    expected_keywords: list[str]
    note: str


def build_system() -> dict[str, Any]:
    stream = MOD.SessionStream()
    stream.append("user", "Gina joined Figma on 2024-01-05.", "2024-01-06T09:00:00+00:00")
    stream.append("user", "Gina married Alex on 2024-03-10.", "2024-03-11T09:00:00+00:00")
    stream.append("user", "Nora helped Gina prepare a Lisbon visa checklist on 2024-04-02.", "2024-04-03T09:00:00+00:00")
    stream.append("user", "Gina signed a Lisbon lease on 2024-05-08.", "2024-05-09T09:00:00+00:00")
    stream.append("user", "Gina left Figma on 2024-06-01.", "2024-06-02T09:00:00+00:00")
    stream.append("user", "Gina plans to move to Lisbon after leaving Figma.", "2024-06-03T09:00:00+00:00")
    stream.append("user", "Screenshot of lease contract showed Rua Augusta 14 Lisbon Lease Agreement.", "2024-05-09T11:00:00+00:00")
    stream.append("user", "Arrival photo showed Santa Apolonia Platform 4.", "2024-06-10T08:00:00+00:00")
    atoms = MOD.AtomExtractor().extract(stream.messages)
    stream.readiness.atoms_ready = True
    tree_blocks = MOD.TemporalTreeProjector().project(atoms)
    nodes, edges = MOD.GraphProjector().project(atoms)
    stream.readiness.tree_ready = True
    stream.readiness.graph_ready = True
    stream.readiness.qa_ready = True
    orchestrator = MOD.SearchOrchestrator(stream, atoms, tree_blocks, nodes, edges)
    return {
        "stream": stream,
        "atoms": atoms,
        "tree_blocks": tree_blocks,
        "nodes": nodes,
        "edges": edges,
        "orchestrator": orchestrator,
    }


def keyword_ok(hits: list[dict[str, Any]], expected_keywords: list[str]) -> bool:
    blob = "\n".join(str(hit.get("content", "")) for hit in hits[:8]).lower()
    return all(keyword.lower() in blob for keyword in expected_keywords)


def required_layers_for_plan(plan: dict[str, Any]) -> list[str]:
    required = list(plan.get("must_have_layers", []))
    if plan.get("family") == "temporal_relational" and "temporal_tree" not in required:
        required.append("temporal_tree")
    return required


def summarize_contract(plan: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    required = required_layers_for_plan(plan)
    present = []
    seen = set()
    for hit in hits:
        layer = str(hit.get("layer", "") or "")
        if layer and layer not in seen:
            seen.add(layer)
            present.append(layer)
    matched = [layer for layer in required if layer in seen]
    missing = [layer for layer in required if layer not in seen]
    ratio = len(matched) / max(len(required), 1)
    return {
        "required_layers": required,
        "present_layers": present,
        "matched_layers": matched,
        "missing_layers": missing,
        "coverage_ratio": round(ratio, 3),
        "contract_ok": ratio >= 1.0,
    }


def base_context(orchestrator: Any, query: str) -> tuple[Any, Any, list[Any], dict[str, Any]]:
    intent = orchestrator.intent_classifier.classify(query)
    plan = orchestrator.planner.plan(query, intent)
    primary_hits = orchestrator._read(plan.primary_reader, query)
    merged = orchestrator.fusion.merge([primary_hits])
    return intent, plan, merged, MOD.asdict(orchestrator.stream.readiness)


def pack_result(mode: str, intent: Any, plan: Any, merged: list[Any], readiness: dict[str, Any], note: str, second_pass_sources: list[str]) -> dict[str, Any]:
    need_expand, confidence, self_check_note = orchestrator_self_check(intent=None, plan=plan, hits=merged)
    answer = MOD.compose_answer("", plan, merged, confidence)
    return {
        "mode": mode,
        "intent": MOD.asdict(intent),
        "plan": MOD.asdict(plan),
        "hits": [MOD.asdict(hit) for hit in merged[:8]],
        "all_hits": [MOD.asdict(hit) for hit in merged],
        "readiness": readiness,
        "self_check": {
            "need_expand": need_expand,
            "confidence": round(confidence, 3),
            "note": self_check_note,
            "second_pass_sources": second_pass_sources,
        },
        "decision": {
            "answer": answer,
            "confidence": round(confidence, 3),
            "note": note,
        },
    }


def orchestrator_self_check(intent: Any, plan: Any, hits: list[Any]) -> tuple[bool, float, str]:
    layers = {h.layer for h in hits[:8]}
    required = required_layers_for_plan(MOD.asdict(plan))
    matched = len([layer for layer in required if layer in layers])
    confidence = matched / max(len(required), 1)
    if confidence >= 1.0:
        return False, confidence, "Evidence contract already complete."
    return True, confidence, "Evidence contract incomplete; supporting evidence should expand."


def one_pass_search(system: dict[str, Any], query: str) -> dict[str, Any]:
    orchestrator = system["orchestrator"]
    intent, plan, merged, readiness = base_context(orchestrator, query)
    return pack_result("one_pass", intent, plan, merged, readiness, "Primary reader only.", [])


def graph_only_second_pass_search(system: dict[str, Any], query: str) -> dict[str, Any]:
    orchestrator = system["orchestrator"]
    intent, plan, merged, readiness = base_context(orchestrator, query)
    sources: list[str] = []
    need_expand, _, _ = orchestrator_self_check(intent, plan, merged)
    if need_expand and plan.primary_reader != "graph" and "graph" in plan.supporting_readers:
        merged = orchestrator.fusion.merge([merged, orchestrator._read("graph", query)])
        sources.append("graph")
    return pack_result("graph_only_second_pass", intent, plan, merged, readiness, "Only graph can be added in second pass.", sources)


def reader_for_missing(layer: str) -> str | None:
    if layer == "temporal_tree":
        return "tree"
    if layer in {"event", "entity", "image_evidence"}:
        return "graph"
    if layer == "fact":
        return "atom"
    return None


def type_aware_second_pass_search(system: dict[str, Any], query: str) -> dict[str, Any]:
    orchestrator = system["orchestrator"]
    intent, plan, merged, readiness = base_context(orchestrator, query)
    _, _, _ = orchestrator_self_check(intent, plan, merged)
    contract = summarize_contract(MOD.asdict(plan), [MOD.asdict(hit) for hit in merged])
    sources: list[str] = []
    groups = [merged]
    for missing in contract["missing_layers"]:
        reader = reader_for_missing(missing)
        if reader is None:
            continue
        if reader in sources:
            continue
        groups.append(orchestrator._read(reader, query))
        sources.append(reader)
    merged = orchestrator.fusion.merge(groups)
    return pack_result("type_aware_second_pass", intent, plan, merged, readiness, "Second pass probes readers based on missing evidence types.", sources)


def run_eval() -> dict[str, Any]:
    system = build_system()
    cases = [
        EvalCase(
            case_id="temporal_join_date",
            query="When did Gina join Figma?",
            expected_keywords=["2024-01-05", "joined Figma"],
            note="Temporal query starts tree-first and should add event support if contract is incomplete.",
        ),
        EvalCase(
            case_id="temporal_leave_date",
            query="When did Gina leave Figma?",
            expected_keywords=["2024-06-01", "left Figma"],
            note="Another chronology-first question where event support matters.",
        ),
        EvalCase(
            case_id="temporal_relational_plan",
            query="What did Gina plan to do after leaving Figma?",
            expected_keywords=["plans to move to Lisbon", "leaving Figma"],
            note="Ordered relation query should not stop at graph-only support; it should also expose chronology support.",
        ),
        EvalCase(
            case_id="temporal_relational_after",
            query="What happened after Gina left Figma?",
            expected_keywords=["plans to move to Lisbon", "left Figma"],
            note="Another temporal-relational case where missing temporal_tree should trigger tree support.",
        ),
        EvalCase(
            case_id="visual_lease_control",
            query="What did lease_screenshot show?",
            expected_keywords=["Rua Augusta 14"],
            note="Control case: visual primary evidence should already satisfy image+fact contract.",
        ),
    ]

    results: list[dict[str, Any]] = []
    summary = {
        "cases": len(cases),
        "one_pass_contract_ok": 0,
        "graph_only_contract_ok": 0,
        "type_aware_contract_ok": 0,
        "one_pass_keyword_ok": 0,
        "graph_only_keyword_ok": 0,
        "type_aware_keyword_ok": 0,
        "type_aware_improved_over_graph_only": [],
        "graph_only_improved_over_one_pass": [],
    }

    for case in cases:
        one_run = one_pass_search(system, case.query)
        graph_run = graph_only_second_pass_search(system, case.query)
        type_run = type_aware_second_pass_search(system, case.query)

        one_eval = summarize_contract(one_run["plan"], one_run["all_hits"])
        graph_eval = summarize_contract(graph_run["plan"], graph_run["all_hits"])
        type_eval = summarize_contract(type_run["plan"], type_run["all_hits"])
        one_kw = keyword_ok(one_run["all_hits"], case.expected_keywords)
        graph_kw = keyword_ok(graph_run["all_hits"], case.expected_keywords)
        type_kw = keyword_ok(type_run["all_hits"], case.expected_keywords)

        summary["one_pass_keyword_ok"] += int(one_kw)
        summary["graph_only_keyword_ok"] += int(graph_kw)
        summary["type_aware_keyword_ok"] += int(type_kw)
        summary["one_pass_contract_ok"] += int(one_kw and one_eval["contract_ok"])
        summary["graph_only_contract_ok"] += int(graph_kw and graph_eval["contract_ok"])
        summary["type_aware_contract_ok"] += int(type_kw and type_eval["contract_ok"])

        if (not (one_kw and one_eval["contract_ok"])) and (graph_kw and graph_eval["contract_ok"]):
            summary["graph_only_improved_over_one_pass"].append(case.case_id)
        if (not (graph_kw and graph_eval["contract_ok"])) and (type_kw and type_eval["contract_ok"]):
            summary["type_aware_improved_over_graph_only"].append(case.case_id)

        results.append({
            "case_id": case.case_id,
            "query": case.query,
            "note": case.note,
            "one_pass": one_run,
            "one_pass_eval": {**one_eval, "keyword_ok": one_kw},
            "graph_only_second_pass": graph_run,
            "graph_only_second_pass_eval": {**graph_eval, "keyword_ok": graph_kw},
            "type_aware_second_pass": type_run,
            "type_aware_second_pass_eval": {**type_eval, "keyword_ok": type_kw},
        })

    return {"summary": summary, "results": results}


def render_html(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    rows = []
    for item in payload["results"]:
        one = item["one_pass_eval"]
        graph = item["graph_only_second_pass_eval"]
        typev = item["type_aware_second_pass_eval"]
        rows.append(
            f"""
            <tr>
              <td><b>{html.escape(item['case_id'])}</b><br>{html.escape(item['query'])}</td>
              <td>{one['keyword_ok']}<br>coverage={one['coverage_ratio']}<br>missing={html.escape(', '.join(one['missing_layers']) or '-')}</td>
              <td>{graph['keyword_ok']}<br>coverage={graph['coverage_ratio']}<br>missing={html.escape(', '.join(graph['missing_layers']) or '-')}</td>
              <td>{typev['keyword_ok']}<br>coverage={typev['coverage_ratio']}<br>missing={html.escape(', '.join(typev['missing_layers']) or '-')}</td>
              <td>{html.escape(item['note'])}</td>
            </tr>
            """
        )
    graph_gain = "".join(f"<li>{html.escape(x)}</li>" for x in summary["graph_only_improved_over_one_pass"]) or "<li>none</li>"
    type_gain = "".join(f"<li>{html.escape(x)}</li>" for x in summary["type_aware_improved_over_graph_only"]) or "<li>none</li>"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>EchoMemory Nano Type-Aware Second Pass Ablation</title>
  <style>
    :root {{
      --bg: #f5f7fb; --panel: #fff; --line: #d9e2ec; --text: #0f172a; --muted: #475569;
      --green: #166534; --green-soft: #dcfce7; --amber: #92400e; --amber-soft: #fef3c7;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; }}
    .wrap {{ max-width: 1120px; margin: 0 auto; padding: 24px; }}
    .hero, .section {{ background: var(--panel); border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }}
    .hero {{ padding: 24px 26px; background: linear-gradient(135deg, #eef4ff, #ffffff 60%); }}
    .section {{ margin-top: 18px; }}
    .section-header {{ padding: 16px 18px; border-bottom: 1px solid var(--line); }}
    .section-body {{ padding: 16px 18px 18px; }}
    h1 {{ margin: 0 0 10px; font-size: 28px; }}
    h2 {{ margin: 0; font-size: 20px; }}
    p, li, td, th {{ color: var(--muted); font-size: 14px; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }}
    .pill {{ display: inline-flex; align-items: center; border: 1px solid var(--line); border-radius: 999px; padding: 6px 10px; font-size: 12px; background: #fff; color: var(--muted); }}
    .grid {{ display: grid; grid-template-columns: repeat(12, minmax(0,1fr)); gap: 14px; }}
    .card {{ grid-column: span 4; background: #f8fafc; border: 1px solid var(--line); border-radius: 12px; padding: 14px; }}
    .full {{ grid-column: span 12; }}
    .tag {{ display: inline-flex; border-radius: 999px; padding: 4px 8px; font-size: 12px; font-weight: 600; }}
    .ok {{ background: var(--green-soft); color: var(--green); }}
    .warn {{ background: var(--amber-soft); color: var(--amber); }}
    .table-wrap {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 12px; }}
    table {{ width: 100%; min-width: 960px; border-collapse: collapse; background: #fff; }}
    th, td {{ text-align: left; vertical-align: top; padding: 12px; border-bottom: 1px solid var(--line); }}
    th {{ background: #f8fafc; color: #334155; }}
    tr:last-child td {{ border-bottom: 0; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: #eef2f7; color: #0f172a; border-radius: 6px; padding: 1px 6px; font-size: 12px; }}
    @media (max-width: 960px) {{ .card, .full {{ grid-column: span 12; }} .wrap {{ padding: 14px; }} .hero {{ padding: 18px; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory Nano: Type-Aware Second Pass Ablation</h1>
      <p>
        这个实验比较三种 retrieval policy：
        <code>one_pass</code>、
        <code>graph-only second pass</code>、
        <code>type-aware second pass</code>。
        核心问题不是“多做一次补检会不会更好”，而是
        <strong>当 evidence contract 缺的是不同类型的证据时，系统能不能补对 reader</strong>。
      </p>
      <div class="meta">
        <span class="pill">cases={summary['cases']}</span>
        <span class="pill">one_pass contract ok={summary['one_pass_contract_ok']}</span>
        <span class="pill">graph-only contract ok={summary['graph_only_contract_ok']}</span>
        <span class="pill">type-aware contract ok={summary['type_aware_contract_ok']}</span>
      </div>
    </section>

    <section class="section">
      <div class="section-header"><h2>结果概览</h2></div>
      <div class="section-body">
        <div class="grid">
          <div class="card">
            <span class="tag warn">one pass</span>
            <p><b>{summary['one_pass_contract_ok']} / {summary['cases']}</b></p>
            <p>只看 primary reader，最容易停在结构不完整的证据上。</p>
          </div>
          <div class="card">
            <span class="tag warn">graph-only second pass</span>
            <p><b>{summary['graph_only_contract_ok']} / {summary['cases']}</b></p>
            <p>能修 tree-first 时间题，但在 graph-primary 的 ordered-relation 题上会卡住。</p>
          </div>
          <div class="card">
            <span class="tag ok">type-aware second pass</span>
            <p><b>{summary['type_aware_contract_ok']} / {summary['cases']}</b></p>
            <p>根据 <code>missing_layers</code> 选择 tree / atom / graph，更接近真正的 contract-driven policy。</p>
          </div>
          <div class="card full">
            <h3>graph-only 相比 one-pass 的改进</h3>
            <ul>{graph_gain}</ul>
          </div>
          <div class="card full">
            <h3>type-aware 相比 graph-only 的改进</h3>
            <ul>{type_gain}</ul>
          </div>
        </div>
      </div>
    </section>

    <section class="section">
      <div class="section-header"><h2>逐题对比</h2></div>
      <div class="section-body">
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Case</th>
                <th>One Pass</th>
                <th>Graph-Only</th>
                <th>Type-Aware</th>
                <th>Why It Matters</th>
              </tr>
            </thead>
            <tbody>
              {''.join(rows)}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  </div>
</body>
</html>"""


def main() -> None:
    payload = run_eval()
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html_text = render_html(payload)
    OUT_HTML.write_text(html_text, encoding="utf-8")
    PUBLIC_HTML.write_text(html_text, encoding="utf-8")
    print(json.dumps({
        "json": str(OUT_JSON),
        "html": str(OUT_HTML),
        "public_html": str(PUBLIC_HTML),
        "one_pass_contract_ok": payload["summary"]["one_pass_contract_ok"],
        "graph_only_contract_ok": payload["summary"]["graph_only_contract_ok"],
        "type_aware_contract_ok": payload["summary"]["type_aware_contract_ok"],
        "type_aware_improved_over_graph_only": payload["summary"]["type_aware_improved_over_graph_only"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
