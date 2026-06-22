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
BASE_PATH = ROOT / "nano_paper_method_tgmm.py"
OUT_JSON = ROOT / "nano_graph_first_ablation_results.json"
OUT_HTML = ROOT / "nano_graph_first_ablation_report.html"


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
    expected_top_type: str
    note: str


def esc(value: Any) -> str:
    return html.escape(str(value))


def setup_memory() -> Any:
    mod = load_module(BASE_PATH, "echomemory_graph_first_ablation")
    mem = mod.NanoPaperMethodTGMM()

    mem.append_text(
        "Gina visited Rome on 2023-01-30 after leaving Milan.",
        observed_at="2023-02-01T09:00:00Z",
        committed_at="2023-02-01T09:05:00Z",
    )
    mem.append_text(
        "On 2023-02-02 Gina lost her job and started searching for design roles.",
        observed_at="2023-02-03T10:00:00Z",
        committed_at="2023-02-03T10:06:00Z",
    )
    mem.append_text(
        "Gina plans to move to Lisbon after the spring hiring season.",
        observed_at="2023-02-05T08:00:00Z",
        committed_at="2023-02-05T08:03:00Z",
    )
    mem.append_text(
        "Jon married Alice on 2023-03-12 in Seattle.",
        observed_at="2023-03-13T11:00:00Z",
        committed_at="2023-03-13T11:03:00Z",
    )
    mem.append_image(
        caption="Phone screenshot from Gina's arrival day.",
        ocr="Roma Termini 08:42 Platform 7",
        observed_at="2023-02-01T09:10:00Z",
        committed_at="2023-02-01T09:10:30Z",
        linked_subject="Gina",
        tags=["rome", "station", "arrival"],
        event_time="2023-01-30",
    )

    mem.project()
    return mem


def lexical_terms(query: str) -> list[str]:
    import re
    lowered = query.lower()
    return [t for t in re.findall(r"[a-z]{2,}|[\u4e00-\u9fa5]{1,}", lowered) if t not in {"the", "did", "what", "was"}]


def lexical_score(text: str, terms: list[str]) -> float:
    hay = text.lower()
    return float(sum(1 for term in terms if term in hay))


def baseline_lexical_search(mem: Any, query: str) -> dict[str, Any]:
    terms = lexical_terms(query)
    hits: list[dict[str, Any]] = []
    for block in mem.blocks:
        score = lexical_score(block.title + "\n" + block.content, terms)
        if score > 0:
            hits.append(
                {
                    "item_id": block.block_id,
                    "node_type": "block",
                    "score": score + 0.2,
                    "content": block.content,
                }
            )
    for node in mem.nodes:
        if node.node_type not in {"fact", "entity"}:
            continue
        score = lexical_score(node.content, terms)
        if score > 0:
            hits.append(
                {
                    "item_id": node.node_id,
                    "node_type": node.node_type,
                    "score": score,
                    "content": node.content,
                    "event_time": node.event_time_start,
                }
            )
    hits.sort(key=lambda item: (-float(item["score"]), str(item["item_id"])))
    return {"mode": "lexical_baseline", "hits": hits[:5]}


def graph_first_search(mem: Any, query: str) -> dict[str, Any]:
    result = mem.search(query)
    return {
        "mode": "graph_first",
        "plan": result.get("plan", {}),
        "hits": [
            {
                "item_id": hit.get("item_id"),
                "node_type": hit.get("layer"),
                "score": hit.get("score"),
                "content": hit.get("content"),
                "event_time": hit.get("evidence_time", ""),
            }
            for hit in result.get("hits", [])
        ],
    }


def graph_path_search(mem: Any, query: str) -> dict[str, Any]:
    base = graph_first_search(mem, query)
    plan = base.get("plan", {}) or {}
    intent = str(plan.get("intent", "general"))
    if intent == "plan":
        return {
            "mode": "graph_path",
            "plan": plan,
            "hits": list(base["hits"]),
        }

    hits = list(base["hits"])
    terms = lexical_terms(query)
    node_by_id = {node.node_id: node for node in mem.nodes}

    # Simple path-style expansion: if an event hit lands high, add adjacent
    # entity/image/fact evidence with a small score bonus.
    expansions: list[dict[str, Any]] = []
    top_ids = {str(hit["item_id"]) for hit in hits[:3]}
    for edge in mem.edges:
        if edge.source_id not in top_ids and edge.target_id not in top_ids:
            continue
        for node_id in (edge.source_id, edge.target_id):
            node = node_by_id.get(node_id)
            if node is None:
                continue
            score = lexical_score(node.content, terms)
            if score <= 0 and node.node_type not in {"image_evidence", "event", "entity", "fact"}:
                continue
            if intent in {"temporal", "visual"} and node.node_type in {"event", "image_evidence"}:
                score += 0.75
            if intent == "temporal" and edge.relation_type in {"involves", "evidence_of", "temporal_next"}:
                score += 0.4
            expansions.append(
                {
                    "item_id": node.node_id,
                    "node_type": node.node_type,
                    "score": score + 0.85,
                    "content": f"{node.content}\n[path:{edge.relation_type}]",
                    "event_time": node.event_time_start,
                }
            )

    merged: dict[str, dict[str, Any]] = {}
    for item in hits + expansions:
        key = str(item["item_id"])
        prev = merged.get(key)
        if prev is None or float(item["score"]) > float(prev["score"]):
            merged[key] = item

    reranked: list[dict[str, Any]] = []
    for item in merged.values():
        score = float(item["score"])
        node_type = str(item["node_type"])
        if intent == "temporal":
            if node_type == "event":
                score += 0.9
            elif node_type == "block":
                score -= 0.8
        elif intent == "visual":
            if node_type == "image_evidence":
                score += 0.9
            elif node_type == "block":
                score -= 0.6
        reranked.append({**item, "score": round(score, 3)})

    merged_hits = sorted(reranked, key=lambda item: (-float(item["score"]), str(item["item_id"])))
    return {
        "mode": "graph_path",
        "plan": plan,
        "hits": merged_hits[:6],
    }


def top_type(result: dict[str, Any]) -> str:
    hits = result.get("hits", [])
    if not hits:
        return "none"
    return str(hits[0].get("node_type", ""))


def contains_keywords(result: dict[str, Any], expected_keywords: list[str]) -> bool:
    blob = "\n".join(str(hit.get("content", "")) for hit in result.get("hits", [])[:4]).lower()
    return all(keyword.lower() in blob for keyword in expected_keywords)


def evaluate_case(mem: Any, case: EvalCase) -> dict[str, Any]:
    lexical = baseline_lexical_search(mem, case.query)
    graph_first = graph_first_search(mem, case.query)
    graph_path = graph_path_search(mem, case.query)

    return {
        "case_id": case.case_id,
        "query": case.query,
        "expected_keywords": case.expected_keywords,
        "expected_top_type": case.expected_top_type,
        "note": case.note,
        "lexical": lexical,
        "graph_first": graph_first,
        "graph_path": graph_path,
        "lexical_ok": contains_keywords(lexical, case.expected_keywords) and top_type(lexical) == case.expected_top_type,
        "graph_first_ok": contains_keywords(graph_first, case.expected_keywords) and top_type(graph_first) == case.expected_top_type,
        "graph_path_ok": contains_keywords(graph_path, case.expected_keywords) and top_type(graph_path) == case.expected_top_type,
    }


def run_eval() -> dict[str, Any]:
    mem = setup_memory()
    cases = [
        EvalCase(
            case_id="temporal_event_date",
            query="When did Gina lose her job?",
            expected_keywords=["2023-02-02"],
            expected_top_type="event",
            note="Temporal question should prefer event nodes, not generic fact text.",
        ),
        EvalCase(
            case_id="visual_arrival_evidence",
            query="What was visible in Gina's screenshot from her arrival day?",
            expected_keywords=["Roma Termini", "08:42"],
            expected_top_type="image_evidence",
            note="Visual question should prioritize image evidence rather than timeline blocks.",
        ),
        EvalCase(
            case_id="multi_hop_person_relation",
            query="Who married Alice and when?",
            expected_keywords=["Jon", "2023-03-12"],
            expected_top_type="event",
            note="This needs relation plus event date; path-style graph evidence should help.",
        ),
        EvalCase(
            case_id="future_plan",
            query="What does Gina plan to do after spring hiring season?",
            expected_keywords=["move to Lisbon"],
            expected_top_type="block",
            note="Plan blocks are still useful; graph-first should not erase block advantages.",
        ),
    ]

    rows = [evaluate_case(mem, case) for case in cases]
    summary = {
        "lexical_correct": sum(1 for row in rows if row["lexical_ok"]),
        "graph_first_correct": sum(1 for row in rows if row["graph_first_ok"]),
        "graph_path_correct": sum(1 for row in rows if row["graph_path_ok"]),
        "total_cases": len(rows),
    }

    return {
        "rows": rows,
        "summary": summary,
    }


def render_html_report(data: dict[str, Any]) -> str:
    summary = data["summary"]
    rows = data["rows"]

    def hit_list(result: dict[str, Any]) -> str:
        hits = result.get("hits", [])
        if not hits:
            return "<li>-</li>"
        return "".join(
            f"<li><code>{esc(hit.get('item_id'))}</code> · {esc(hit.get('node_type'))} · score={esc(hit.get('score'))}<br>{esc(str(hit.get('content', ''))[:200])}</li>"
            for hit in hits[:4]
        )

    cases_html = "".join(
        "<div class='case'>"
        f"<h3>{esc(row['case_id'])}</h3>"
        f"<p><b>Query:</b> {esc(row['query'])}</p>"
        f"<p class='muted'>{esc(row['note'])}</p>"
        "<div class='grid three'>"
        "<div class='card'>"
        f"<div class='badge {'ok' if row['lexical_ok'] else 'bad'}'>lexical baseline {'pass' if row['lexical_ok'] else 'fail'}</div>"
        "<ul>" + hit_list(row["lexical"]) + "</ul>"
        "</div>"
        "<div class='card'>"
        f"<div class='badge {'ok' if row['graph_first_ok'] else 'bad'}'>graph-first {'pass' if row['graph_first_ok'] else 'fail'}</div>"
        "<ul>" + hit_list(row["graph_first"]) + "</ul>"
        "</div>"
        "<div class='card'>"
        f"<div class='badge {'ok' if row['graph_path_ok'] else 'bad'}'>graph-path {'pass' if row['graph_path_ok'] else 'fail'}</div>"
        "<ul>" + hit_list(row["graph_path"]) + "</ul>"
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
  <title>EchoMemory Graph-first Ablation</title>
  <style>
    :root {{
      --bg:#f6f7fb;--panel:#fff;--text:#172033;--muted:#667085;--line:#dde4ee;
      --green:#067647;--green-soft:#ecfdf3;--red:#b42318;--red-soft:#fff1f3;--blue:#175cd3;--blue-soft:#eff4ff;
    }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.68 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
    .wrap{{max-width:1240px;margin:0 auto;padding:28px 20px 64px}}
    .hero,.panel,.card,.case{{background:var(--panel);border:1px solid var(--line);border-radius:12px}}
    .hero{{padding:28px 30px 22px}}
    .panel{{padding:20px 22px;margin-top:18px}}
    .grid{{display:grid;gap:16px}}
    .grid.three{{grid-template-columns:repeat(3,minmax(0,1fr))}}
    .stats{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:18px}}
    .stat{{padding:14px 16px;border:1px solid var(--line);border-radius:10px;background:#fbfcff}}
    .stat .label{{font-size:12px;color:var(--muted);display:block;margin-bottom:4px}}
    .stat .value{{font-size:22px;font-weight:700}}
    h1,h2,h3{{margin:0 0 10px}}
    p{{margin:0 0 10px}}
    .muted{{color:var(--muted)}}
    .badge{{display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:700;margin-bottom:12px}}
    .badge.ok{{background:var(--green-soft);color:var(--green)}}
    .badge.bad{{background:var(--red-soft);color:var(--red)}}
    .badge.info{{background:var(--blue-soft);color:var(--blue)}}
    .case{{padding:18px 20px;margin-top:16px}}
    .card{{padding:14px}}
    ul{{margin:0;padding-left:18px}}
    li{{margin:6px 0}}
    code{{background:#f3f6fb;border-radius:6px;padding:2px 6px;font-size:12px}}
    @media (max-width: 980px) {{
      .grid.three,.stats{{grid-template-columns:1fr}}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>EchoMemory Nano: Graph-first Ablation</h1>
      <p>
        这个实验专门回答一个方法问题：<b>为什么 EchoMemory 值得把 temporal / relational / visual query 交给 graph-first planner</b>。
        我们比较三种检索模式：
        <b>lexical baseline</b>（只看 block/fact 文本匹配）、
        <b>graph-first</b>（按 query intent 优先 event / image_evidence）、
        <b>graph-path</b>（在 graph-first 基础上沿边补 path evidence）。
      </p>
      <div class="stats">
        <div class="stat"><span class="label">lexical baseline</span><span class="value">{summary['lexical_correct']}/{summary['total_cases']}</span></div>
        <div class="stat"><span class="label">graph-first</span><span class="value">{summary['graph_first_correct']}/{summary['total_cases']}</span></div>
        <div class="stat"><span class="label">graph-path</span><span class="value">{summary['graph_path_correct']}/{summary['total_cases']}</span></div>
        <div class="stat"><span class="label">最重要结论</span><span class="value">Graph is not decoration</span></div>
      </div>
    </div>

    <div class="panel">
      <h2>Interpretation</h2>
      <p>
        如果结果如预期，说明两件事：
        第一，时间题和视觉题不是“多检索点文本”就够了，而是要让 <code>event</code>、<code>image_evidence</code> 直接成为主证据入口。
        第二，只把图节点召回出来还不够，<b>沿边拿到 path evidence</b> 会比孤立节点更接近真实系统要做的事。
      </p>
    </div>

    {cases_html}
  </div>
</body>
</html>
"""


def main() -> None:
    data = run_eval()
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html_report(data), encoding="utf-8")
    print(json.dumps({"json": str(OUT_JSON), "html": str(OUT_HTML), "summary": data["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
