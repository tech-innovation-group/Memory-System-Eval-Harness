#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def f0(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def pct(part: float, total: float) -> str:
    if total <= 0:
        return "-"
    return f"{part / total * 100:.2f}%"


def ms_text(value: float) -> str:
    return f"{value:,.1f} ms"


def s_text(value_ms: float) -> str:
    return f"{value_ms / 1000.0:,.2f} s"


def parse_internal_llm_log(log_path: Path) -> dict[str, dict[str, float | int]]:
    pattern = re.compile(r"call_site=(?P<site>\S+).*?latency=(?P<lat>[\d.]+)ms")
    buckets: dict[str, dict[str, float | int]] = {}
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.search(line)
        if not match:
            continue
        site = match.group("site")
        lat = float(match.group("lat"))
        bucket = buckets.setdefault(
            site,
            {"calls": 0, "zero_calls": 0, "nonzero_calls": 0, "total_ms": 0.0, "max_ms": 0.0},
        )
        bucket["calls"] = int(bucket["calls"]) + 1
        bucket["total_ms"] = float(bucket["total_ms"]) + lat
        bucket["max_ms"] = max(float(bucket["max_ms"]), lat)
        if lat == 0:
            bucket["zero_calls"] = int(bucket["zero_calls"]) + 1
        else:
            bucket["nonzero_calls"] = int(bucket["nonzero_calls"]) + 1
    for bucket in buckets.values():
        calls = max(int(bucket["calls"]), 1)
        nonzero = max(int(bucket["nonzero_calls"]), 1)
        bucket["total_ms"] = round(float(bucket["total_ms"]), 1)
        bucket["avg_ms"] = round(float(bucket["total_ms"]) / calls, 1)
        bucket["avg_nonzero_ms"] = round(float(bucket["total_ms"]) / nonzero, 1)
        bucket["max_ms"] = round(float(bucket["max_ms"]), 1)
    return buckets


def build_stage_stats(rows: list[dict[str, str]]) -> tuple[dict[str, dict[str, float]], float]:
    end_to_end_vals = [f0(row.get("end_to_end_ms")) for row in rows]
    total_end_to_end_ms = sum(end_to_end_vals)
    keys = [
        ("primary_search_ms", "Primary sdk.search"),
        ("followup_search_ms", "Follow-up sdk.search"),
        ("overview_enrichment_ms", "Overview enrichment"),
        ("segment_readback_ms", "Segment readback"),
        ("local_evidence_ms", "Local evidence"),
        ("dedup_ms", "Dedup"),
        ("rank_ms", "Ranking"),
        ("postprocess_ms", "Retrieval postprocess"),
        ("prefetch_ms", "Initial prefetch"),
        ("memory_format_ms", "Memory format"),
        ("message_build_ms", "Prompt assembly"),
        ("injection_total_ms", "Injection total"),
        ("llm_answer_ms", "Answer LLM"),
        ("llm_fallback_ms", "Fallback LLM"),
        ("llm_rescue_ms", "Rescue LLM"),
        ("llm_refinement_ms", "Refinement LLM"),
        ("llm_total_ms", "All answer-side LLM"),
    ]
    stats: dict[str, dict[str, float]] = {}
    for key, label in keys:
        vals = [f0(row.get(key)) for row in rows]
        stats[key] = {
            "label": label,
            "sum_ms": round(sum(vals), 1),
            "avg_ms": round(sum(vals) / len(vals), 1) if vals else 0.0,
            "p50_ms": round(statistics.median(vals), 1) if vals else 0.0,
            "max_ms": round(max(vals), 1) if vals else 0.0,
            "share": 0.0 if total_end_to_end_ms <= 0 else round(sum(vals) / total_end_to_end_ms * 100.0, 2),
        }
    return stats, total_end_to_end_ms


def render_report(
    *,
    rows: list[dict[str, str]],
    summary: dict[str, Any],
    judge: dict[str, Any],
    manifest: dict[str, Any],
    log_summary: dict[str, dict[str, float | int]],
    title: str,
    output_path: Path,
    source_paths: dict[str, Path],
) -> str:
    stage_stats, total_end_to_end_ms = build_stage_stats(rows)
    end_to_end_vals = [f0(row.get("end_to_end_ms")) for row in rows]
    avg_end_to_end_ms = round(sum(end_to_end_vals) / len(end_to_end_vals), 1) if end_to_end_vals else 0.0
    p50_end_to_end_ms = round(statistics.median(end_to_end_vals), 1) if end_to_end_vals else 0.0
    max_end_to_end_ms = round(max(end_to_end_vals), 1) if end_to_end_vals else 0.0
    answer_refined_count = sum(1 for row in rows if str(row.get("answer_refined") or "").lower() == "true")
    retrieval_empty_count = sum(1 for row in rows if str(row.get("retrieval_status") or "") == "empty")
    overhead_vals = [
        f0(row.get("end_to_end_ms")) - f0(row.get("injection_total_ms")) - f0(row.get("llm_total_ms"))
        for row in rows
    ]
    avg_overhead_ms = round(sum(overhead_vals) / len(overhead_vals), 1) if overhead_vals else 0.0
    p50_overhead_ms = round(statistics.median(overhead_vals), 1) if overhead_vals else 0.0

    stage_rows = []
    for key in [
        "primary_search_ms",
        "followup_search_ms",
        "overview_enrichment_ms",
        "segment_readback_ms",
        "local_evidence_ms",
        "dedup_ms",
        "rank_ms",
        "postprocess_ms",
        "prefetch_ms",
        "memory_format_ms",
        "message_build_ms",
        "injection_total_ms",
        "llm_answer_ms",
        "llm_fallback_ms",
        "llm_rescue_ms",
        "llm_refinement_ms",
        "llm_total_ms",
    ]:
        item = stage_stats[key]
        stage_rows.append(
            f"""
            <tr>
              <td>{esc(item['label'])}</td>
              <td>{ms_text(float(item['avg_ms']))}</td>
              <td>{ms_text(float(item['p50_ms']))}</td>
              <td>{ms_text(float(item['max_ms']))}</td>
              <td>{s_text(float(item['sum_ms']))}</td>
              <td>{item['share']:.2f}%</td>
            </tr>
            """
        )

    llm_rows = []
    for site, bucket in sorted(log_summary.items(), key=lambda item: float(item[1].get("total_ms") or 0.0), reverse=True):
        llm_rows.append(
            f"""
            <tr>
              <td>{esc(site)}</td>
              <td>{int(bucket['calls'])}</td>
              <td>{int(bucket['nonzero_calls'])}</td>
              <td>{int(bucket['zero_calls'])}</td>
              <td>{s_text(float(bucket['total_ms']))}</td>
              <td>{ms_text(float(bucket['avg_ms']))}</td>
              <td>{ms_text(float(bucket['avg_nonzero_ms']))}</td>
              <td>{ms_text(float(bucket['max_ms']))}</td>
            </tr>
            """
        )

    slow_rows = []
    for row in sorted(rows, key=lambda item: f0(item.get("end_to_end_ms")), reverse=True)[:10]:
        slow_rows.append(
            f"""
            <tr>
              <td>{esc(row.get('question_id'))}</td>
              <td>{esc(row.get('result') or '-')}</td>
              <td>{esc(row.get('retrieval_status') or '-')}</td>
              <td>{esc(row.get('answer_refined') or '-')}</td>
              <td>{ms_text(f0(row.get('end_to_end_ms')))}</td>
              <td>{ms_text(f0(row.get('injection_total_ms')))}</td>
              <td>{ms_text(f0(row.get('llm_answer_ms')))}</td>
              <td>{ms_text(f0(row.get('llm_refinement_ms')))}</td>
              <td>{ms_text(f0(row.get('llm_total_ms')))}</td>
            </tr>
            """
        )

    accuracy_text = (
        f"{int(judge.get('correct') or 0)}/{int(judge.get('count') or 0)} ({float(judge.get('accuracy') or 0) * 100:.2f}%)"
        if judge
        else "not judged in this run"
    )
    internal_total_ms = sum(float(bucket.get("total_ms") or 0.0) for bucket in log_summary.values())
    qa_only_note = "This run is QA-only on an already imported conv-30 workspace." if manifest.get("reused_import_workspace") else "This run operates on the provided workspace/account without re-importing."
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)}</title>
  <style>
    :root {{
      --bg:#ffffff; --ink:#111827; --muted:#4b5563; --line:#e5e7eb; --soft:#f8fafc; --accent:#0f766e;
      --good:#166534; --warn:#b45309; --bad:#b91c1c;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:var(--ink); line-height:1.55; }}
    header {{ padding:28px 24px 18px; border-bottom:1px solid var(--line); }}
    main {{ max-width:1320px; margin:0 auto; padding:22px 24px 48px; }}
    h1 {{ margin:0 0 6px; font-size:28px; }}
    h2 {{ margin:28px 0 12px; font-size:20px; }}
    p, li {{ margin:6px 0; font-size:14px; }}
    code {{ font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; background:var(--soft); padding:2px 5px; border-radius:4px; }}
    .small {{ color:var(--muted); font-size:12px; }}
    .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }}
    .card {{ border:1px solid var(--line); border-radius:8px; padding:14px; background:#fff; }}
    .label {{ color:var(--muted); font-size:12px; text-transform:uppercase; }}
    .stat {{ margin-top:8px; font-size:26px; font-weight:700; }}
    .note {{ border-left:4px solid var(--accent); background:#f0fdfa; padding:12px 14px; border-radius:6px; }}
    .warn {{ border-left-color:var(--warn); background:#fffbeb; }}
    table {{ width:100%; border-collapse:collapse; }}
    th, td {{ border:1px solid var(--line); padding:8px 10px; text-align:left; vertical-align:top; font-size:13px; }}
    th {{ background:var(--soft); position:sticky; top:0; }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:8px; }}
    .paths {{ display:grid; grid-template-columns:200px 1fr; gap:8px 12px; }}
    @media (max-width:1100px) {{ .grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
    @media (max-width:760px) {{ header, main {{ padding-left:16px; padding-right:16px; }} .grid {{ grid-template-columns:1fr; }} .paths {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
<header>
  <div class="small">Generated at {esc(datetime.now().isoformat(timespec="seconds"))}</div>
  <h1>{esc(title)}</h1>
  <p class="small">{esc(qa_only_note)}</p>
</header>
<main>
  <section class="note">
    <p><strong>Bottom line.</strong> On this conv-30 QA-only rerun, EchoMemory answer-time injection averaged <code>{ms_text(stage_stats['injection_total_ms']['avg_ms'])}</code> per question, while answer-side LLM calls averaged <code>{ms_text(stage_stats['llm_total_ms']['avg_ms'])}</code>. Median end-to-end QA time was <code>{ms_text(p50_end_to_end_ms)}</code>.</p>
    <p><strong>What dominates.</strong> The retrieval side is mostly <code>sdk.search</code> itself: primary search averaged <code>{ms_text(stage_stats['primary_search_ms']['avg_ms'])}</code>, follow-up search averaged <code>{ms_text(stage_stats['followup_search_ms']['avg_ms'])}</code>, and the rest of injection assembly was close to zero under this baseline config.</p>
    <p><strong>Internal EchoMemory LLM time.</strong> The runtime log shows <code>{len(log_summary)}</code> internal call-site group(s) during QA. In this rerun it is effectively all <code>embedding</code>, totaling <code>{s_text(internal_total_ms)}</code>. Rule-only search intent means there is no extra <code>search_intent</code> LLM phase in this run.</p>
  </section>

  <div class="grid" style="margin-top:14px">
    <section class="card"><div class="label">Judge Accuracy</div><div class="stat">{esc(accuracy_text)}</div><p>{'Reference from the judged rerun.' if judge else 'This QA-only rerun did not execute judge.'}</p></section>
    <section class="card"><div class="label">Rows</div><div class="stat">{len(rows)}</div><p>conv-30 eligible questions</p></section>
    <section class="card"><div class="label">Avg End-to-End</div><div class="stat">{s_text(avg_end_to_end_ms)}</div><p>p50 {s_text(p50_end_to_end_ms)} / max {s_text(max_end_to_end_ms)}</p></section>
    <section class="card"><div class="label">Avg Injection</div><div class="stat">{s_text(stage_stats['injection_total_ms']['avg_ms'])}</div><p>{pct(stage_stats['injection_total_ms']['sum_ms'], total_end_to_end_ms)} of QA wall time</p></section>
  </div>

  <div class="grid" style="margin-top:12px">
    <section class="card"><div class="label">Avg Answer LLM</div><div class="stat">{s_text(stage_stats['llm_answer_ms']['avg_ms'])}</div><p>sum {s_text(stage_stats['llm_answer_ms']['sum_ms'])}</p></section>
    <section class="card"><div class="label">Avg Refinement LLM</div><div class="stat">{s_text(stage_stats['llm_refinement_ms']['avg_ms'])}</div><p>{answer_refined_count} rows ended up refined</p></section>
    <section class="card"><div class="label">Avg All LLM</div><div class="stat">{s_text(stage_stats['llm_total_ms']['avg_ms'])}</div><p>{pct(stage_stats['llm_total_ms']['sum_ms'], total_end_to_end_ms)} of QA wall time</p></section>
    <section class="card"><div class="label">Residual Overhead</div><div class="stat">{s_text(avg_overhead_ms)}</div><p>p50 {ms_text(p50_overhead_ms)}; mostly zero, a few outliers remain</p></section>
  </div>

  <h2>Timing Breakdown</h2>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Stage</th>
          <th>Avg</th>
          <th>p50</th>
          <th>Max</th>
          <th>Sum</th>
          <th>Share Of QA Wall</th>
        </tr>
      </thead>
      <tbody>
        {''.join(stage_rows)}
      </tbody>
    </table>
  </div>

  <h2>Internal Runtime LLM Log</h2>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Call Site</th>
          <th>Calls</th>
          <th>Nonzero Calls</th>
          <th>Zero-Latency Calls</th>
          <th>Total Latency</th>
          <th>Avg</th>
          <th>Avg Nonzero</th>
          <th>Max</th>
        </tr>
      </thead>
      <tbody>
        {''.join(llm_rows)}
      </tbody>
    </table>
  </div>

  <h2>What The Time Is Made Of</h2>
  <ul>
    <li><strong>Primary retrieval:</strong> the largest injection-side component is the first batch of <code>sdk.search</code> calls, averaging <code>{ms_text(stage_stats['primary_search_ms']['avg_ms'])}</code> per question.</li>
    <li><strong>Follow-up retrieval:</strong> missing-keyword follow-up search adds another <code>{ms_text(stage_stats['followup_search_ms']['avg_ms'])}</code> on average.</li>
    <li><strong>Overview enrichment and ranking are tiny:</strong> overview enrichment averages <code>{ms_text(stage_stats['overview_enrichment_ms']['avg_ms'])}</code>, dedup <code>{ms_text(stage_stats['dedup_ms']['avg_ms'])}</code>, rank <code>{ms_text(stage_stats['rank_ms']['avg_ms'])}</code>.</li>
    <li><strong>This baseline did not use local evidence or segment readback:</strong> those stages stayed at <code>0</code>, so they are not causing latency in this rerun.</li>
    <li><strong>Prompt assembly is negligible:</strong> memory formatting and message construction are effectively sub-millisecond; the slowness is not from string assembly.</li>
    <li><strong>Answer-side LLM dominates total QA time:</strong> all answer/refine calls together took <code>{s_text(stage_stats['llm_total_ms']['sum_ms'])}</code>, versus <code>{s_text(stage_stats['injection_total_ms']['sum_ms'])}</code> for injection prep.</li>
    <li><strong>Refinement is expensive when it fires:</strong> only <code>{answer_refined_count}</code> rows ended up marked refined, but refinement still consumed <code>{s_text(stage_stats['llm_refinement_ms']['sum_ms'])}</code> total.</li>
    <li><strong>Do not use judged CSV <code>time_cost</code> for QA latency:</strong> the companion <code>local_judge.py</code> adds judge runtime into <code>time_cost</code>. This report uses row-level <code>end_to_end_ms</code> instead.</li>
  </ul>

  <h2>Slowest Questions</h2>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Question</th>
          <th>Judge</th>
          <th>Retrieval</th>
          <th>Refined</th>
          <th>End-to-End</th>
          <th>Injection</th>
          <th>Answer LLM</th>
          <th>Refinement LLM</th>
          <th>All LLM</th>
        </tr>
      </thead>
      <tbody>
        {''.join(slow_rows)}
      </tbody>
    </table>
  </div>

  <h2>Paths</h2>
  <div class="paths">
    <strong>HTML</strong><div><code>{esc(output_path)}</code></div>
    <strong>CSV</strong><div><code>{esc(source_paths['csv'])}</code></div>
    <strong>Summary</strong><div><code>{esc(source_paths['summary'])}</code></div>
    <strong>Judge</strong><div><code>{esc(source_paths['judge'])}</code></div>
    <strong>Manifest</strong><div><code>{esc(source_paths['manifest'])}</code></div>
    <strong>QA Log</strong><div><code>{esc(source_paths['log'])}</code></div>
  </div>
</main>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a conv-30 EchoMemory injection timing HTML report.")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--judge-summary", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--title", default="EchoMemory conv-30 Injection Timing Report")
    args = parser.parse_args()

    csv_path = Path(args.csv).expanduser().resolve()
    summary_path = Path(args.summary).expanduser().resolve()
    manifest_path = Path(args.manifest).expanduser().resolve()
    log_path = Path(args.log).expanduser().resolve()
    judge_path = Path(args.judge_summary).expanduser().resolve() if args.judge_summary else Path()
    output_path = Path(args.output).expanduser().resolve() if args.output else (csv_path.parent / "conv30_injection_timing_report.html")

    rows = read_csv(csv_path)
    summary = read_json(summary_path)
    judge = read_json(judge_path) if judge_path and judge_path.exists() else {}
    manifest = read_json(manifest_path)
    log_summary = parse_internal_llm_log(log_path)

    html_text = render_report(
        rows=rows,
        summary=summary,
        judge=judge,
        manifest=manifest,
        log_summary=log_summary,
        title=args.title,
        output_path=output_path,
        source_paths={
            "csv": csv_path,
            "summary": summary_path,
            "judge": judge_path if judge_path else Path(""),
            "manifest": manifest_path,
            "log": log_path,
        },
    )
    output_path.write_text(html_text, encoding="utf-8")
    print(json.dumps({"output": str(output_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
