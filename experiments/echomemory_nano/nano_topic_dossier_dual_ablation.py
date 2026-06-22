#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from nano_topic_dossier_canonicalization_ablation import build_demo


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_topic_dossier_dual_ablation_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_topic_dossier_dual_ablation_20260617.html"
)


def esc(value: Any) -> str:
    return html.escape(str(value))


CONFIGS = [
    {
        "label": "explicit_hint + lexical",
        "grouping_mode": "explicit_hint",
        "selection_mode": "lexical",
        "family": "upper_bound",
    },
    {
        "label": "naive_no_hint + lexical",
        "grouping_mode": "naive_no_hint",
        "selection_mode": "lexical",
        "family": "baseline",
    },
    {
        "label": "naive_no_hint + longitudinal",
        "grouping_mode": "naive_no_hint",
        "selection_mode": "longitudinal",
        "family": "selection_only",
    },
    {
        "label": "canonicalized_no_hint + lexical",
        "grouping_mode": "canonicalized_no_hint",
        "selection_mode": "lexical",
        "family": "grouping_only",
    },
    {
        "label": "canonicalized_no_hint + longitudinal",
        "grouping_mode": "canonicalized_no_hint",
        "selection_mode": "longitudinal",
        "family": "full_method",
    },
]


def run() -> dict[str, Any]:
    bench, cases = build_demo()
    rows: list[dict[str, Any]] = []
    for cfg in CONFIGS:
        result = bench.score_config(
            cfg["grouping_mode"],
            cfg["selection_mode"],
            cases,
            label=cfg["label"],
        )
        result["family"] = cfg["family"]
        rows.append(result)

    case_rows = []
    for case in cases:
        runs = []
        for row in rows:
            run = next(item for item in row["runs"] if item["qid"] == case.qid)
            runs.append(run)
        case_rows.append(
            {
                "qid": case.qid,
                "question": case.question,
                "gold_topic": case.gold_topic,
                "runs": runs,
            }
        )

    payload = {"summary": rows, "cases": case_rows}
    return payload


def render(payload: dict[str, Any]) -> str:
    summary_rows = []
    for row in payload["summary"]:
        summary_rows.append(
            "<tr>"
            f"<td>{esc(row['mode'])}</td>"
            f"<td>{esc(row['grouping_mode'])}</td>"
            f"<td>{esc(row['selection_mode'])}</td>"
            f"<td>{row['correct']}/{row['total']}</td>"
            f"<td>{row['accuracy']:.2%}</td>"
            f"<td>{row['cluster_count']}</td>"
            f"<td>{row['purity']:.2%}</td>"
            "</tr>"
        )

    case_sections = []
    for case in payload["cases"]:
        cards = []
        for run in case["runs"]:
            cards.append(
                "<div class='card'>"
                f"<h4>{esc(run['mode'])} {'✅' if run['success'] else '❌'}</h4>"
                f"<p><b>Selected dossier:</b> {esc(run['selected_dossier'])}</p>"
                f"<p><b>Selected gold topics:</b> {esc(', '.join(run['selected_gold_topics'])) or 'none'}</p>"
                f"<p><b>Answer:</b><br>{esc(run['answer'])}</p>"
                "</div>"
            )
        case_sections.append(
            "<section class='panel'>"
            f"<h3>{esc(case['qid'])} · {esc(case['question'])}</h3>"
            f"<p class='muted'>Gold topic: {esc(case['gold_topic'])}</p>"
            f"<div class='grid'>{''.join(cards)}</div>"
            "</section>"
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Nano Topic Dossier Dual Ablation</title>
  <style>
    :root {{ --bg:#f6f8fc;--panel:#fff;--line:#dbe3ee;--text:#172233;--muted:#617186;--blue:#245cff; }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
    .page{{max-width:1220px;margin:0 auto;padding:24px 18px 56px}}
    .hero,.panel,.card{{background:var(--panel);border:1px solid var(--line);border-radius:12px}}
    .hero,.panel{{padding:20px;margin-bottom:16px}}
    .hero{{background:linear-gradient(135deg,#fff 0%,#eef4ff 100%)}}
    .grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}
    .card{{padding:14px}}
    h1,h2,h3,h4{{margin:0 0 10px;line-height:1.3}} h1{{font-size:28px}} h2{{font-size:20px}} h3{{font-size:17px}} h4{{font-size:15px}}
    p{{margin:8px 0}} .muted{{color:var(--muted)}}
    table{{width:100%;border-collapse:collapse;margin-top:10px}} th,td{{border:1px solid var(--line);padding:10px;text-align:left;vertical-align:top}} th{{background:#f4f7fd}}
    code{{background:#f3f6fb;border:1px solid #e0e7f1;border-radius:4px;padding:1px 5px;font-size:12px}}
    ul{{margin:8px 0 0 18px;padding:0}} li{{margin:6px 0}}
    @media (max-width:980px){{.grid{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>EchoMemory Nano: Topic Dossier Dual Ablation</h1>
      <p class="muted">This experiment separates two middle-layer effects that were previously bundled together: <b>topic canonicalization</b> and <b>longitudinal dossier selection</b>. The point is to show whether improvement comes from forming a better dossier object, choosing a better dossier at retrieval time, or both.</p>
      <ul>
        <li><b>Grouping axis</b>: explicit hint / naive no-hint / canonicalized no-hint</li>
        <li><b>Selection axis</b>: lexical similarity only / longitudinal dossier bias</li>
        <li><b>No benchmark-specific topic keyword rules were added</b>; the longitudinal selector only prefers dossiers with richer multi-update, multi-timepoint structure.</li>
      </ul>
    </section>

    <section class="panel">
      <h2>1. Summary</h2>
      <table>
        <thead><tr><th>Config</th><th>Grouping</th><th>Selection</th><th>QA Correct</th><th>Accuracy</th><th>Cluster Count</th><th>Purity</th></tr></thead>
        <tbody>{''.join(summary_rows)}</tbody>
      </table>
      <p class="muted">The cleanest interpretation is: grouping quality determines whether the right dossier object exists, and selection quality determines whether the system can actually choose that dossier for longitudinal questions.</p>
    </section>

    {''.join(case_sections)}
  </div>
</body>
</html>"""


def main() -> None:
    payload = run()
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render(payload), encoding="utf-8")
    print(json.dumps({"json": str(OUT_JSON), "html": str(OUT_HTML)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
