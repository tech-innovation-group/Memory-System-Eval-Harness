#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import statistics
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import nano_memory_os_dual_backbone as mod


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_memory_os_cost_profile_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_memory_os_cost_profile_20260615.html"
)

REPEATS = 2000
VARIANTS = ("flat_text", "dual_backbone", "contract_aware")
CASE_SPECS = [
    {
        "case_id": "c1_temporal_absolute",
        "query": "When did Aria sign the Riverside lease?",
        "query_time": "2026-03-11T10:00:00Z",
        "note": "Story time should beat mention time.",
    },
    {
        "case_id": "c2_temporal_relative",
        "query": "What happened yesterday?",
        "query_time": "2026-05-10T20:00:00Z",
        "note": "Relative time should use query anchor.",
    },
    {
        "case_id": "c3_relational",
        "query": "Who helped Aria with the visa checklist?",
        "query_time": "2026-04-05T10:00:00Z",
        "note": "Relation query should be graph-first.",
    },
    {
        "case_id": "c4_plan",
        "query": "What does Aria plan to do after joining Orchard Labs?",
        "query_time": "2026-04-06T10:00:00Z",
        "note": "Plan query should keep event/fact support.",
    },
    {
        "case_id": "c5_visual",
        "query": "What address was shown in the lease screenshot?",
        "query_time": "2026-03-11T10:00:00Z",
        "note": "Visual evidence should be first-class.",
    },
    {
        "case_id": "c6_general",
        "query": "Where did Aria join in February?",
        "query_time": "2026-02-16T09:00:00Z",
        "note": "General factual query should still work.",
    },
]


def esc(value: Any) -> str:
    return html.escape(str(value))


def summarize_variant(mem: mod.NanoMemoryOSDualBackbone, case: dict[str, Any], variant: str) -> dict[str, Any]:
    samples_us: list[float] = []
    latest: dict[str, Any] | None = None
    for _ in range(REPEATS):
        t0 = time.perf_counter_ns()
        latest = mem.run_query(case["query"], case["query_time"], variant)
        elapsed_us = (time.perf_counter_ns() - t0) / 1000.0
        samples_us.append(elapsed_us)
    assert latest is not None
    hits = latest.get("hits", [])
    coverage = latest.get("coverage", {})
    second = latest.get("second_pass_readers", [])
    unique_layers = sorted({hit.get("layer", "") for hit in hits if hit.get("layer")})
    readers_touched = 1 + len(second) if variant != "flat_text" else 1
    return {
        "variant": variant,
        "hits_returned": len(hits),
        "unique_layers": unique_layers,
        "coverage_ratio": coverage.get("coverage_ratio", 0.0),
        "contract_ok": bool(coverage.get("contract_ok")),
        "second_pass_readers": second,
        "reader_count": readers_touched,
        "mean_us": round(statistics.mean(samples_us), 3),
        "median_us": round(statistics.median(samples_us), 3),
        "p95_us": round(percentile(samples_us, 95), 3),
    }


def percentile(values: list[float], p: int) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    idx = min(len(ordered) - 1, max(0, int(round((p / 100.0) * (len(ordered) - 1)))))
    return ordered[idx]


def build_results() -> dict[str, Any]:
    mem = mod.build_demo_memory()
    rows: list[dict[str, Any]] = []
    aggregate: dict[str, dict[str, list[float] | int]] = {
        name: {
            "mean_us": [],
            "p95_us": [],
            "reader_count": [],
            "hits_returned": [],
            "coverage_ratio": [],
            "contract_ok": 0,
            "cases": 0,
            "second_pass_cases": 0,
        }
        for name in VARIANTS
    }
    for case in CASE_SPECS:
        entry = {
            "case_id": case["case_id"],
            "query": case["query"],
            "note": case["note"],
            "variants": {},
        }
        for variant in VARIANTS:
            summary = summarize_variant(mem, case, variant)
            entry["variants"][variant] = summary
            agg = aggregate[variant]
            for key in ("mean_us", "p95_us", "reader_count", "hits_returned", "coverage_ratio"):
                agg[key].append(summary[key])  # type: ignore[index]
            agg["cases"] += 1  # type: ignore[operator]
            if summary["contract_ok"]:
                agg["contract_ok"] += 1  # type: ignore[operator]
            if summary["second_pass_readers"]:
                agg["second_pass_cases"] += 1  # type: ignore[operator]
        rows.append(entry)
    aggregate_summary = {}
    for variant, vals in aggregate.items():
        cases = int(vals["cases"])  # type: ignore[arg-type]
        aggregate_summary[variant] = {
            "avg_mean_us": round(statistics.mean(vals["mean_us"]), 3),  # type: ignore[arg-type]
            "avg_p95_us": round(statistics.mean(vals["p95_us"]), 3),  # type: ignore[arg-type]
            "avg_reader_count": round(statistics.mean(vals["reader_count"]), 3),  # type: ignore[arg-type]
            "avg_hits_returned": round(statistics.mean(vals["hits_returned"]), 3),  # type: ignore[arg-type]
            "avg_coverage_ratio": round(statistics.mean(vals["coverage_ratio"]), 3),  # type: ignore[arg-type]
            "contract_ok_cases": int(vals["contract_ok"]),  # type: ignore[arg-type]
            "second_pass_cases": int(vals["second_pass_cases"]),  # type: ignore[arg-type]
            "cases": cases,
        }
    return {
        "repeats": REPEATS,
        "aggregate": aggregate_summary,
        "cases": rows,
    }


def render_html(payload: dict[str, Any]) -> str:
    ag = payload["aggregate"]
    case_rows = []
    for case in payload["cases"]:
        cells = []
        for variant in VARIANTS:
            v = case["variants"][variant]
            cells.append(
                f"""<td>
                <div><b>{esc(variant)}</b></div>
                <div class="mini">mean {esc(v['mean_us'])} us / p95 {esc(v['p95_us'])} us</div>
                <div class="mini">readers {esc(v['reader_count'])} / hits {esc(v['hits_returned'])}</div>
                <div class="mini">coverage {esc(v['coverage_ratio'])} / second pass {esc(v['second_pass_readers'])}</div>
                </td>"""
            )
        case_rows.append(
            f"""<tr>
            <td><b>{esc(case['case_id'])}</b><br><span class="mini">{esc(case['note'])}</span></td>
            <td>{esc(case['query'])}</td>
            {''.join(cells)}
            </tr>"""
        )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Nano Memory-OS Cost Profile</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --line:#dbe3ee; --text:#172233; --muted:#627286;
      --blue:#245cff; --green:#12895f; --amber:#a86a00; --red:#c13f36;
      --blue-soft:#eef4ff; --green-soft:#eefaf4; --amber-soft:#fff8ec; --red-soft:#fff4f2;
    }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
    .page{{max-width:1240px;margin:0 auto;padding:28px 20px 56px}}
    .hero,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:16px}}
    .hero{{padding:26px;background:linear-gradient(135deg,#fff 0%,#eef4ff 100%)}}
    h1,h2{{margin:0 0 10px;line-height:1.3}} h1{{font-size:30px}} h2{{font-size:20px;padding-bottom:8px;border-bottom:1px solid var(--line)}}
    p{{margin:8px 0}} .muted,.mini{{color:var(--muted)}} .mini{{font-size:12px}}
    .chips{{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}}
    .chip{{display:inline-block;padding:4px 10px;border-radius:999px;border:1px solid #cad7ee;background:#f8fbff;color:#274674;font-size:12px;font-weight:700}}
    .metric-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:16px}}
    .metric{{border:1px solid var(--line);border-radius:8px;padding:14px;background:#fff}}
    .value{{font-size:24px;font-weight:800;margin-top:4px}}
    table{{width:100%;border-collapse:collapse;margin-top:10px;font-size:14px}}
    th,td{{border:1px solid var(--line);padding:10px;vertical-align:top;text-align:left}}
    th{{background:#f4f7fd}}
    ul{{margin:8px 0 0 18px;padding:0}} li{{margin:6px 0}}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>EchoMemory Nano: Memory-OS Cost Profile</h1>
      <p class="muted">
        这页不讨论“答得对不对”，而讨论三种路径在系统代价上的形状差异。
        这里的代价是轻量 proxy：reader 数、second pass 次数、返回 evidence 数、contract 完整率，以及本地微基准耗时。
      </p>
      <div class="chips">
        <span class="chip">generic</span>
        <span class="chip">no dataset hack</span>
        <span class="chip">systems-side evidence</span>
        <span class="chip">{payload['repeats']} repeats per case</span>
      </div>
      <div class="metric-grid">
        <div class="metric"><div class="mini">Flat avg mean</div><div class="value">{ag['flat_text']['avg_mean_us']} us</div></div>
        <div class="metric"><div class="mini">Dual avg mean</div><div class="value">{ag['dual_backbone']['avg_mean_us']} us</div></div>
        <div class="metric"><div class="mini">Contract avg mean</div><div class="value">{ag['contract_aware']['avg_mean_us']} us</div></div>
      </div>
    </section>

    <section class="panel">
      <h2>聚合结论</h2>
      <table>
        <thead>
          <tr>
            <th>Variant</th>
            <th>Avg Mean (us)</th>
            <th>Avg P95 (us)</th>
            <th>Avg Readers</th>
            <th>Avg Hits</th>
            <th>Avg Coverage</th>
            <th>Contract-OK Cases</th>
            <th>Second-pass Cases</th>
          </tr>
        </thead>
        <tbody>
          {''.join(
              f"<tr><td><b>{esc(name)}</b></td><td>{esc(vals['avg_mean_us'])}</td><td>{esc(vals['avg_p95_us'])}</td><td>{esc(vals['avg_reader_count'])}</td><td>{esc(vals['avg_hits_returned'])}</td><td>{esc(vals['avg_coverage_ratio'])}</td><td>{esc(vals['contract_ok_cases'])}/{esc(vals['cases'])}</td><td>{esc(vals['second_pass_cases'])}/{esc(vals['cases'])}</td></tr>"
              for name, vals in ag.items()
          )}
        </tbody>
      </table>
      <ul>
        <li><b>flat_text</b> 最便宜，但 contract 完整率最低，几乎没有结构保障。</li>
        <li><b>dual_backbone</b> reader 成本仍然低，但已经显著改善 evidence shape。</li>
        <li><b>contract_aware</b> 成本比 dual 略高，主要来自 second pass；收益是更高的 contract completeness，而不是单纯更多 hits。</li>
      </ul>
    </section>

    <section class="panel">
      <h2>逐题细项</h2>
      <table>
        <thead>
          <tr>
            <th style="width:14%">Case</th>
            <th style="width:22%">Query</th>
            <th style="width:21%">Flat</th>
            <th style="width:21%">Dual</th>
            <th>Contract-aware</th>
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
        <li>它不适合拿去宣称真实部署 latency。</li>
        <li>它适合在论文的 systems discussion 里解释：为什么 contract-aware second pass 是“有代价的”，但这个代价主要体现为更少量的额外 reader 调用，而不是无界扩搜。</li>
        <li>它也适合帮助决定主仓下一步要不要做更强的 gating：如果某些 family 的 coverage 改善不值得 reader 增量，就应该在真实系统里做更细的策略。</li>
      </ul>
    </section>
  </div>
</body>
</html>
"""


def main() -> None:
    payload = build_results()
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(payload), encoding="utf-8")
    print(f"Wrote {OUT_JSON}")
    print(f"Wrote {OUT_HTML}")


if __name__ == "__main__":
    main()
