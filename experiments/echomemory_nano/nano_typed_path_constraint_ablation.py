#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_typed_path_constraint_ablation_results.json"
OUT_HTML = ROOT / "nano_typed_path_constraint_ablation_report.html"
PUBLIC_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_typed_path_constraint_ablation_20260617.html"
)


@dataclass(frozen=True)
class Node:
    node_id: str
    node_type: str
    label: str
    salience: float


@dataclass(frozen=True)
class Edge:
    source_id: str
    relation_type: str
    target_id: str


@dataclass(frozen=True)
class Case:
    case_id: str
    query: str
    left_entity: str
    right_entity: str
    expected_bridge: str
    required_bridge_types: tuple[str, ...]
    required_relation_types: tuple[str, ...]
    note: str


def esc(value: Any) -> str:
    return html.escape(str(value))


def build_graph() -> tuple[dict[str, Node], list[Edge]]:
    nodes = {
        "maya": Node("maya", "person", "Maya", 0.42),
        "leo": Node("leo", "person", "Leo", 0.44),
        "nia": Node("nia", "person", "Nia", 0.61),
        "studiox": Node("studiox", "company", "StudioX", 0.92),
        "aria": Node("aria", "person", "Aria", 0.40),
        "ivy": Node("ivy", "person", "Lawyer Ivy", 0.43),
        "priya": Node("priya", "person", "Priya", 0.59),
        "slack": Node("slack", "channel", "Residency Slack", 0.88),
        "elena": Node("elena", "person", "Elena", 0.45),
        "chen": Node("chen", "person", "Investor Chen", 0.47),
        "marco": Node("marco", "person", "Marco", 0.63),
        "hub": Node("hub", "community", "Startup Hub", 0.91),
    }
    edges = [
        Edge("nia", "introduced", "maya"),
        Edge("nia", "introduced", "leo"),
        Edge("studiox", "employs", "maya"),
        Edge("studiox", "employs", "leo"),
        Edge("priya", "referred", "aria"),
        Edge("priya", "referred", "ivy"),
        Edge("slack", "discussed", "aria"),
        Edge("slack", "discussed", "ivy"),
        Edge("marco", "connected", "elena"),
        Edge("marco", "connected", "chen"),
        Edge("hub", "hosts", "elena"),
        Edge("hub", "hosts", "chen"),
    ]
    return nodes, edges


def build_cases() -> list[Case]:
    return [
        Case(
            case_id="introducer_maya_leo",
            query="Who introduced Maya to Leo?",
            left_entity="maya",
            right_entity="leo",
            expected_bridge="nia",
            required_bridge_types=("person",),
            required_relation_types=("introduced",),
            note="Shared company is a high-salience distractor; introducer semantics should still pick a person bridge with introduced edges.",
        ),
        Case(
            case_id="referrer_aria_ivy",
            query="Who referred Aria to Lawyer Ivy?",
            left_entity="aria",
            right_entity="ivy",
            expected_bridge="priya",
            required_bridge_types=("person",),
            required_relation_types=("referred",),
            note="A busy channel links both endpoints, but relation semantics ask for a referrer, not a co-mentioned venue.",
        ),
        Case(
            case_id="connector_elena_chen",
            query="Who connected Elena with Investor Chen?",
            left_entity="elena",
            right_entity="chen",
            expected_bridge="marco",
            required_bridge_types=("person",),
            required_relation_types=("connected",),
            note="Community membership creates an easy false path; typed constraints should keep the bridge role human and relation-specific.",
        ),
    ]


def shared_bridge_candidates(
    nodes: dict[str, Node],
    edges: list[Edge],
    *,
    left_entity: str,
    right_entity: str,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    by_source: dict[str, list[Edge]] = {}
    for edge in edges:
        by_source.setdefault(edge.source_id, []).append(edge)
    for bridge_id, outgoing in by_source.items():
        to_left = [edge for edge in outgoing if edge.target_id == left_entity]
        to_right = [edge for edge in outgoing if edge.target_id == right_entity]
        if not to_left or not to_right:
            continue
        node = nodes[bridge_id]
        candidates.append(
            {
                "bridge_id": bridge_id,
                "bridge_label": node.label,
                "bridge_type": node.node_type,
                "salience": node.salience,
                "path": [
                    f"{bridge_id}-[{to_left[0].relation_type}]->{left_entity}",
                    f"{bridge_id}-[{to_right[0].relation_type}]->{right_entity}",
                ],
                "relation_types": sorted(
                    {edge.relation_type for edge in (to_left + to_right)}
                ),
            }
        )
    candidates.sort(key=lambda item: item["salience"], reverse=True)
    return candidates


def run_naive_shared_neighbor(case: Case, nodes: dict[str, Node], edges: list[Edge]) -> dict[str, Any]:
    candidates = shared_bridge_candidates(
        nodes,
        edges,
        left_entity=case.left_entity,
        right_entity=case.right_entity,
    )
    chosen = candidates[0] if candidates else None
    return {
        "mode": "naive_shared_neighbor",
        "answer_bridge": chosen["bridge_id"] if chosen else "unknown",
        "answer_label": chosen["bridge_label"] if chosen else "unknown",
        "trace": chosen["path"] if chosen else [],
        "candidates": candidates,
    }


def run_typed_constraint(case: Case, nodes: dict[str, Node], edges: list[Edge]) -> dict[str, Any]:
    candidates = shared_bridge_candidates(
        nodes,
        edges,
        left_entity=case.left_entity,
        right_entity=case.right_entity,
    )
    filtered = [
        item
        for item in candidates
        if item["bridge_type"] in case.required_bridge_types
        and all(rel in case.required_relation_types for rel in item["relation_types"])
    ]
    chosen = filtered[0] if filtered else None
    return {
        "mode": "typed_path_constraint",
        "answer_bridge": chosen["bridge_id"] if chosen else "unknown",
        "answer_label": chosen["bridge_label"] if chosen else "unknown",
        "trace": chosen["path"] if chosen else [],
        "candidates": candidates,
        "filtered_candidates": filtered,
        "required_bridge_types": list(case.required_bridge_types),
        "required_relation_types": list(case.required_relation_types),
    }


def judge(case: Case, run: dict[str, Any]) -> dict[str, Any]:
    ok = run["answer_bridge"] == case.expected_bridge
    return {
        "expected_bridge": case.expected_bridge,
        "predicted_bridge": run["answer_bridge"],
        "passed": ok,
    }


def run_ablation() -> dict[str, Any]:
    nodes, edges = build_graph()
    cases = build_cases()
    rows: list[dict[str, Any]] = []
    summary = {
        "cases": len(cases),
        "naive_correct": 0,
        "typed_correct": 0,
        "typed_fixed_cases": [],
    }
    for case in cases:
        naive = run_naive_shared_neighbor(case, nodes, edges)
        typed = run_typed_constraint(case, nodes, edges)
        naive_judge = judge(case, naive)
        typed_judge = judge(case, typed)
        summary["naive_correct"] += int(naive_judge["passed"])
        summary["typed_correct"] += int(typed_judge["passed"])
        if (not naive_judge["passed"]) and typed_judge["passed"]:
            summary["typed_fixed_cases"].append(case.case_id)
        rows.append(
            {
                "case_id": case.case_id,
                "query": case.query,
                "note": case.note,
                "naive": naive,
                "naive_judge": naive_judge,
                "typed": typed,
                "typed_judge": typed_judge,
            }
        )
    return {
        "summary": summary,
        "cases": rows,
    }


def render_html(report: dict[str, Any]) -> str:
    summary = report["summary"]
    rows = []
    for row in report["cases"]:
        rows.append(
            f"""
            <tr>
              <td><b>{esc(row['case_id'])}</b><br /><span class="muted">{esc(row['query'])}</span></td>
              <td>{esc(row['naive']['answer_label'])}<br /><code>{esc(row['naive']['trace'])}</code></td>
              <td>{esc(row['typed']['answer_label'])}<br /><code>{esc(row['typed']['trace'])}</code></td>
              <td>{'yes' if row['naive_judge']['passed'] else 'no'}</td>
              <td>{'yes' if row['typed_judge']['passed'] else 'no'}</td>
              <td>{esc(row['note'])}</td>
            </tr>
            """
        )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Nano Typed Path Constraint Ablation</title>
  <style>
    :root{{--bg:#f5f7fb;--panel:#fff;--line:#dde5ef;--text:#182333;--muted:#607286;--blue:#245cff;--shadow:0 14px 34px rgba(18,32,51,.08)}}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
    .page{{max-width:1180px;margin:0 auto;padding:28px 20px 56px}}
    .hero,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow);padding:18px;margin-bottom:16px}}
    .hero{{padding:28px;background:linear-gradient(135deg,#fff 0%,#eef4ff 100%)}}
    h1,h2{{margin:0 0 10px;line-height:1.28}} h1{{font-size:30px}} h2{{font-size:20px;padding-bottom:8px;border-bottom:1px solid var(--line)}}
    p{{margin:8px 0}} .muted{{color:var(--muted)}}
    table{{width:100%;border-collapse:collapse;margin-top:10px}}
    th,td{{border:1px solid var(--line);padding:10px;vertical-align:top;text-align:left}}
    th{{background:#f4f7fd}}
    code{{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;background:#f3f6fb;border:1px solid #dfe7f1;border-radius:4px;padding:1px 5px;font-size:12px;word-break:break-all}}
    .kpi{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:14px}}
    .kpi>div{{border:1px solid var(--line);border-radius:10px;padding:12px;background:#fbfcff}}
    .kpi strong{{display:block;font-size:24px}}
    .callout{{margin-top:12px;padding:12px 14px;border-left:4px solid var(--blue);background:#f4f8ff;border-radius:8px}}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>Nano Typed Path Constraint Ablation</h1>
      <p class="muted">
        这个实验关注一个很具体但很通用的问题：<b>图检索里“共享邻居”并不等于“语义合法的桥”</b>。
        如果不约束桥接节点类型和边类型，系统很容易把共享公司、共享频道、共享社区误当成“介绍人”或“牵线人”。
      </p>
      <div class="kpi">
        <div><strong>{summary['cases']}</strong><span class="muted">cases</span></div>
        <div><strong>{summary['naive_correct']}</strong><span class="muted">naive shared-neighbor</span></div>
        <div><strong>{summary['typed_correct']}</strong><span class="muted">typed path constraint</span></div>
        <div><strong>{esc(summary['typed_fixed_cases'])}</strong><span class="muted">cases fixed by typed constraints</span></div>
      </div>
      <div class="callout">
        核心主张：关系题不仅需要 <code>path_grounding</code>，还需要 <b>typed path grounding</b>。
        否则图里任何高显著度共享节点都可能冒充“答案”。
      </div>
    </section>

    <section class="panel">
      <h2>Results</h2>
      <table>
        <thead>
          <tr>
            <th>Case</th>
            <th>Naive</th>
            <th>Typed Constraint</th>
            <th>Naive OK</th>
            <th>Typed OK</th>
            <th>Why it matters</th>
          </tr>
        </thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </section>
  </div>
</body>
</html>"""


def main() -> None:
    report = run_ablation()
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    html_text = render_html(report)
    OUT_HTML.write_text(html_text, encoding="utf-8")
    PUBLIC_HTML.write_text(html_text, encoding="utf-8")
    print(json.dumps({"ok": True, "summary": report["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
