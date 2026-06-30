#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import importlib.util
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web")
RUNS_ROOT = ROOT / "runs"
DATASET = ROOT / "dataset/full/hotpotqa_dev_distractor.json"

OPENVIKING_RUN = ROOT / "runs/formal_hotpotqa_distractor_full_openviking_20260606_1530"
OPENVIKING_FIRST500_CSV = OPENVIKING_RUN / "openviking_generic_qa_results_first500.csv"
OPENVIKING_FIRST500_SUMMARY = OPENVIKING_RUN / "first500_eval/hotpotqa_answer_summary.json"
OPENVIKING_FULL_RUNNING = OPENVIKING_RUN / "running_summary.json"

OUTPUT = ROOT / "web/static/generated-reports/hotpotqa_500_openviking_v044_vs_echomemory_20260630.html"
STATIC_MIRROR = ROOT / "static/generated-reports/hotpotqa_500_openviking_v044_vs_echomemory_20260630.html"
LATEST_OUTPUT = ROOT / "web/static/generated-reports/hotpotqa_500_openviking_v044_vs_echomemory_latest.html"
LATEST_STATIC_MIRROR = ROOT / "static/generated-reports/hotpotqa_500_openviking_v044_vs_echomemory_latest.html"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def maybe_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = load_json(path)
    return data if isinstance(data, dict) else {}


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def discover_echomemory_500_paths() -> dict[str, Path]:
    candidates: list[tuple[tuple[int, int, int, float], Path]] = []
    for root in sorted(RUNS_ROOT.glob("echomemory_hotpotqa_500_*")):
        run_dir = root / "echomemory_generic_qa"
        if not run_dir.exists():
            continue
        csv_path = run_dir / "echomemory_generic_qa_results.csv"
        running_path = run_dir / "running_summary.json"
        status_path = run_dir / "generic_qa_status.json"
        final_summary = run_dir / "summary.json"
        final_answer = run_dir / "hotpotqa_answer_summary.json"
        final_judge = run_dir / "judge_summary.json"
        rows = 0
        if csv_path.exists():
          try:
              rows = len(load_csv(csv_path))
          except Exception:
              rows = 0
        graded = 0
        if final_answer.exists():
            try:
                graded = int((load_json(final_answer) or {}).get("graded") or 0)
            except Exception:
                graded = 0
        done = int(final_summary.exists() and final_answer.exists() and graded >= 500)
        ts = root.stat().st_mtime
        candidates.append(((done, graded, rows, ts), root))

    if not candidates:
        fallback_root = RUNS_ROOT / "echomemory_hotpotqa_500_20260630_031739"
        run_dir = fallback_root / "echomemory_generic_qa"
        return {
            "root": fallback_root,
            "run": run_dir,
            "csv": run_dir / "echomemory_generic_qa_results.csv",
            "running": run_dir / "running_summary.json",
            "status": run_dir / "generic_qa_status.json",
            "final_summary": run_dir / "summary.json",
            "final_answer": run_dir / "hotpotqa_answer_summary.json",
            "final_judge": run_dir / "judge_summary.json",
        }

    _, selected_root = max(candidates, key=lambda item: item[0])
    selected_run = selected_root / "echomemory_generic_qa"
    return {
        "root": selected_root,
        "run": selected_run,
        "csv": selected_run / "echomemory_generic_qa_results.csv",
        "running": selected_run / "running_summary.json",
        "status": selected_run / "generic_qa_status.json",
        "final_summary": selected_run / "summary.json",
        "final_answer": selected_run / "hotpotqa_answer_summary.json",
        "final_judge": selected_run / "judge_summary.json",
    }


def pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.2f}%"


def num(value: float | int | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}"


def int_text(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{value:,}"


def duration_text(value: float | None) -> str:
    if value is None:
        return "-"
    if value < 60:
        return f"{value:.1f}s"
    minutes = int(value // 60)
    seconds = value - minutes * 60
    if minutes < 60:
        return f"{minutes}m {seconds:.1f}s"
    hours = int(minutes // 60)
    minutes = minutes % 60
    return f"{hours}h {minutes}m {seconds:.1f}s"


def local_datetime_text(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def compact_text(text: str, limit: int = 140) -> str:
    plain = " ".join((text or "").split())
    if len(plain) <= limit:
        return plain
    return plain[: limit - 1] + "..."


def load_metric_module() -> Any:
    script = ROOT / "scripts/hotpotqa_answer_eval.py"
    spec = importlib.util.spec_from_file_location("hotpot_eval_20260630", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def csv_time_stats(rows: list[dict[str, str]], field: str) -> dict[str, float | int | None]:
    vals = [float(row[field]) for row in rows if row.get(field)]
    if not vals:
        return {"count": 0, "total": None, "avg": None, "median": None, "min": None, "max": None}
    return {
        "count": len(vals),
        "total": sum(vals),
        "avg": sum(vals) / len(vals),
        "median": statistics.median(vals),
        "min": min(vals),
        "max": max(vals),
    }


def compute_answer_only_summary(rows: list[dict[str, str]], refs: dict[str, dict[str, Any]], metric_mod: Any) -> dict[str, Any]:
    graded: list[dict[str, Any]] = []
    by_type: dict[str, list[tuple[float, float]]] = {}
    for row in rows:
        qid = str(row.get("question_id") or row.get("sample_id") or "")
        if not qid or qid not in refs:
            continue
        gold = str(refs[qid].get("answer") or "")
        prediction = str(row.get("response") or "")
        answer_type = str(refs[qid].get("type") or row.get("category") or "unknown")
        em = float(metric_mod.exact_match(prediction, gold))
        f1 = float(metric_mod.f1_score(prediction, gold))
        graded.append(
            {
                "qid": qid,
                "question": str(refs[qid].get("question") or row.get("question") or ""),
                "gold": gold,
                "prediction": prediction,
                "type": answer_type,
                "em": em,
                "f1": f1,
            }
        )
        by_type.setdefault(answer_type, []).append((em, f1))

    def avg(items: list[float]) -> float | None:
        return (sum(items) / len(items)) if items else None

    return {
        "graded": len(graded),
        "answer_em": avg([item["em"] for item in graded]),
        "answer_f1": avg([item["f1"] for item in graded]),
        "by_type": {
            key: {
                "count": len(values),
                "answer_em": avg([pair[0] for pair in values]),
                "answer_f1": avg([pair[1] for pair in values]),
            }
            for key, values in sorted(by_type.items())
        },
        "rows": graded,
    }


def openviking_snapshot() -> dict[str, Any]:
    rows = load_csv(OPENVIKING_FIRST500_CSV)
    first500 = maybe_json(OPENVIKING_FIRST500_SUMMARY)
    full_running = maybe_json(OPENVIKING_FULL_RUNNING)
    qa_stats = csv_time_stats(rows, "time_cost")
    return {
        "label": "OpenViking v0.4.4",
        "graded": int(first500.get("graded") or len(rows)),
        "answer_em": first500.get("answer_em"),
        "answer_f1": first500.get("answer_f1"),
        "by_type": first500.get("by_type") or {},
        "qa_total_s": qa_stats["total"],
        "qa_avg_s": qa_stats["avg"],
        "qa_median_s": qa_stats["median"],
        "qa_min_s": qa_stats["min"],
        "qa_max_s": qa_stats["max"],
        "full_run_rows": full_running.get("rows"),
        "full_run_qa_total_s": full_running.get("total_qa_time_s"),
        "full_run_qa_avg_s": full_running.get("avg_qa_time_s"),
        "source_note": "基线来自 2026-06-06 的正式全量 OpenViking 运行；为了公平对比，这里取前 500 行重新用官方 HotpotQA answer-only 口径重算。",
    }


def echomemory_snapshot() -> dict[str, Any]:
    paths = discover_echomemory_500_paths()
    metric_mod = load_metric_module()
    refs = metric_mod.load_reference(DATASET)
    rows = load_csv(paths["csv"])
    running = maybe_json(paths["running"])
    status = maybe_json(paths["status"])
    final_summary = maybe_json(paths["final_summary"])
    final_answer = maybe_json(paths["final_answer"])
    final_judge = maybe_json(paths["final_judge"])
    partial_answer = compute_answer_only_summary(rows, refs, metric_mod) if rows else {}
    done = bool(final_summary and final_answer)
    timing_rows = rows
    qa_stats = csv_time_stats(timing_rows, "qa_time_s")
    inject_stats = csv_time_stats(timing_rows, "memory_injection_time_s")
    settle_stats = csv_time_stats(timing_rows, "memory_settle_wait_elapsed_s")
    e2e_stats = csv_time_stats(timing_rows, "end_to_end_time_s")
    answer_source = final_answer if final_answer else partial_answer
    samples = (partial_answer.get("rows") or [])[:6]
    rows_written = int(running.get("rows") or len(rows))
    progress_now = max(rows_written, int(status.get("job_index") or 0), len(rows))
    remaining = max(0, 500 - progress_now)
    partial_qa_total = running.get("total_qa_time_s") or qa_stats["total"]
    partial_qa_avg = running.get("avg_qa_time_s") or qa_stats["avg"]
    partial_e2e_total = running.get("total_end_to_end_time_s") or running.get("total_end_to_end_s") or e2e_stats["total"]
    partial_e2e_avg = running.get("avg_end_to_end_time_s") or running.get("avg_end_to_end_s") or e2e_stats["avg"]
    avg_e2e = final_summary.get("avg_end_to_end_time_s") if done else partial_e2e_avg
    eta_seconds = (remaining * float(avg_e2e)) if (avg_e2e is not None and remaining > 0) else 0.0
    projected_done_at = datetime.now(timezone.utc) if done else (
        datetime.now(timezone.utc) + __import__("datetime").timedelta(seconds=eta_seconds)
        if eta_seconds > 0
        else None
    )
    return {
        "label": "EchoMemory",
        "done": done,
        "stage": "final" if done else "partial",
        "metric_method": "官方 HotpotQA answer-only 汇总" if done else "基于已写出结果行的本地 answer-only 重算",
        "rows_written": rows_written,
        "job_index": status.get("job_index") or len(rows),
        "job_total": status.get("job_total"),
        "progress_now": progress_now,
        "remaining": remaining,
        "status": running.get("status") or status.get("stage") or "running",
        "checked_at": status.get("checked_at") or running.get("updated_at") or "",
        "eta_seconds": eta_seconds if eta_seconds > 0 else None,
        "projected_done_at": local_datetime_text(projected_done_at),
        "answer_em": answer_source.get("answer_em"),
        "answer_f1": answer_source.get("answer_f1"),
        "graded": int(answer_source.get("graded") or len(rows)),
        "by_type": answer_source.get("by_type") or {},
        "judge_accuracy": final_judge.get("accuracy"),
        "qa_total_s": final_summary.get("total_qa_time_s") if done else partial_qa_total,
        "qa_avg_s": final_summary.get("avg_qa_time_s") if done else partial_qa_avg,
        "inject_total_s": final_summary.get("total_memory_injection_time_s") if done else inject_stats["total"],
        "inject_avg_s": final_summary.get("avg_memory_injection_time_s") if done else inject_stats["avg"],
        "settle_total_s": final_summary.get("total_memory_settle_wait_time_s") if done else settle_stats["total"],
        "settle_avg_s": final_summary.get("avg_memory_settle_wait_time_s") if done else settle_stats["avg"],
        "e2e_total_s": final_summary.get("total_end_to_end_time_s") if done else partial_e2e_total,
        "e2e_avg_s": final_summary.get("avg_end_to_end_time_s") if done else partial_e2e_avg,
        "sample_rows": samples,
        "run_root": str(paths["root"]),
        "source_note": "EchoMemory 还在正式跑 500 题；当前 EM/F1 是基于已写出结果行做的本地 answer-only 重算，只能看趋势，不能替代最终 official summary。" if not done else "EchoMemory 正式 500 题结果已完成。",
    }


def card(title: str, value: str, note: str = "", tone: str = "") -> str:
    klass = f"metric-card {tone}".strip()
    return (
        f"<article class='{klass}'>"
        f"<span>{html.escape(title)}</span>"
        f"<strong>{html.escape(value)}</strong>"
        f"<small>{html.escape(note)}</small>"
        "</article>"
    )


def kv_row(label: str, value: str) -> str:
    return f"<div class='kv-row'><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></div>"


def render_type_rows(by_type: dict[str, Any]) -> str:
    rows = []
    for key, item in by_type.items():
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(key))}</td>"
            f"<td>{html.escape(int_text(int(item.get('count') or 0)))}</td>"
            f"<td>{html.escape(pct(item.get('answer_em')))}</td>"
            f"<td>{html.escape(pct(item.get('answer_f1')))}</td>"
            "</tr>"
        )
    return "\n".join(rows) if rows else "<tr><td colspan='4'>暂无分类型结果</td></tr>"


def render_sample_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<tr><td colspan='5'>暂无样本</td></tr>"
    parts = []
    for item in rows:
        parts.append(
            "<tr>"
            f"<td><code>{html.escape(item['qid'])}</code></td>"
            f"<td>{html.escape(compact_text(item['question'], 96))}</td>"
            f"<td>{html.escape(compact_text(item['gold'], 72))}</td>"
            f"<td>{html.escape(compact_text(item['prediction'], 96))}</td>"
            f"<td>{html.escape(pct(item['f1']))}</td>"
            "</tr>"
        )
    return "\n".join(parts)


def build_html() -> str:
    ov = openviking_snapshot()
    echo = echomemory_snapshot()
    generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    if echo["done"]:
        summary_title = "HotpotQA 500 题正式对比已完成"
        one_line = "一句话：这份报告可以直接看 500 题正式结果，比较 OpenViking v0.4.4 和 EchoMemory 的答案质量与耗时。"
    else:
        summary_title = "HotpotQA 500 题正式对比仍在进行中"
        one_line = (
            f"一句话：OpenViking v0.4.4 的 500 题基线已经固定，EchoMemory 正式跑到 {echo['rows_written']}/500，"
            "当前先看到趋势和耗时结构，不能当作最终结论。"
        )

    echo_progress_note = (
        f"当前 EchoMemory 已写出 {echo['rows_written']} 题，任务至少推进到 {echo['progress_now']}/{echo['job_total'] or 500}，"
        f"状态 {echo['status']}。"
    )
    runtime_bottleneck_note = (
        "当前正式任务没有报错退出，主要时间消耗在每题的写入记忆、等待异步索引稳定，以及随后再进入问答。"
        if not echo["done"]
        else "正式任务已经完成，下面所有结果均为完整 500 题正式结论。"
    )

    html_text = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HotpotQA 500 对比报告</title>
  <style>
    :root {{
      --bg: #f6f4ef;
      --panel: #ffffff;
      --border: #e6ded1;
      --text: #111827;
      --muted: #6b7280;
      --blue: #2563eb;
      --green: #16a34a;
      --orange: #d97706;
      --red: #dc2626;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
    }}
    .page {{
      width: min(1200px, calc(100% - 40px));
      margin: 24px auto 48px;
      display: grid;
      gap: 16px;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 18px 20px;
    }}
    h1, h2, h3, p {{ margin: 0; }}
    .hero {{
      display: grid;
      gap: 10px;
    }}
    .eyebrow {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
      letter-spacing: 0.02em;
      text-transform: uppercase;
    }}
    .hero h1 {{
      font-size: 28px;
      line-height: 1.15;
    }}
    .hero .summary {{
      font-size: 15px;
      color: var(--text);
    }}
    .hero .meta {{
      color: var(--muted);
      font-size: 13px;
    }}
    .status {{
      display: inline-flex;
      align-items: center;
      width: fit-content;
      min-height: 28px;
      padding: 0 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      border: 1px solid var(--border);
    }}
    .status.done {{ color: var(--green); background: #f0fdf4; border-color: #bbf7d0; }}
    .status.running {{ color: var(--orange); background: #fff7ed; border-color: #fed7aa; }}
    .grid-4 {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }}
    .grid-2 {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }}
    .metric-card {{
      display: grid;
      gap: 6px;
      min-height: 82px;
      padding: 14px;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: #fcfbf8;
    }}
    .metric-card span {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
    }}
    .metric-card strong {{
      font-size: 22px;
      line-height: 1.1;
    }}
    .metric-card small {{
      color: var(--muted);
      font-size: 12px;
    }}
    .metric-card.good strong {{ color: var(--green); }}
    .metric-card.warn strong {{ color: var(--orange); }}
    .metric-card.info strong {{ color: var(--blue); }}
    .comparison-table, .detail-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    .comparison-table th, .comparison-table td,
    .detail-table th, .detail-table td {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
    }}
    .comparison-table th, .detail-table th {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      background: #faf8f3;
    }}
    .note-list {{
      display: grid;
      gap: 10px;
    }}
    .note {{
      padding: 12px 14px;
      border-left: 3px solid var(--blue);
      background: #f8fbff;
      border-radius: 8px;
      font-size: 14px;
    }}
    .warning {{
      border-left-color: var(--orange);
      background: #fffaf2;
    }}
    .kv {{
      display: grid;
      gap: 8px;
    }}
    .kv-row {{
      display: grid;
      grid-template-columns: 140px minmax(0, 1fr);
      gap: 10px;
      align-items: start;
    }}
    .kv-row span {{
      color: var(--muted);
      font-size: 13px;
    }}
    .kv-row strong {{
      font-size: 13px;
      overflow-wrap: anywhere;
    }}
    code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12px;
    }}
    @media (max-width: 900px) {{
      .grid-4, .grid-2 {{ grid-template-columns: minmax(0, 1fr); }}
      .kv-row {{ grid-template-columns: minmax(0, 1fr); }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <section class="panel hero">
      <div class="eyebrow">HotpotQA Benchmark Report</div>
      <div class="status {'done' if echo['done'] else 'running'}">{'正式完成' if echo['done'] else '进行中'}</div>
      <h1>{html.escape(summary_title)}</h1>
      <p class="summary">{html.escape(one_line)}</p>
      <p class="meta">生成时间：{html.escape(generated_at)} · 数据集：HotpotQA dev distractor · 对比对象：OpenViking v0.4.4 vs EchoMemory</p>
      <p class="meta">{html.escape(runtime_bottleneck_note)}</p>
    </section>

    <section class="panel">
      <div class="grid-4">
        {card("OpenViking 500题 EM", pct(ov["answer_em"]), "前500题 answer-only", "info")}
        {card("OpenViking 500题 F1", pct(ov["answer_f1"]), "前500题 answer-only", "info")}
        {card("EchoMemory 当前 EM", pct(echo["answer_em"]), f"{echo['graded']} 题本地 answer-only 重算", "warn" if not echo['done'] else "good")}
        {card("EchoMemory 当前 F1", pct(echo["answer_f1"]), f"{echo['graded']} 题本地 answer-only 重算", "warn" if not echo['done'] else "good")}
      </div>
    </section>

    <section class="panel note-list">
      <div class="note">{html.escape(ov['source_note'])}</div>
      <div class="note warning">{html.escape(echo['source_note'])}</div>
    </section>

    <section class="panel">
      <h2 style="margin-bottom: 12px;">适合汇报的结论</h2>
      <div class="note-list">
        <div class="note">1. OpenViking v0.4.4 的前 500 题 answer-only 基线目前是 EM {pct(ov['answer_em'])}、F1 {pct(ov['answer_f1'])}，这是当前最稳定、最完整的对照组。</div>
        <div class="note">2. EchoMemory 的 500 题正式任务还没有跑完，所以现在看到的 EM {pct(echo['answer_em'])}、F1 {pct(echo['answer_f1'])} 是按已写出结果行本地重算出来的中间趋势，不能直接当作最终 official summary。</div>
        <div class="note">3. 从已经写出的样本看，EchoMemory 最大耗时不在“最终回答”本身，而在“写入记忆 + 等待索引完成”这两个前置阶段。</div>
        <div class="note">4. 这次报告的官方口径是 answer-only EM/F1，也就是只比较“模型最后答案和标准答案像不像”；supporting facts 和 joint 指标目前都没有正式算进来。</div>
        <div class="note">5. 按当前平均端到端速度估算，EchoMemory 剩余 {int_text(echo['remaining'])} 题大约还需要 {duration_text(echo['eta_seconds'])}，预计完成时刻约为 {echo['projected_done_at']}。</div>
      </div>
    </section>

    <section class="panel">
      <h2 style="margin-bottom: 12px;">核心对比表</h2>
      <table class="comparison-table">
        <thead>
          <tr>
            <th>系统</th>
            <th>问题数</th>
            <th>Answer EM</th>
            <th>Answer F1</th>
            <th>Judge Accuracy</th>
            <th>平均 QA</th>
            <th>总 QA</th>
            <th>平均端到端</th>
            <th>总端到端</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>OpenViking v0.4.4</td>
            <td>{html.escape(int_text(ov['graded']))}</td>
            <td>{html.escape(pct(ov['answer_em']))}</td>
            <td>{html.escape(pct(ov['answer_f1']))}</td>
            <td>-</td>
            <td>{html.escape(duration_text(ov['qa_avg_s']))}</td>
            <td>{html.escape(duration_text(ov['qa_total_s']))}</td>
            <td>-</td>
            <td>-</td>
          </tr>
          <tr>
            <td>EchoMemory {("(进行中，本地重算)" if not echo['done'] else "(正式完成)")}</td>
            <td>{html.escape(int_text(echo['graded']))}</td>
            <td>{html.escape(pct(echo['answer_em']))}</td>
            <td>{html.escape(pct(echo['answer_f1']))}</td>
            <td>{html.escape(pct(echo['judge_accuracy']))}</td>
            <td>{html.escape(duration_text(echo['qa_avg_s']))}</td>
            <td>{html.escape(duration_text(echo['qa_total_s']))}</td>
            <td>{html.escape(duration_text(echo['e2e_avg_s']))}</td>
            <td>{html.escape(duration_text(echo['e2e_total_s']))}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel grid-2">
      <div>
        <h2 style="margin-bottom: 12px;">评价指标怎么理解</h2>
        <div class="kv">
          {kv_row("Answer EM", "Exact Match。看模型答案是否和标准答案标准化后完全一样。最严格。")}
          {kv_row("Answer F1", "部分匹配分数。即使答案不完全一样，只要关键词重叠较多，也会给分。比 EM 更宽松。")}
          {kv_row("Judge Accuracy", "如果有正式 judge，就表示判分模型认为“答对”的比例。当前 EchoMemory 正式 judge 还没产出。")}
          {kv_row("Answer-only", "这次官方对比只算答案，不算 supporting facts 命中，也不算 joint EM/F1。EchoMemory 当前中间分数也是按这个口径本地重算。")}
        </div>
      </div>
      <div>
        <h2 style="margin-bottom: 12px;">当前任务状态</h2>
        <div class="kv">
          {kv_row("EchoMemory进度", echo_progress_note)}
          {kv_row("最近状态时间", echo["checked_at"] or "-")}
          {kv_row("剩余题数", int_text(echo["remaining"]))}
          {kv_row("预计剩余时间", duration_text(echo["eta_seconds"]))}
          {kv_row("预计完成时刻", echo["projected_done_at"])}
          {kv_row("OpenViking来源", "2026-06-06 正式全量运行，前500题重新按官方口径重算。")}
          {kv_row("当前分数口径", echo["metric_method"])}
          {kv_row("结论可信度", "OpenViking 高；EchoMemory 当前为中间态，只能看趋势。")}
        </div>
      </div>
    </section>

    <section class="panel grid-2">
      <div>
        <h2 style="margin-bottom: 12px;">按题型看答案质量</h2>
        <h3 style="margin: 0 0 8px;">OpenViking v0.4.4</h3>
        <table class="detail-table">
          <thead><tr><th>题型</th><th>题数</th><th>EM</th><th>F1</th></tr></thead>
          <tbody>{render_type_rows(ov["by_type"])}</tbody>
        </table>
        <h3 style="margin: 14px 0 8px;">EchoMemory 当前已写出样本</h3>
        <table class="detail-table">
          <thead><tr><th>题型</th><th>题数</th><th>EM</th><th>F1</th></tr></thead>
          <tbody>{render_type_rows(echo["by_type"])}</tbody>
        </table>
      </div>
      <div>
        <h2 style="margin-bottom: 12px;">耗时拆解</h2>
        <div class="kv">
          {kv_row("OpenViking 平均 QA", duration_text(ov["qa_avg_s"]))}
          {kv_row("OpenViking QA 中位数", duration_text(ov["qa_median_s"]))}
          {kv_row("EchoMemory 平均写入", duration_text(echo["inject_avg_s"]))}
          {kv_row("EchoMemory 平均索引等待", duration_text(echo["settle_avg_s"]))}
          {kv_row("EchoMemory 平均 QA", duration_text(echo["qa_avg_s"]))}
          {kv_row("EchoMemory 平均端到端", duration_text(echo["e2e_avg_s"]))}
          {kv_row("当前正式运行目录", echo["run_root"])}
        </div>
        <div class="note warning" style="margin-top: 12px;">对 EchoMemory 来说，端到端时间 = 写入记忆 + 等索引就绪 + 最终问答。对 OpenViking 来说，目前最稳定的是纯 QA 时间。</div>
      </div>
    </section>

    <section class="panel">
      <h2 style="margin-bottom: 12px;">EchoMemory 当前已写出样本</h2>
      <table class="detail-table">
        <thead>
          <tr>
            <th>question_id</th>
            <th>问题</th>
            <th>Gold Answer</th>
            <th>Predicted Answer</th>
            <th>F1</th>
          </tr>
        </thead>
        <tbody>
          {render_sample_rows(echo["sample_rows"])}
        </tbody>
      </table>
    </section>

    <section class="panel">
      <h2 style="margin-bottom: 12px;">说明与限制</h2>
      <div class="note-list">
        <div class="note">1. OpenViking 的前 500 题耗时是直接从结果 CSV 的 <code>time_cost</code> 字段实算，当前平均约 {duration_text(ov['qa_avg_s'])}。</div>
        <div class="note">2. EchoMemory 当前只完成了 {echo['rows_written']} 行结果写出，所以 EM/F1 和耗时拆解都只是“到目前为止”的真实数字；其中 EM/F1 还是本地 answer-only 重算，不是正式 final summary。</div>
        <div class="note">3. EchoMemory 当前样本里可以看到一些答案还残留工具调用或查询痕迹，这会直接拉低 answer-only EM/F1。</div>
        <div class="note">4. 等 EchoMemory 500 题正式跑完后，这份 HTML 只需要重跑同一个脚本，就会自动切换成最终版。</div>
      </div>
    </section>
  </div>
</body>
</html>
"""
    return html_text


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    STATIC_MIRROR.parent.mkdir(parents=True, exist_ok=True)
    LATEST_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    LATEST_STATIC_MIRROR.parent.mkdir(parents=True, exist_ok=True)
    report = build_html()
    OUTPUT.write_text(report, encoding="utf-8")
    STATIC_MIRROR.write_text(report, encoding="utf-8")
    LATEST_OUTPUT.write_text(report, encoding="utf-8")
    LATEST_STATIC_MIRROR.write_text(report, encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
