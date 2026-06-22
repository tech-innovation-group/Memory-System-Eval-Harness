#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SRC = Path("/Users/chx/Code/echomemory/echo_memory_v006/experiments/realcode_selfcheck_subset_benchmark_results.json")
OUT_JSON = Path("/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_realcode_behavior_cost_bridge_20260615.json")
OUT_HTML = Path("/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_realcode_behavior_cost_bridge_20260615.html")


def esc(value: Any) -> str:
    return html.escape(str(value))


def load() -> dict[str, Any]:
    return json.loads(SRC.read_text(encoding="utf-8"))


def family_bucket(case: dict[str, Any]) -> str:
    expected = case.get("expectation", {}).get("family", "")
    return expected or "none"


def summarize(data: dict[str, Any]) -> dict[str, Any]:
    cases = data["cases"]
    fam_rows: dict[str, dict[str, Any]] = {}
    global_missing = Counter()
    global_expand = Counter()
    for case in cases:
        fam = family_bucket(case)
        row = fam_rows.setdefault(
            fam,
            {
                "cases": 0,
                "review_ok": 0,
                "self_check_on": 0,
                "avg_total_items": [],
                "avg_temporal_items": [],
                "avg_graph_like_items": [],
                "avg_atom_items": [],
                "avg_episode_items": [],
                "avg_overview_items": [],
                "expand_cases": 0,
                "missing_counter": Counter(),
                "expand_counter": Counter(),
            },
        )
        row["cases"] += 1
        if case.get("self_check_enabled"):
            row["self_check_on"] += 1
        if case.get("review_enough"):
            row["review_ok"] += 1
        signals = case.get("signals", {})
        for signal_name in (
            "total_items",
            "temporal_items",
            "graph_like_items",
            "atom_items",
            "episode_items",
            "overview_items",
        ):
            row[f"avg_{signal_name}"].append(signals.get(signal_name, 0))
        missing = list(case.get("review_missing", []))
        expand = list(case.get("review_expand", []))
        if expand:
            row["expand_cases"] += 1
        row["missing_counter"].update(missing)
        row["expand_counter"].update(expand)
        global_missing.update(missing)
        global_expand.update(expand)

    family_summary = {}
    for fam, row in fam_rows.items():
        cases_n = row["cases"]
        family_summary[fam] = {
            "cases": cases_n,
            "review_ok": row["review_ok"],
            "review_ok_rate": round(row["review_ok"] / cases_n, 3) if cases_n else 0.0,
            "self_check_on": row["self_check_on"],
            "avg_total_items": round(sum(row["avg_total_items"]) / cases_n, 3) if cases_n else 0.0,
            "avg_temporal_items": round(sum(row["avg_temporal_items"]) / cases_n, 3) if cases_n else 0.0,
            "avg_graph_like_items": round(sum(row["avg_graph_like_items"]) / cases_n, 3) if cases_n else 0.0,
            "avg_atom_items": round(sum(row["avg_atom_items"]) / cases_n, 3) if cases_n else 0.0,
            "avg_episode_items": round(sum(row["avg_episode_items"]) / cases_n, 3) if cases_n else 0.0,
            "avg_overview_items": round(sum(row["avg_overview_items"]) / cases_n, 3) if cases_n else 0.0,
            "expand_cases": row["expand_cases"],
            "top_missing": row["missing_counter"].most_common(5),
            "top_expand": row["expand_counter"].most_common(5),
        }

    return {
        "summary": data.get("summary", {}),
        "family_summary": family_summary,
        "global_missing": global_missing.most_common(),
        "global_expand": global_expand.most_common(),
        "cases": cases,
    }


def render(payload: dict[str, Any]) -> str:
    families = payload["family_summary"]
    fam_rows = []
    for fam, vals in families.items():
        fam_rows.append(
            f"""<tr>
            <td><b>{esc(fam)}</b></td>
            <td>{esc(vals['review_ok'])}/{esc(vals['cases'])}</td>
            <td>{esc(vals['avg_total_items'])}</td>
            <td>{esc(vals['avg_temporal_items'])}</td>
            <td>{esc(vals['avg_graph_like_items'])}</td>
            <td>{esc(vals['avg_atom_items'])}</td>
            <td>{esc(vals['avg_episode_items'])}</td>
            <td>{esc(vals['avg_overview_items'])}</td>
            <td>{esc(vals['expand_cases'])}</td>
            <td>{esc(vals['top_missing'])}</td>
            <td>{esc(vals['top_expand'])}</td>
            </tr>"""
        )

    case_rows = []
    for case in payload["cases"]:
        sig = case["signals"]
        case_rows.append(
            f"""<tr>
            <td><b>{esc(case['name'])}</b></td>
            <td>{esc(family_bucket(case))}</td>
            <td>{'on' if case['self_check_enabled'] else 'off'}</td>
            <td>{'yes' if case['review_enough'] else 'no'}</td>
            <td>{esc(sig.get('total_items', 0))}</td>
            <td>{esc(sig.get('temporal_items', 0))}</td>
            <td>{esc(sig.get('graph_like_items', 0))}</td>
            <td>{esc(sig.get('atom_items', 0))}</td>
            <td>{esc(case.get('review_missing', []))}</td>
            <td>{esc(case.get('review_expand', []))}</td>
            <td>{esc(case.get('note', ''))}</td>
            </tr>"""
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Real-Code Behavior/Cost Bridge</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --line:#dbe3ee; --text:#172233; --muted:#627286;
      --blue:#245cff; --green:#12895f; --amber:#a86a00; --red:#c13f36;
      --blue-soft:#eef4ff; --green-soft:#eefaf4; --amber-soft:#fff8ec; --red-soft:#fff4f2;
    }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
    .page{{max-width:1320px;margin:0 auto;padding:28px 20px 56px}}
    .hero,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:16px}}
    .hero{{padding:26px;background:linear-gradient(135deg,#fff 0%,#eef4ff 100%)}}
    h1,h2{{margin:0 0 10px;line-height:1.3}} h1{{font-size:30px}} h2{{font-size:20px;padding-bottom:8px;border-bottom:1px solid var(--line)}}
    p{{margin:8px 0}} .muted{{color:var(--muted)}}
    .chips{{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}}
    .chip{{display:inline-block;padding:4px 10px;border-radius:999px;border:1px solid #cad7ee;background:#f8fbff;color:#274674;font-size:12px;font-weight:700}}
    .metric-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:16px}}
    .metric{{border:1px solid var(--line);border-radius:8px;padding:14px;background:#fff}}
    .value{{font-size:24px;font-weight:800;margin-top:4px}}
    table{{width:100%;border-collapse:collapse;margin-top:10px;font-size:14px}}
    th,td{{border:1px solid var(--line);padding:10px;vertical-align:top;text-align:left}}
    th{{background:#f4f7fd}}
    ul{{margin:8px 0 0 18px;padding:0}} li{{margin:6px 0}}
    code{{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;background:#f3f6fb;border:1px solid #e0e7f1;border-radius:4px;padding:1px 5px;font-size:12px}}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>EchoMemory Real-Code Behavior / Cost Bridge</h1>
      <p class="muted">
        这页不重新跑 benchmark，而是直接复用现有 21-case real-code subset 的结果，
        从真实 SearchService 行为里抽出更接近系统讨论的 proxy：每个 family 平均会拉多少 evidence、主要靠哪类 evidence、什么情况下触发 expand、最常缺什么。
      </p>
      <div class="chips">
        <span class="chip">real code</span>
        <span class="chip">21 cases</span>
        <span class="chip">family-level proxy</span>
        <span class="chip">no dataset hack</span>
      </div>
      <div class="metric-grid">
        <div class="metric"><div class="muted">Cases</div><div class="value">{esc(payload['summary'].get('total_cases', 21))}</div></div>
        <div class="metric"><div class="muted">Structural Passes</div><div class="value">{esc(payload['summary'].get('passed_cases', 21))}</div></div>
        <div class="metric"><div class="muted">Review-OK</div><div class="value">{esc(payload['summary'].get('review_ok_cases', 11))}</div></div>
        <div class="metric"><div class="muted">Families</div><div class="value">{esc(len(families))}</div></div>
      </div>
    </section>

    <section class="panel">
      <h2>聚合观察</h2>
      <ul>
        <li><b>temporal</b> family 平均 evidence 数不高，但几乎总要同时看到 chronology 和 graph-like support 才容易 review=ok。</li>
        <li><b>relational</b> family 即使 graph path 强，也常单独暴露 <code>fact_grounding</code> 缺口，这说明 graph 和 atom 在真实代码里是互补的。</li>
        <li><b>visual</b> family 的主要风险不是“完全没东西”，而是只有 overview / text-like support，没有真正 image-grounded evidence。</li>
        <li><b>factual</b> family 说明 overview-level hit 和 atom-grounded hit 在 review 里应分开看，不该混成一个“相关就行”。</li>
      </ul>
    </section>

    <section class="panel">
      <h2>按 Family 汇总</h2>
      <table>
        <thead>
          <tr>
            <th>Family</th>
            <th>Review-OK</th>
            <th>Avg Total</th>
            <th>Avg Temporal</th>
            <th>Avg Graph</th>
            <th>Avg Atom</th>
            <th>Avg Episode</th>
            <th>Avg Overview</th>
            <th>Expand Cases</th>
            <th>Top Missing</th>
            <th>Top Expand</th>
          </tr>
        </thead>
        <tbody>
          {''.join(fam_rows)}
        </tbody>
      </table>
    </section>

    <section class="panel">
      <h2>逐题行为明细</h2>
      <table>
        <thead>
          <tr>
            <th>Case</th>
            <th>Family</th>
            <th>Self-check</th>
            <th>Review-OK</th>
            <th>Total</th>
            <th>Temporal</th>
            <th>Graph</th>
            <th>Atom</th>
            <th>Missing</th>
            <th>Expand</th>
            <th>Note</th>
          </tr>
        </thead>
        <tbody>
          {''.join(case_rows)}
        </tbody>
      </table>
    </section>

    <section class="panel">
      <h2>这页适合怎么用</h2>
      <ul>
        <li>它可以作为论文里 nano cost profile 和大规模 benchmark 之间的中间桥。</li>
        <li>它不声称真实 latency，只说明真实代码路径上的 evidence shape 和 expand pattern。</li>
        <li>如果后面要做更正式的系统表，这页已经给了最值得先量化的几列：family、evidence counts、missing type、expand type。</li>
      </ul>
    </section>
  </div>
</body>
</html>
"""


def main() -> None:
    payload = summarize(load())
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render(payload), encoding="utf-8")
    print(f"wrote {OUT_JSON}")
    print(f"wrote {OUT_HTML}")


if __name__ == "__main__":
    main()
