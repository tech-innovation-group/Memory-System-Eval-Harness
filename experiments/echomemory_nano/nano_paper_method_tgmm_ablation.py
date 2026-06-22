#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from nano_paper_method_tgmm import build_demo


CASES = [
    {
        "id": "q1_temporal",
        "query": "When did Jon lose his job?",
        "expects_any": ["2023-01-19", "lost_job"],
    },
    {
        "id": "q2_profile",
        "query": "What does Gina like?",
        "expects_any": ["jazz", "museums"],
    },
    {
        "id": "q3_plan",
        "query": "What does Jon plan to do?",
        "expects_any": ["three investors", "plans to call"],
    },
    {
        "id": "q4_visual",
        "query": "What does the screenshot say?",
        "expects_any": ["Revenue 123", "Margin 18%", "dashboard screenshot"],
    },
]


def lexical_score(text: str, query: str) -> float:
    query_terms = [t for t in query.lower().replace("?", "").split() if len(t) >= 2]
    hay = text.lower()
    return float(sum(1 for t in query_terms if t in hay))


def search_flat_facts(system, query: str) -> list[dict]:
    hits = []
    for node in system.nodes:
        if node.node_type != "fact":
            continue
        score = lexical_score(node.content, query)
        if score > 0:
            hits.append(
                {
                    "item_id": node.node_id,
                    "layer": "fact",
                    "score": score,
                    "content": node.content,
                }
            )
    hits.sort(key=lambda h: (-h["score"], h["item_id"]))
    return hits[:5]


def search_blocks_only(system, query: str) -> list[dict]:
    hits = []
    for block in system.blocks:
        score = lexical_score(block.title + "\n" + block.content, query)
        if score > 0:
            hits.append(
                {
                    "item_id": block.block_id,
                    "layer": "block",
                    "score": score + 0.3,
                    "content": block.content,
                }
            )
    hits.sort(key=lambda h: (-h["score"], h["item_id"]))
    return hits[:5]


def judge_hit(hits: list[dict], expects_any: list[str]) -> bool:
    merged = "\n".join(hit["content"] for hit in hits[:3])
    lowered = merged.lower()
    return any(expect.lower() in lowered for expect in expects_any)


def render_html(report: dict) -> str:
    rows = []
    for variant in report["variants"]:
        case_rows = []
        for case in variant["cases"]:
            badge = "ok" if case["pass"] else "bad"
            top = case["hits"][0]["content"] if case["hits"] else "no hit"
            case_rows.append(
                f"""
                <tr>
                  <td>{case['id']}</td>
                  <td>{case['query']}</td>
                  <td><span class="{badge}">{'pass' if case['pass'] else 'fail'}</span></td>
                  <td><pre>{top}</pre></td>
                </tr>
                """
            )
        rows.append(
            f"""
            <section class="card">
              <h2>{variant['name']} · {variant['score']}/{variant['total']}</h2>
              <p>{variant['note']}</p>
              <table>
                <thead><tr><th>ID</th><th>Query</th><th>Result</th><th>Top hit</th></tr></thead>
                <tbody>{''.join(case_rows)}</tbody>
              </table>
            </section>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Nano Paper Method Ablation</title>
  <style>
    body {{ margin: 0; background: #f5f7fb; color: #172033; font: 14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif; }}
    .page {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
    .hero, .card {{ background: #fff; border: 1px solid #d9e2ef; border-radius: 12px; box-shadow: 0 10px 28px rgba(15,23,42,0.08); }}
    .hero {{ padding: 28px 30px; margin-bottom: 18px; }}
    .card {{ padding: 20px 22px; margin-bottom: 18px; }}
    h1, h2 {{ margin: 0 0 10px; }}
    p {{ color: #5b6679; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ text-align: left; vertical-align: top; padding: 10px; border-top: 1px solid #d9e2ef; }}
    th {{ font-size: 12px; color: #5b6679; background: #fbfcff; }}
    pre {{ white-space: pre-wrap; word-break: break-word; margin: 0; font-family: ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
    .ok, .bad {{ display:inline-block; padding:2px 8px; border-radius:999px; font-size:12px; font-weight:700; }}
    .ok {{ background:#e9faf6; color:#0f766e; }}
    .bad {{ background:#fff0ef; color:#b42318; }}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>Nano Paper Method Ablation</h1>
      <p>这个小实验不追求 benchmark 分数，只验证论文方法节里的核心结构差异：<b>flat facts</b>、<b>typed blocks</b>、<b>full temporal-graph + multimodal</b>。</p>
      <p>四道 toy query 分别覆盖：时间、偏好、计划、截图证据。</p>
    </section>
    {''.join(rows)}
  </div>
</body>
</html>"""


def main() -> None:
    system, _payload = build_demo()

    variants = [
        ("Flat facts only", "只看 fact nodes，模拟最朴素的扁平检索。", search_flat_facts),
        ("Typed blocks only", "只看 profile/timeline/plan blocks，模拟只做聚合视图。", search_blocks_only),
        ("Full TG+MM", "走完整 temporal graph + typed blocks + image_evidence 路径。", lambda s, q: s.search(q)["hits"]),
    ]

    variant_reports = []
    for name, note, fn in variants:
        case_reports = []
        score = 0
        for case in CASES:
            hits = fn(system, case["query"])
            passed = judge_hit(hits, case["expects_any"])
            score += int(passed)
            case_reports.append(
                {
                    "id": case["id"],
                    "query": case["query"],
                    "pass": passed,
                    "hits": hits,
                }
            )
        variant_reports.append(
            {
                "name": name,
                "note": note,
                "score": score,
                "total": len(CASES),
                "cases": case_reports,
            }
        )

    report = {"variants": variant_reports}
    out_json = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_paper_method_tgmm_ablation_results.json")
    out_html = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_paper_method_tgmm_ablation_report.html")
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_html.write_text(render_html(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
