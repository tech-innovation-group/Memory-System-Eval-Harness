#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from nano_memory_os_dual_backbone import (
    OUT_HTML as BASE_OUT_HTML,
    QueryPlan,
    build_demo_memory,
)


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_contract_ordered_second_pass_ablation_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_contract_ordered_second_pass_ablation_20260615.html"
)


def esc(value: Any) -> str:
    return html.escape(str(value))


def broad_required_layers(plan: QueryPlan) -> list[str]:
    family = plan.family
    if family == "temporal":
        return ["temporal_tree", "event", "fact", "episode"]
    if family == "relational":
        return ["graph", "entity", "event", "fact"]
    if family == "plan":
        return ["event", "fact", "temporal_tree"]
    if family == "visual":
        return ["image_evidence", "fact", "entity"]
    return ["fact", "event", "entity"]


def coverage(required: list[str], hits: list[dict[str, Any]]) -> dict[str, Any]:
    present = sorted({str(hit.get("layer", "")).strip() for hit in hits if str(hit.get("layer", "")).strip()})
    matched = [layer for layer in required if layer in present]
    missing = [layer for layer in required if layer not in matched]
    return {
        "required_layers": required,
        "present_layers": present,
        "matched_layers": matched,
        "missing_layers": missing,
        "contract_ok": not missing,
        "coverage_ratio": round((len(matched) / len(required)) if required else 1.0, 3),
    }


def dedupe_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for hit in hits:
        key = (str(hit.get("source", "")), str(hit.get("layer", "")))
        if key not in deduped or float(hit.get("score", 0.0)) > float(deduped[key].get("score", 0.0)):
            deduped[key] = hit
    return sorted(deduped.values(), key=lambda x: float(x.get("score", 0.0)), reverse=True)


def run_policy(mem: Any, query: str, query_time: str, policy: str) -> dict[str, Any]:
    plan = mem.plan(query)
    if policy == "mode_only_fixed":
        required = broad_required_layers(plan)
        readers = ["tree", "episode", "atom", "graph"]
    elif policy == "typed_planner_ordered":
        required = list(plan.must_have_layers)
        readers = [plan.primary_reader] + list(plan.supporting_readers)
    else:
        raise ValueError(policy)

    used_readers: list[str] = []
    hits = [asdict(hit) for hit in mem._read(plan.primary_reader, query, query_time)]
    used_readers.append(plan.primary_reader)
    current = coverage(required, hits)

    for reader in readers:
        if reader == plan.primary_reader:
            continue
        if current["contract_ok"]:
            break
        new_hits = [asdict(hit) for hit in mem._read(reader, query, query_time)]
        if new_hits:
            used_readers.append(reader)
            hits = dedupe_hits(hits + new_hits)
            current = coverage(required, hits)

    return {
        "policy": policy,
        "plan": asdict(plan),
        "required_layers": required,
        "used_readers": used_readers,
        "coverage": current,
        "top_hits": hits[:6],
    }


def main() -> None:
    mem = build_demo_memory()
    cases = [
        {
            "case_id": "temporal",
            "query": "When did Aria sign the Riverside lease?",
            "query_time": "2026-03-11T10:00:00Z",
            "why": "需要 chronology skeleton，再配事件 grounding。",
        },
        {
            "case_id": "relational",
            "query": "Who helped Aria with the visa checklist?",
            "query_time": "2026-04-05T10:00:00Z",
            "why": "需要 graph connectivity 与 fact grounding。",
        },
        {
            "case_id": "plan",
            "query": "What does Aria plan to do after joining Orchard Labs?",
            "query_time": "2026-04-06T10:00:00Z",
            "why": "需要计划和先前事件有联系，但不必把所有层都凑齐。",
        },
        {
            "case_id": "visual",
            "query": "What address was shown in the lease screenshot?",
            "query_time": "2026-03-11T10:00:00Z",
            "why": "需要 image evidence 是一等对象。",
        },
    ]

    rows: list[dict[str, Any]] = []
    policy_totals = {
        "mode_only_fixed": {"contract_ok": 0, "avg_readers": 0.0},
        "typed_planner_ordered": {"contract_ok": 0, "avg_readers": 0.0},
    }

    for case in cases:
        row = {"case_id": case["case_id"], "query": case["query"], "why": case["why"], "policies": {}}
        for policy in policy_totals:
            result = run_policy(mem, case["query"], case["query_time"], policy)
            row["policies"][policy] = result
            if result["coverage"]["contract_ok"]:
                policy_totals[policy]["contract_ok"] += 1
            policy_totals[policy]["avg_readers"] += len(result["used_readers"])
        rows.append(row)

    for policy in policy_totals:
        policy_totals[policy]["avg_readers"] = round(policy_totals[policy]["avg_readers"] / max(len(cases), 1), 2)

    payload = {
        "source_reference": str(BASE_OUT_HTML),
        "summary": policy_totals,
        "cases": rows,
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(payload), encoding="utf-8")


def render_html(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    rows = payload["cases"]
    case_rows = []
    for row in rows:
        mode_only = row["policies"]["mode_only_fixed"]
        typed = row["policies"]["typed_planner_ordered"]
        case_rows.append(
            f"""
            <tr>
              <td><b>{esc(row['case_id'])}</b><br><span class="muted">{esc(row['query'])}</span></td>
              <td>{esc(row['why'])}</td>
              <td>
                required: <code>{esc(mode_only['required_layers'])}</code><br>
                readers: <code>{esc(mode_only['used_readers'])}</code><br>
                coverage: <b>{esc(mode_only['coverage']['coverage_ratio'])}</b><br>
                ok: <b>{esc(mode_only['coverage']['contract_ok'])}</b>
              </td>
              <td>
                required: <code>{esc(typed['required_layers'])}</code><br>
                readers: <code>{esc(typed['used_readers'])}</code><br>
                coverage: <b>{esc(typed['coverage']['coverage_ratio'])}</b><br>
                ok: <b>{esc(typed['coverage']['contract_ok'])}</b>
              </td>
            </tr>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Nano Contract / Ordered Second Pass Ablation</title>
  <style>
    :root{{--bg:#f5f7fb;--panel:#fff;--line:#dbe3ee;--text:#172233;--muted:#617186;--blue:#245cff;--green:#11885e;--amber:#a86a00;--code:#f3f6fb}}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
    .page{{max-width:1120px;margin:0 auto;padding:28px 20px 56px}}
    .hero,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:16px}}
    .hero{{padding:26px;background:linear-gradient(135deg,#fff 0%,#eef4ff 100%)}}
    h1,h2{{margin:0 0 10px;line-height:1.28}} h1{{font-size:30px}} h2{{font-size:20px;padding-bottom:8px;border-bottom:1px solid var(--line)}}
    p{{margin:8px 0}} .muted{{color:var(--muted)}}
    table{{width:100%;border-collapse:collapse;margin-top:10px;font-size:14px}}
    th,td{{border:1px solid var(--line);padding:10px;vertical-align:top;text-align:left}}
    th{{background:#f4f7fd}}
    code{{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;background:var(--code);border:1px solid #e0e7f1;border-radius:4px;padding:1px 5px;font-size:12px;word-break:break-all}}
    .callout{{border-left:4px solid var(--blue);background:#f4f8ff;padding:12px 14px;border-radius:6px;margin-top:10px}}
    .ok{{color:var(--green);font-weight:700}} .warn{{color:var(--amber);font-weight:700}}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>Nano Ablation: mode-only vs typed-contract</h1>
      <p class="muted">这不是 benchmark 分数对比，而是一个结构性对照：比较“粗粒度 mode + 固定 second-pass 顺序”和“typed contract + planner reader 顺序”在同一套小记忆系统里的行为差异。</p>
      <div class="callout">
        目标是说明：<b>结构改进的收益不是来自数据集关键词，而是来自更窄的证据契约和更合理的 supporting retrieval 路径。</b>
      </div>
    </section>

    <section class="panel">
      <h2>Summary</h2>
      <table>
        <thead><tr><th>Policy</th><th>Contract OK</th><th>Average Readers Used</th></tr></thead>
        <tbody>
          <tr><td><code>mode_only_fixed</code></td><td>{esc(summary['mode_only_fixed']['contract_ok'])}/4</td><td>{esc(summary['mode_only_fixed']['avg_readers'])}</td></tr>
          <tr><td><code>typed_planner_ordered</code></td><td>{esc(summary['typed_planner_ordered']['contract_ok'])}/4</td><td>{esc(summary['typed_planner_ordered']['avg_readers'])}</td></tr>
        </tbody>
      </table>
    </section>

    <section class="panel">
      <h2>Per Case</h2>
      <table>
        <thead>
          <tr>
            <th style="width:20%">Case</th>
            <th style="width:18%">Why</th>
            <th style="width:31%">mode_only_fixed</th>
            <th>typed_planner_ordered</th>
          </tr>
        </thead>
        <tbody>
          {''.join(case_rows)}
        </tbody>
      </table>
    </section>
  </div>
</body>
</html>"""


if __name__ == "__main__":
    main()
