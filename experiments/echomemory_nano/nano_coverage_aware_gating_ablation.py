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
OUT_JSON = ROOT / "nano_coverage_aware_gating_ablation_results.json"
OUT_HTML = ROOT / "nano_coverage_aware_gating_ablation_report.html"
PUBLIC_HTML = Path("/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_coverage_aware_gating_ablation_20260615.html")


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MOD = load_module(BASE_PATH, "echomemory_nano_modular_for_gating_ablation")


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
    blob = "\n".join(str(hit.get("content", "")) for hit in hits[:5]).lower()
    return all(keyword.lower() in blob for keyword in expected_keywords)


def contract_summary(plan: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    required_layers = list(plan.get("must_have_layers", []))
    present_layers = []
    seen = set()
    for hit in hits[:6]:
        layer = str(hit.get("layer", "") or "")
        if layer and layer not in seen:
            seen.add(layer)
            present_layers.append(layer)
    matched_layers = [layer for layer in required_layers if layer in seen]
    missing_layers = [layer for layer in required_layers if layer not in seen]
    coverage_ratio = len(matched_layers) / max(len(required_layers), 1)
    return {
        "required_layers": required_layers,
        "present_layers": present_layers,
        "matched_layers": matched_layers,
        "missing_layers": missing_layers,
        "coverage_ratio": round(coverage_ratio, 3),
        "contract_ok": coverage_ratio >= 1.0,
    }


def confidence_only_search(system: dict[str, Any], query: str, threshold: float = 0.55) -> dict[str, Any]:
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
            "mode": "confidence_only",
            "decision": {"answer": "unknown", "confidence": 0.0, "note": "qa_ready=false"},
            "readiness": readiness,
        }
    primary_hits = orchestrator._read(plan.primary_reader, query)
    merged = orchestrator.fusion.merge([primary_hits])
    top_score = max((hit.score for hit in merged), default=0.0)
    expanded = False
    if top_score < threshold:
        support_groups = [orchestrator._read(reader, query) for reader in plan.supporting_readers]
        merged = orchestrator.fusion.merge([primary_hits] + support_groups)
        expanded = True
    need_expand, confidence, note = orchestrator.self_check.assess(plan, merged)
    answer = MOD.compose_answer(query, plan, merged, confidence)
    return {
        "query": query,
        "intent": MOD.asdict(intent),
        "plan": MOD.asdict(plan),
        "hits": [MOD.asdict(hit) for hit in merged[:6]],
        "all_hits": [MOD.asdict(hit) for hit in merged],
        "mode": "confidence_only",
        "readiness": readiness,
        "self_check": {
            "need_expand": need_expand,
            "confidence": round(confidence, 3),
            "note": note,
        },
        "decision": {
            "answer": answer,
            "confidence": round(top_score, 3),
            "expanded": expanded,
            "note": "Stop if primary hit confidence is already high enough.",
        },
    }


def coverage_aware_search(system: dict[str, Any], query: str) -> dict[str, Any]:
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
            "all_hits": [],
            "decision": {"answer": "unknown", "confidence": 0.0, "note": "qa_ready=false"},
            "readiness": readiness,
        }
    primary_hits = orchestrator._read(plan.primary_reader, query)
    merged = orchestrator.fusion.merge([primary_hits])
    need_expand, confidence, note = orchestrator.self_check.assess(plan, merged)
    expanded = False
    if need_expand:
        support_groups = [orchestrator._read(reader, query) for reader in plan.supporting_readers]
        merged = orchestrator.fusion.merge([primary_hits] + support_groups)
        expanded = True
        _, confidence, note = orchestrator.self_check.assess(plan, merged)
    answer = MOD.compose_answer(query, plan, merged, confidence)
    return {
        "query": query,
        "intent": MOD.asdict(intent),
        "plan": MOD.asdict(plan),
        "hits": [MOD.asdict(hit) for hit in merged[:6]],
        "all_hits": [MOD.asdict(hit) for hit in merged],
        "decision": {
            "answer": answer,
            "confidence": round(confidence, 3),
            "should_answer": answer != "unknown",
            "note": note,
            "expanded_support": expanded,
        },
        "readiness": readiness,
    }


def run_eval() -> dict[str, Any]:
    system = build_system()
    cases = [
        EvalCase(
            case_id="temporal_leave_date",
            query="When did Gina leave Figma?",
            expected_keywords=["2024-06-01", "left Figma"],
            note="Tree primary can be high-confidence, but contract still wants event support.",
        ),
        EvalCase(
            case_id="temporal_join_date",
            query="When did Gina join Figma?",
            expected_keywords=["2024-01-05", "joined Figma"],
            note="Another chronology-first query that should not stop at a tree-only surface hit.",
        ),
        EvalCase(
            case_id="relational_helper",
            query="Who helped Gina with the Lisbon visa checklist?",
            expected_keywords=["Nora helped Gina", "visa checklist"],
            note="Graph route usually gets relation context first, but fact grounding still matters.",
        ),
        EvalCase(
            case_id="temporal_relational_plan",
            query="What did Gina plan to do after leaving Figma?",
            expected_keywords=["plans to move to Lisbon", "leaving Figma"],
            note="Mixed query family: relation/path support should still be grounded by fact evidence.",
        ),
        EvalCase(
            case_id="visual_lease",
            query="What did the screenshot of the lease contract show?",
            expected_keywords=["Rua Augusta 14", "Lease Agreement"],
            note="Image evidence alone should not satisfy the full contract without fact support.",
        ),
        EvalCase(
            case_id="visual_arrival",
            query="What did the arrival photo show?",
            expected_keywords=["Santa Apolonia Platform 4"],
            note="Visual family again tests whether confidence-only stopping over-trusts image evidence.",
        ),
    ]

    case_results: list[dict[str, Any]] = []
    confidence_contract_ok = 0
    coverage_contract_ok = 0
    improved_contract_cases: list[str] = []
    confidence_keyword_ok = 0
    coverage_keyword_ok = 0

    for case in cases:
        conf_run = confidence_only_search(system, case.query)
        cov_run = coverage_aware_search(system, case.query)
        conf_eval = contract_summary(conf_run["plan"], conf_run.get("all_hits", conf_run["hits"]))
        cov_eval = contract_summary(cov_run["plan"], cov_run.get("all_hits", cov_run["hits"]))
        conf_kw = keyword_ok(conf_run.get("all_hits", conf_run["hits"]), case.expected_keywords)
        cov_kw = keyword_ok(cov_run.get("all_hits", cov_run["hits"]), case.expected_keywords)
        confidence_keyword_ok += int(conf_kw)
        coverage_keyword_ok += int(cov_kw)
        confidence_contract_ok += int(conf_kw and conf_eval["contract_ok"])
        coverage_contract_ok += int(cov_kw and cov_eval["contract_ok"])
        if (not (conf_kw and conf_eval["contract_ok"])) and (cov_kw and cov_eval["contract_ok"]):
            improved_contract_cases.append(case.case_id)
        case_results.append(
            {
                "case_id": case.case_id,
                "query": case.query,
                "note": case.note,
                "confidence_only": conf_run,
                "confidence_only_eval": {**conf_eval, "keyword_ok": conf_kw},
                "coverage_aware": cov_run,
                "coverage_aware_eval": {**cov_eval, "keyword_ok": cov_kw},
            }
        )

    return {
        "experiment": "coverage_aware_gating_ablation",
        "cases": len(cases),
        "confidence_only_keyword_ok": confidence_keyword_ok,
        "coverage_aware_keyword_ok": coverage_keyword_ok,
        "confidence_only_contract_ok": confidence_contract_ok,
        "coverage_aware_contract_ok": coverage_contract_ok,
        "improved_contract_cases": improved_contract_cases,
        "results": case_results,
        "summary": {
            "takeaway": (
                "Confidence-only gating often preserves surface-answer relevance, "
                "but coverage-aware gating is more reliable at completing the planned evidence contract."
            )
        },
    }


def render_html(data: dict[str, Any]) -> str:
    rows = []
    for item in data["results"]:
        conf = item["confidence_only_eval"]
        cov = item["coverage_aware_eval"]
        rows.append(
            f"""
            <tr>
              <td><b>{html.escape(item['case_id'])}</b><br>{html.escape(item['query'])}</td>
              <td>{html.escape(str(conf['keyword_ok']))}<br>coverage={conf['coverage_ratio']}<br>missing={html.escape(', '.join(conf['missing_layers']) or '-')}</td>
              <td>{html.escape(str(cov['keyword_ok']))}<br>coverage={cov['coverage_ratio']}<br>missing={html.escape(', '.join(cov['missing_layers']) or '-')}</td>
              <td>{html.escape(item['note'])}</td>
            </tr>
            """
        )
    improved = "".join(f"<li>{html.escape(case_id)}</li>" for case_id in data["improved_contract_cases"])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>EchoMemory Nano Coverage-Aware Gating Ablation</title>
  <style>
    :root {{
      --bg: #f5f7fb; --panel: #fff; --line: #d9e2ec; --text: #0f172a; --muted: #475569; --blue: #2563eb;
      --green: #166534; --green-soft: #dcfce7; --amber: #92400e; --amber-soft: #fef3c7;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; }}
    .wrap {{ max-width: 1100px; margin: 0 auto; padding: 24px; }}
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
    table {{ width: 100%; border-collapse: collapse; min-width: 860px; background: #fff; }}
    th, td {{ text-align: left; padding: 12px; border-bottom: 1px solid var(--line); vertical-align: top; }}
    th {{ background: #f8fafc; color: #334155; }}
    tr:last-child td {{ border-bottom: 0; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: #eef2f7; color: #0f172a; border-radius: 6px; padding: 1px 6px; font-size: 12px; }}
    @media (max-width: 960px) {{ .card, .full {{ grid-column: span 12; }} .wrap {{ padding: 14px; }} .hero {{ padding: 18px; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory Nano: Coverage-Aware Gating Ablation</h1>
      <p>
        这个小实验专门测一个很系统层、也很泛化的问题：
        <strong>当 primary backbone 已经给出高分命中时，系统要不要因为“分数高”就提早停？</strong>
        我们对比两种策略：
        <code>confidence-only gating</code> 和 <code>coverage-aware gating</code>。
      </p>
      <div class="meta">
        <span class="pill">cases={data['cases']}</span>
        <span class="pill">confidence-only contract ok={data['confidence_only_contract_ok']}</span>
        <span class="pill">coverage-aware contract ok={data['coverage_aware_contract_ok']}</span>
      </div>
    </section>

    <section class="section">
      <div class="section-header"><h2>结果概览</h2></div>
      <div class="section-body">
        <div class="grid">
          <div class="card">
            <span class="tag warn">confidence-only</span>
            <h3>关键词命中</h3>
            <p><b>{data['confidence_only_keyword_ok']} / {data['cases']}</b></p>
            <p>表面答案相关性通常还行，但更容易在 evidence contract 没补齐时提前停。</p>
          </div>
          <div class="card">
            <span class="tag ok">coverage-aware</span>
            <h3>关键词命中</h3>
            <p><b>{data['coverage_aware_keyword_ok']} / {data['cases']}</b></p>
            <p>表面答案不降，同时更稳定地补齐计划要求的证据层。</p>
          </div>
          <div class="card">
            <span class="tag ok">contract gain</span>
            <h3>Contract 完整通过</h3>
            <p><b>{data['confidence_only_contract_ok']} / {data['cases']}</b> -> <b>{data['coverage_aware_contract_ok']} / {data['cases']}</b></p>
            <p>这更能说明系统是不是“真的按计划拿到了该拿的证据”。</p>
          </div>
          <div class="card full">
            <h3>改进案例</h3>
            <ul>{improved or '<li>none</li>'}</ul>
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
                <th>Confidence-Only</th>
                <th>Coverage-Aware</th>
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
    data = run_eval()
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    html_text = render_html(data)
    OUT_HTML.write_text(html_text, encoding="utf-8")
    PUBLIC_HTML.write_text(html_text, encoding="utf-8")
    print(json.dumps({
        "json": str(OUT_JSON),
        "html": str(OUT_HTML),
        "public_html": str(PUBLIC_HTML),
        "confidence_only_contract_ok": data["confidence_only_contract_ok"],
        "coverage_aware_contract_ok": data["coverage_aware_contract_ok"],
        "improved_contract_cases": data["improved_contract_cases"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
