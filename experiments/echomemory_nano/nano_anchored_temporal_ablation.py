#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from nano_canonical_echomemory_tg import CanonicalEchoMemoryTG


OUT_JSON = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_anchored_temporal_ablation_results.json")
OUT_HTML = Path("/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_anchored_temporal_ablation_20260614.html")


@dataclass
class Case:
    case_id: str
    query: str
    query_time: str
    expected_keywords: list[str]
    expected_top_layers: list[str]
    note: str


def build_memory() -> CanonicalEchoMemoryTG:
    mem = CanonicalEchoMemoryTG(enable_story_time=True, enable_graph_first=True)
    mem.append_turn("user", "Jon lost his banker job yesterday and decided to start a studio.", "2023-01-20T09:00:00+00:00")
    mem.append_turn("user", "Jon opened his studio in April 2023 after months of preparation.", "2023-04-20T09:00:00+00:00")
    mem.append_turn("user", "Jon started expanding his studio social media presence in April 2023.", "2023-04-25T09:00:00+00:00")
    mem.append_turn("user", "Jon started learning marketing and analytics tools in July 2023.", "2023-07-10T09:00:00+00:00")
    mem.append_turn("user", "Jon visited Rome on 2023-06-19.", "2023-06-20T09:00:00+00:00")
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


def run_tree_only(mem: CanonicalEchoMemoryTG, case: Case) -> dict[str, Any]:
    q_terms = mem._tokenize(case.query)
    hits = mem._search_layer("temporal_tree", q_terms, query=case.query, query_time=case.query_time)
    hits = sorted(hits, key=lambda h: h.score, reverse=True)[:6]
    return {
        "mode": "tree_only",
        "hits": [asdict(h) for h in hits],
    }


def run_graph_only(mem: CanonicalEchoMemoryTG, case: Case) -> dict[str, Any]:
    q_terms = mem._tokenize(case.query)
    hits = mem._search_layer("event", q_terms, query=case.query, query_time=case.query_time)
    hits += mem._search_layer("fact", q_terms, query=case.query, query_time=case.query_time)
    hits = sorted(hits, key=lambda h: h.score, reverse=True)[:6]
    return {
        "mode": "graph_only",
        "hits": [asdict(h) for h in hits],
    }


def run_dual(mem: CanonicalEchoMemoryTG, case: Case) -> dict[str, Any]:
    result = mem.search(case.query, query_time=case.query_time)
    return {
        "mode": "dual_backbone",
        "hits": [asdict(h) for h in result.hits],
        "plan": asdict(result.plan),
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


def evaluate_case(mem: CanonicalEchoMemoryTG, case: Case) -> dict[str, Any]:
    tree_only = run_tree_only(mem, case)
    graph_only = run_graph_only(mem, case)
    dual = run_dual(mem, case)
    return {
        "case_id": case.case_id,
        "query": case.query,
        "query_time": case.query_time,
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
    rows = []
    for case in payload["cases"]:
        rows.append(
            f"""
            <tr>
              <td>{html.escape(case['case_id'])}</td>
              <td>{html.escape(case['query'])}<br><span class="muted">{html.escape(case['query_time'])}</span></td>
              <td>{html.escape(', '.join(case['expected_top_layers']))}</td>
              <td>{html.escape(case['tree_only_judge']['top_layer'])} / {'yes' if case['tree_only_judge']['overall_ok'] else 'no'}</td>
              <td>{html.escape(case['graph_only_judge']['top_layer'])} / {'yes' if case['graph_only_judge']['overall_ok'] else 'no'}</td>
              <td>{html.escape(case['dual_backbone_judge']['top_layer'])} / {'yes' if case['dual_backbone_judge']['overall_ok'] else 'no'}</td>
              <td>{html.escape(case['note'])}</td>
            </tr>
            """
        )

    summary = payload["summary"]
    first_case = payload["cases"][0]
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Anchored Temporal Ablation</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --text:#18212f; --muted:#617184; --line:#dbe3ee;
      --blue:#2563eb; --shadow:0 10px 28px rgba(15,23,42,.08);
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
    .muted {{ color:var(--muted); font-size:12px; }}
    pre {{ background:#f8fafc; border:1px solid var(--line); border-radius:8px; padding:12px; overflow:auto; font-size:12px; }}
    @media (max-width:980px) {{ .kpis {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>EchoMemory Anchored Temporal Ablation</h1>
      <p>
        这组小实验只回答一个很具体的问题：
        <b>在 anchored temporal queries 上，tree-only、graph-only、dual-backbone 哪个更稳？</b>
      </p>
      <div class="kpis">
        <div class="kpi"><span class="label">Cases</span><span class="value">{summary['cases']}</span></div>
        <div class="kpi"><span class="label">Tree-only</span><span class="value">{summary['tree_only_passed']}</span></div>
        <div class="kpi"><span class="label">Graph-only</span><span class="value">{summary['graph_only_passed']}</span></div>
        <div class="kpi"><span class="label">Dual-backbone</span><span class="value">{summary['dual_passed']}</span></div>
      </div>
    </div>

    <div class="section">
      <h2>Interpretation</h2>
      <p>
        这不是 general benchmark，而是一个更尖锐的 method ablation：
        只看带 <code>query_time</code> 的相对时间问题。它的意义在于验证论文里的 temporal backbone 主张，而不是证明整个系统已经最优。
      </p>
    </div>

    <div class="section">
      <h2>Results</h2>
      <table>
        <thead>
          <tr>
            <th>Case</th>
            <th>Query / Query Time</th>
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
      <h2>Example</h2>
      <pre>{html.escape(json.dumps(first_case, ensure_ascii=False, indent=2))}</pre>
    </div>
  </div>
</body>
</html>"""


def main() -> None:
    mem = build_memory()
    cases = [
        Case(
            case_id="yesterday_loss",
            query="What happened yesterday?",
            query_time="2023-01-21T09:00:00+00:00",
            expected_keywords=["2023-01-19", "lost his banker job"],
            expected_top_layers=["temporal_tree"],
            note="Relative day query should anchor to query_time and prefer day/month blocks.",
        ),
        Case(
            case_id="last_week_learning",
            query="What happened last week about marketing and analytics tools?",
            query_time="2023-07-17T09:00:00+00:00",
            expected_keywords=["2023-07", "marketing and analytics tools"],
            expected_top_layers=["temporal_tree", "event"],
            note="Relative week query should find the anchored July event.",
        ),
        Case(
            case_id="rome_date",
            query="When was Jon in Rome?",
            query_time="2023-06-21T09:00:00+00:00",
            expected_keywords=["2023-06-19", "visited Rome"],
            expected_top_layers=["temporal_tree", "event"],
            note="Explicit temporal question should be stable under anchored retrieval.",
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
