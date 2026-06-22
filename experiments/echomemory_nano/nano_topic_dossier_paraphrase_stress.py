#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from nano_topic_dossier_canonicalization_ablation import CanonicalizationBenchmark, QueryCase, build_demo


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_topic_dossier_paraphrase_stress_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_topic_dossier_paraphrase_stress_20260617.html"
)


def esc(value: Any) -> str:
    return html.escape(str(value))


def paraphrase_cases() -> list[QueryCase]:
    return [
        QueryCase("lease_1", "How did the apartment lease situation evolve?", "apartment_lease", ["Rua Augusta 14", "2026-03-27"]),
        QueryCase("lease_2", "What changed over time in the rental paperwork situation?", "apartment_lease", ["Rua Augusta 14", "2026-03-27"]),
        QueryCase("lease_3", "Give the move-in timeline for the housing handover.", "apartment_lease", ["2026-03-27"]),
        QueryCase("lease_4", "What is the latest state of the housing arrangement?", "apartment_lease", ["2026-03-27"]),

        QueryCase("visa_1", "What is the latest status of the visa process?", "visa_process", ["missing residence document", "approval"]),
        QueryCase("visa_2", "How did the consular case develop?", "visa_process", ["missing residence document", "approval"]),
        QueryCase("visa_3", "What happened over time in Maya's immigration approval case?", "visa_process", ["missing residence document", "approval"]),
        QueryCase("visa_4", "Summarize the residence-document / visa timeline.", "visa_process", ["missing residence document", "approval"]),

        QueryCase("launch_1", "How did the product launch change over time?", "product_launch", ["2026-04-24", "payment bug"]),
        QueryCase("launch_2", "What changed in the rollout plan?", "product_launch", ["2026-04-24", "payment bug"]),
        QueryCase("launch_3", "Give the release schedule timeline.", "product_launch", ["2026-04-24", "payment bug"]),
        QueryCase("launch_4", "What is the latest state of the beta launch?", "product_launch", ["2026-04-24", "payment bug"]),

        QueryCase("care_1", "How did the childcare arrangement evolve?", "family_support", ["mother", "weekday"]),
        QueryCase("care_2", "What changed over time in the babysitter plan?", "family_support", ["mother", "weekday"]),
        QueryCase("care_3", "Give the care-arrangement timeline.", "family_support", ["mother", "weekday"]),
        QueryCase("care_4", "What is the latest status of the family support arrangement?", "family_support", ["mother", "weekday"]),
    ]


def run() -> dict[str, Any]:
    bench, _cases = build_demo()
    cases = paraphrase_cases()

    configs = [
        ("naive_no_hint + lexical", "naive_no_hint", "lexical"),
        ("canonicalized_no_hint + lexical", "canonicalized_no_hint", "lexical"),
        ("canonicalized_no_hint + longitudinal", "canonicalized_no_hint", "longitudinal"),
    ]

    rows = []
    for label, grouping_mode, selection_mode in configs:
        result = bench.score_config(
            grouping_mode,
            selection_mode,
            cases,
            label=label,
        )
        family_totals: dict[str, dict[str, int]] = {}
        for run in result["runs"]:
            family = run["gold_topic"]
            stats = family_totals.setdefault(family, {"count": 0, "correct": 0})
            stats["count"] += 1
            stats["correct"] += int(run["success"])
        result["family_totals"] = family_totals
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
    return {"summary": rows, "cases": case_rows}


def render(payload: dict[str, Any]) -> str:
    summary_rows = []
    for row in payload["summary"]:
        summary_rows.append(
            "<tr>"
            f"<td>{esc(row['mode'])}</td>"
            f"<td>{row['correct']}/{row['total']}</td>"
            f"<td>{row['accuracy']:.2%}</td>"
            f"<td>{row['cluster_count']}</td>"
            f"<td>{row['purity']:.2%}</td>"
            "</tr>"
        )

    family_sections = []
    for row in payload["summary"]:
        family_rows = []
        for family, stats in sorted(row["family_totals"].items()):
            family_rows.append(
                f"<tr><td>{esc(family)}</td><td>{stats['correct']}/{stats['count']}</td></tr>"
            )
        family_sections.append(
            "<section class='panel'>"
            f"<h3>{esc(row['mode'])}</h3>"
            "<table>"
            "<thead><tr><th>Topic family</th><th>Correct</th></tr></thead>"
            f"<tbody>{''.join(family_rows)}</tbody>"
            "</table>"
            "</section>"
        )

    case_sections = []
    for case in payload["cases"]:
        cards = []
        for run in case["runs"]:
            cards.append(
                "<div class='card'>"
                f"<h4>{esc(run['mode'])} {'✅' if run['success'] else '❌'}</h4>"
                f"<p><b>Selected dossier:</b> {esc(run['selected_dossier'])}</p>"
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
  <title>EchoMemory Nano Topic Dossier Paraphrase Stress</title>
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
    ul{{margin:8px 0 0 18px;padding:0}} li{{margin:6px 0}}
    @media (max-width:980px){{.grid{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>EchoMemory Nano: Topic Dossier Paraphrase Stress</h1>
      <p class="muted">This benchmark holds the underlying topic objects fixed, but rewrites the longitudinal queries with different surface forms such as <code>lease / rental paperwork / housing arrangement</code> or <code>visa / consular case / immigration approval</code>. The goal is to test whether middle-layer retrieval still selects the right dossier under wording variation without adding benchmark-specific keywords.</p>
      <ul>
        <li><b>Baseline</b>: naive no-hint grouping + lexical selection</li>
        <li><b>Grouping only</b>: canonicalized no-hint grouping + lexical selection</li>
        <li><b>Full method</b>: canonicalized no-hint grouping + longitudinal dossier selection</li>
      </ul>
    </section>

    <section class="panel">
      <h2>1. Summary</h2>
      <table>
        <thead><tr><th>Config</th><th>Correct</th><th>Accuracy</th><th>Cluster Count</th><th>Purity</th></tr></thead>
        <tbody>{''.join(summary_rows)}</tbody>
      </table>
    </section>

    {''.join(family_sections)}
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
