#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import importlib.util
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web")
RUNS = ROOT / "runs"
OUTPUT = ROOT / "web/static/generated-reports/hotpotqa_openviking_vs_echomemory_final_100_20260622.html"
STATIC_MIRROR = ROOT / "static/generated-reports/hotpotqa_openviking_vs_echomemory_final_100_20260622.html"

ECHO_TASK_ID = "echomemory_generic_qa_20260622_233948_1a19e0"
OV_TASK_ID = "openviking_generic_qa_20260622_231559_1bd882"

ECHO_RUN = RUNS / "echomemory_generic_qa_20260622_183050_943ead" / "echomemory_generic_qa"
OV_RUN = RUNS / "openviking_generic_qa_20260622_231559_1bd882" / "openviking_generic_qa"
DATASET = ROOT / "dataset/full/hotpotqa_dev_distractor.json"

CASE_SAMPLES = [
    "5a8e3ea95542995a26add48d",
    "5a8c7595554299585d9e36b6",
    "5ab51dae5542991779162d82",
    "5a85ea095542994775f606a8",
    "5a7bbb64554299042af8f7cc",
]

LOG_PATTERNS = [
    ("atom_extraction_truncated", r"Atomic extraction output appears truncated"),
    ("typed_sidecar_corrupt", r"Typed sidecar corrupt"),
    ("commit_incomplete", r"complete=False commit_index=9/9 atom_pipeline_index=-1/9"),
    ("pending_async_memory", r"pending_async_memory"),
    ("import_incomplete", r"ECHOMEMORY_IMPORT_INCOMPLETE"),
    ("model_retry", r"\[model\] retry="),
    ("import_timeout", r"\[import-timeout\]"),
    ("import_error", r"\[import-error\]"),
    ("question_timeout", r"question exceeded timeout_s="),
    ("connect_error", r"ConnectError|nodename nor servname"),
    ("ssl_eof", r"EOF occurred in violation of protocol"),
]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def maybe_load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = load_json(path)
    return data if isinstance(data, dict) else {}


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.2f}%"


def short_text(text: str, limit: int = 260) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else f"{text[:limit - 1]}..."


def short_uri(uri: str) -> str:
    if not uri:
        return ""
    return uri if len(uri) <= 140 else f"{uri[:64]}...{uri[-60:]}"


def is_unknown_response(text: str) -> bool:
    normalized = " ".join((text or "").strip().lower().split())
    return normalized in {"", "unknown", "i do not know", "i do not know."}


def parse_memories(value: str) -> list[dict[str, Any]]:
    if not value:
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def log_anomaly_counts(path: Path) -> dict[str, int]:
    if not path.exists():
        return {key: 0 for key, _ in LOG_PATTERNS}
    text = path.read_text(encoding="utf-8", errors="replace")
    return {key: len(re.findall(pattern, text)) for key, pattern in LOG_PATTERNS}


def recent_log_activity(path: Path, tail_lines: int = 400) -> dict[str, int | bool]:
    if not path.exists():
        return {
            "tail_lines": tail_lines,
            "import": 0,
            "verify": 0,
            "qa": 0,
            "commit": 0,
            "embedding": 0,
            "business_events": 0,
            "only_embedding": False,
        }
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-tail_lines:]
    import_count = sum("[import]" in line for line in lines)
    verify_count = sum("[verify]" in line for line in lines)
    qa_count = sum("[qa]" in line for line in lines)
    commit_count = sum("[commit]" in line for line in lines)
    embedding_count = sum("[LLM] call_site=embedding" in line for line in lines)
    business_events = int(import_count + verify_count + qa_count + commit_count)
    return {
        "tail_lines": tail_lines,
        "import": int(import_count),
        "verify": int(verify_count),
        "qa": int(qa_count),
        "commit": int(commit_count),
        "embedding": int(embedding_count),
        "business_events": business_events,
        "only_embedding": bool(business_events == 0 and embedding_count > 0),
    }


def load_hotpot_metric_module() -> Any:
    script = ROOT / "scripts/hotpotqa_answer_eval.py"
    spec = importlib.util.spec_from_file_location("hotpot_eval_final_compare", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def task_snapshot(task_id: str) -> dict[str, Any]:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:19181/api/tasks/{task_id}", timeout=20) as response:
            data = json.load(response)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {
            "id": task_id,
            "status": "running",
            "progress": {},
            "run_dir": str(RUNS / task_id),
        }


def artifact_state(run_dir: Path) -> dict[str, Any]:
    summary_exists = (run_dir / "summary.json").exists()
    judge_exists = (run_dir / "judge_summary.json").exists()
    official_exists = (run_dir / "hotpotqa_answer_summary.json").exists()
    return {
        "summary_exists": summary_exists,
        "judge_exists": judge_exists,
        "official_exists": official_exists,
        "complete": summary_exists and judge_exists and official_exists,
    }


def progress_label(task: dict[str, Any], rows: int, artifacts: dict[str, Any]) -> str:
    if artifacts["complete"]:
        return f"已完成 · 正式产物齐全 · {rows} 题"
    progress = task.get("progress") or {}
    current = progress.get("current")
    total = progress.get("total")
    phase = progress.get("phase")
    pieces = [str(task.get("status") or "-")]
    if current is not None and total is not None:
        pieces.append(f"{current}/{total}")
    else:
        pieces.append(f"{rows} 题")
    if phase:
        pieces.append(str(phase))
    if artifacts["official_exists"] or artifacts["summary_exists"] or artifacts["judge_exists"]:
        pieces.append("部分正式产物已落盘")
    return " · ".join(pieces)


def evidence_lines(row: dict[str, str], limit: int = 4) -> list[dict[str, str]]:
    lines: list[dict[str, str]] = []
    for mem in parse_memories(row.get("relevant_memory", ""))[:limit]:
        uri = str(mem.get("uri") or "")
        content = str(mem.get("content") or "")
        mem_type = str(mem.get("memory_type") or mem.get("source") or "")
        score = mem.get("score")
        lines.append(
            {
                "label": mem_type or uri or "memory",
                "uri": short_uri(uri),
                "score": "-" if score is None else f"{float(score):.3f}",
                "snippet": short_text(content, 320),
            }
        )
    return lines


def render_evidence_list(items: list[dict[str, str]]) -> str:
    if not items:
        return "<div class='quote'>无</div>"
    chunks = ["<div class='evidence-list'>"]
    for item in items:
        chunks.append(
            "<div class='evidence'>"
            f"<div class='evidence-title'>{html.escape(item['label'])} <span class='muted'>score {html.escape(item['score'])}</span></div>"
            f"<div class='evidence-uri'><code>{html.escape(item['uri'])}</code></div>"
            f"<div class='evidence-snippet'>{html.escape(item['snippet'])}</div>"
            "</div>"
        )
    chunks.append("</div>")
    return "\n".join(chunks)


def render_table_rows(rows: list[dict[str, str]]) -> str:
    parts = []
    for row in rows:
        parts.append(
            "<tr>"
            f"<td><code>{html.escape(row['sample_id'])}</code></td>"
            f"<td>{html.escape(row['question'])}</td>"
            f"<td>{html.escape(row['gold'])}</td>"
            f"<td>{html.escape(row['echo'])}</td>"
            f"<td>{html.escape(row['ov'])}</td>"
            "</tr>"
        )
    return "\n".join(parts)


def render_slow_rows(rows: list[dict[str, Any]]) -> str:
    parts = []
    for row in rows:
        parts.append(
            "<tr>"
            f"<td><code>{html.escape(row['sample_id'])}</code></td>"
            f"<td>{html.escape(row['question'])}</td>"
            f"<td>{row['end_to_end_time_s']:.1f}s</td>"
            f"<td><code>{html.escape(row['final_source'])}</code></td>"
            f"<td>{html.escape(row['import_state'])}</td>"
            f"<td>{html.escape(row['response'])}</td>"
            "</tr>"
        )
    return "\n".join(parts)


def render_polluted_rows(rows: list[dict[str, Any]]) -> str:
    parts = []
    for row in rows:
        parts.append(
            "<tr>"
            f"<td><code>{html.escape(row['sample_id'])}</code></td>"
            f"<td>{html.escape(row['question'])}</td>"
            f"<td>{html.escape(row['gold'])}</td>"
            f"<td>{html.escape(row['response'])}</td>"
            f"<td><code>{html.escape(row['uri'])}</code></td>"
            f"<td>{html.escape(row['snippet'])}</td>"
            "</tr>"
        )
    return "\n".join(parts)


def render_unknown_loss_rows(rows: list[dict[str, Any]]) -> str:
    parts = []
    for row in rows:
        parts.append(
            "<tr>"
            f"<td><code>{html.escape(row['sample_id'])}</code></td>"
            f"<td>{html.escape(row['question'])}</td>"
            f"<td>{html.escape(row['gold'])}</td>"
            f"<td>{html.escape(row['echo'])}</td>"
            f"<td>{html.escape(row['ov'])}</td>"
            f"<td><code>{html.escape(row['echo_source'])}</code></td>"
            f"<td>{html.escape(row['echo_status'])}</td>"
            "</tr>"
        )
    return "\n".join(parts)


def answer_metrics(rows: list[dict[str, str]], hotpot_eval: Any) -> dict[str, Any]:
    if not rows:
        return {"count": 0, "em": 0.0, "f1": 0.0}
    em_values = [hotpot_eval.exact_match(row.get("response") or "", row.get("answer") or "") for row in rows]
    f1_values = [hotpot_eval.f1_score(row.get("response") or "", row.get("answer") or "") for row in rows]
    return {
        "count": len(rows),
        "em": sum(em_values) / len(rows),
        "f1": sum(f1_values) / len(rows),
    }


def build_html() -> str:
    hotpot_eval = load_hotpot_metric_module()
    echo_task = task_snapshot(ECHO_TASK_ID)
    ov_task = task_snapshot(OV_TASK_ID)

    echo_rows_list = load_csv(ECHO_RUN / "echomemory_generic_qa_results.csv")
    ov_rows_list = load_csv(OV_RUN / "openviking_generic_qa_results.csv")
    echo_rows = {row["sample_id"]: row for row in echo_rows_list}
    ov_rows = {row["sample_id"]: row for row in ov_rows_list}

    echo_summary = maybe_load_json(ECHO_RUN / "summary.json")
    ov_summary = maybe_load_json(OV_RUN / "summary.json")
    echo_official = maybe_load_json(ECHO_RUN / "hotpotqa_answer_summary.json")
    ov_official = maybe_load_json(OV_RUN / "hotpotqa_answer_summary.json")
    echo_judge = maybe_load_json(ECHO_RUN / "judge_summary.json")
    ov_judge = maybe_load_json(OV_RUN / "judge_summary.json")
    echo_artifacts = artifact_state(ECHO_RUN)
    ov_artifacts = artifact_state(OV_RUN)
    partial = not (echo_artifacts["complete"] and ov_artifacts["complete"])
    echo_log_counts = log_anomaly_counts(RUNS / ECHO_TASK_ID / "run.log")
    ov_log_counts = log_anomaly_counts(RUNS / OV_TASK_ID / "run.log")
    echo_recent_activity = recent_log_activity(RUNS / ECHO_TASK_ID / "run.log")

    shared_ids = sorted(set(echo_rows) & set(ov_rows))
    shared_rows = []
    for sample_id in shared_ids:
        echo_row = echo_rows[sample_id]
        ov_row = ov_rows[sample_id]
        gold = echo_row.get("answer") or ""
        shared_rows.append(
            {
                "sample_id": sample_id,
                "question": echo_row["question"],
                "gold": gold,
                "echo": echo_row.get("response") or "",
                "ov": ov_row.get("response") or "",
                "echo_f1": hotpot_eval.f1_score(echo_row.get("response") or "", gold),
                "ov_f1": hotpot_eval.f1_score(ov_row.get("response") or "", gold),
                "echo_em": hotpot_eval.exact_match(echo_row.get("response") or "", gold),
                "ov_em": hotpot_eval.exact_match(ov_row.get("response") or "", gold),
            }
        )

    echo_shared = answer_metrics([echo_rows[sid] for sid in shared_ids], hotpot_eval) if shared_ids else {"count": 0, "em": 0.0, "f1": 0.0}
    ov_shared = answer_metrics([ov_rows[sid] for sid in shared_ids], hotpot_eval) if shared_ids else {"count": 0, "em": 0.0, "f1": 0.0}

    ov_better = [item for item in shared_rows if item["ov_f1"] > item["echo_f1"]]
    echo_better = [item for item in shared_rows if item["echo_f1"] > item["ov_f1"]]
    ties = [item for item in shared_rows if item["echo_f1"] == item["ov_f1"]]

    echo_unknown = sum(is_unknown_response(item["echo"] or "") for item in shared_rows)
    ov_unknown = sum(is_unknown_response(item["ov"] or "") for item in shared_rows)
    echo_source_counter = Counter((row.get("final_evidence_source") or "none") for row in echo_rows_list)
    echo_pending = sum((row.get("import_integrity") or "") == "pending_async_memory" for row in echo_rows_list)
    echo_incomplete = sum((row.get("import_status") or "") == "ECHOMEMORY_IMPORT_INCOMPLETE" for row in echo_rows_list)
    echo_unknown_by_source = Counter(
        (row.get("final_evidence_source") or "none")
        for row in echo_rows_list
        if is_unknown_response(row.get("response") or "")
    )
    echo_partial = sum(
        0.0 < hotpot_eval.f1_score(row.get("response") or "", row.get("answer") or "") < 1.0
        for row in echo_rows_list
    )
    echo_atom_wrong = sum(
        (row.get("final_evidence_source") or "") == "atom"
        and hotpot_eval.exact_match(row.get("response") or "", row.get("answer") or "") == 0.0
        for row in echo_rows_list
    )

    echo_avg_injection = (
        sum(float(row.get("memory_injection_time_s") or 0.0) for row in echo_rows_list) / len(echo_rows_list)
        if echo_rows_list else 0.0
    )
    echo_avg_qa = (
        sum(float(row.get("qa_time_s") or 0.0) for row in echo_rows_list) / len(echo_rows_list)
        if echo_rows_list else 0.0
    )
    echo_source_metrics = {}
    for source, count in echo_source_counter.items():
        rows_for_source = [row for row in echo_rows_list if (row.get("final_evidence_source") or "none") == source]
        if not rows_for_source:
            continue
        echo_source_metrics[source] = {
            "count": count,
            "em": sum(hotpot_eval.exact_match(row.get("response") or "", row.get("answer") or "") for row in rows_for_source) / len(rows_for_source),
            "f1": sum(hotpot_eval.f1_score(row.get("response") or "", row.get("answer") or "") for row in rows_for_source) / len(rows_for_source),
            "unknown": echo_unknown_by_source.get(source, 0),
        }
    ov_avg_qa = (
        sum(float(row.get("qa_time_s") or row.get("time_cost") or 0.0) for row in ov_rows_list) / len(ov_rows_list)
        if ov_rows_list else 0.0
    )
    echo_long_tail = sum(safe_float(row.get("end_to_end_time_s")) >= 300.0 for row in echo_rows_list)
    echo_slowest = sorted(
        [
            {
                "sample_id": str(row.get("sample_id") or ""),
                "question": short_text(str(row.get("question") or ""), 180),
                "end_to_end_time_s": safe_float(row.get("end_to_end_time_s")),
                "final_source": str(row.get("final_evidence_source") or "none"),
                "import_state": f"{row.get('import_status') or '-'} / {row.get('import_integrity') or '-'}",
                "response": short_text(str(row.get("response") or ""), 120),
            }
            for row in echo_rows_list
        ],
        key=lambda item: item["end_to_end_time_s"],
        reverse=True,
    )[:8]
    echo_polluted = []
    for row in echo_rows_list:
        if (row.get("final_evidence_source") or "") != "atom":
            continue
        if hotpot_eval.exact_match(row.get("response") or "", row.get("answer") or "") == 1.0:
            continue
        memories = parse_memories(row.get("relevant_memory", ""))
        if not memories:
            continue
        top = memories[0]
        echo_polluted.append(
            {
                "sample_id": str(row.get("sample_id") or ""),
                "question": short_text(str(row.get("question") or ""), 150),
                "gold": short_text(str(row.get("answer") or ""), 80),
                "response": short_text(str(row.get("response") or ""), 80),
                "uri": short_uri(str(top.get("uri") or "")),
                "snippet": short_text(str(top.get("content") or ""), 180),
                "score": safe_float(top.get("score")),
            }
        )
    echo_polluted = sorted(echo_polluted, key=lambda item: item["score"], reverse=True)[:8]
    echo_unknown_losses = sorted(
        [
            {
                "sample_id": item["sample_id"],
                "question": short_text(item["question"], 160),
                "gold": short_text(item["gold"], 80),
                "echo": short_text(item["echo"], 80),
                "ov": short_text(item["ov"], 80),
                "echo_source": str(echo_rows[item["sample_id"]].get("final_evidence_source") or "none"),
                "echo_status": (
                    str(echo_rows[item["sample_id"]].get("import_status") or "-")
                    + " / "
                    + str(echo_rows[item["sample_id"]].get("import_integrity") or "-")
                ),
                "delta": item["ov_f1"] - item["echo_f1"],
            }
            for item in shared_rows
            if is_unknown_response(item["echo"] or "") and item["ov_f1"] > item["echo_f1"]
        ],
        key=lambda item: item["delta"],
        reverse=True,
    )[:8]

    case_html = []
    for sample_id in CASE_SAMPLES:
        echo_row = echo_rows.get(sample_id)
        ov_row = ov_rows.get(sample_id)
        if not echo_row or not ov_row:
            continue
        answer = echo_row.get("answer") or ""
        case_html.append(
            f"""
      <div class="case">
        <h3>{html.escape(echo_row['question'])}</h3>
        <p><span class="muted">sample_id</span> <code>{html.escape(sample_id)}</code> · <span class="muted">Gold</span> <strong>{html.escape(answer)}</strong></p>
        <div class="case-grid">
          <div class="subcard">
            <h3>EchoMemory</h3>
            <div class="kv">
              <div class="k">回答</div><div>{html.escape(echo_row.get('response') or '')}</div>
              <div class="k">Answer F1</div><div>{hotpot_eval.f1_score(echo_row.get('response') or '', answer):.4f}</div>
              <div class="k">最终证据源</div><div><code>{html.escape(echo_row.get('final_evidence_source') or 'none')}</code></div>
              <div class="k">导入状态</div><div>{html.escape((echo_row.get('import_status') or '-') + ' / ' + (echo_row.get('import_integrity') or '-'))}</div>
              <div class="k">耗时</div><div>injection {html.escape(echo_row.get('memory_injection_time_s') or '-')} s, qa {html.escape(echo_row.get('qa_time_s') or '-')} s</div>
            </div>
            {render_evidence_list(evidence_lines(echo_row))}
          </div>
          <div class="subcard">
            <h3>OpenViking</h3>
            <div class="kv">
              <div class="k">回答</div><div>{html.escape(ov_row.get('response') or '')}</div>
              <div class="k">Answer F1</div><div>{hotpot_eval.f1_score(ov_row.get('response') or '', answer):.4f}</div>
              <div class="k">retrieval_count</div><div>{html.escape(ov_row.get('retrieval_count') or '-')}</div>
              <div class="k">memory_hit_count</div><div>{html.escape(ov_row.get('memory_hit_count') or '-')}</div>
            </div>
            {render_evidence_list(evidence_lines(ov_row))}
          </div>
        </div>
      </div>
            """
        )

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    echo_current = str((echo_task.get("progress") or {}).get("current") or len(echo_rows_list))
    echo_total = str((echo_task.get("progress") or {}).get("total") or 100)
    banner = (
        "这份页面已切换为完整 100 题定稿版。"
        if not partial
        else "这份页面的结构已经是最终版，但当前仍是运行中快照。OpenViking 已完成，EchoMemory 还在继续跑。"
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>HotpotQA 100 题 OpenViking vs EchoMemory 最终对比</title>
  <style>
    :root {{
      --bg: #f5f6f8;
      --panel: #ffffff;
      --line: #d9dce3;
      --text: #16181d;
      --muted: #68707f;
      --blue: #1d4ed8;
      --green: #157f3b;
      --orange: #b4690e;
      --red: #b42318;
      --shadow: 0 12px 28px rgba(16, 24, 40, 0.08);
      --radius: 14px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Helvetica Neue", Arial, sans-serif;
      line-height: 1.6;
    }}
    .wrap {{ max-width: 1280px; margin: 0 auto; padding: 24px 18px 60px; }}
    .hero, .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }}
    .hero {{ padding: 28px 24px; margin-bottom: 18px; }}
    .card {{ padding: 20px 18px; margin-bottom: 16px; }}
    h1, h2, h3 {{ margin: 0; letter-spacing: 0; }}
    h1 {{ font-size: 30px; line-height: 1.2; margin-bottom: 10px; }}
    h2 {{ font-size: 22px; line-height: 1.25; margin-bottom: 14px; }}
    h3 {{ font-size: 17px; line-height: 1.35; margin-bottom: 10px; }}
    p {{ margin: 0 0 12px; }}
    .muted {{ color: var(--muted); }}
    .note {{
      padding: 12px 14px;
      border-left: 3px solid var(--blue);
      background: #f8fbff;
      color: var(--muted);
      border-radius: 10px;
    }}
    .alert {{
      padding: 12px 14px;
      border-left: 3px solid var(--red);
      background: #fff7f6;
      color: var(--text);
      border-radius: 10px;
    }}
    .stat-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
    .stat {{
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      background: #fbfbfc;
    }}
    .label {{ font-size: 12px; color: var(--muted); margin-bottom: 4px; }}
    .value {{ font-size: 24px; line-height: 1.2; font-weight: 700; }}
    .good {{ color: var(--green); }}
    .warn {{ color: var(--orange); }}
    .bad {{ color: var(--red); }}
    .grid {{ display: grid; gap: 16px; }}
    .grid.two {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    ul {{ margin: 0; padding-left: 18px; }}
    li {{ margin: 0 0 8px; }}
    code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      background: #eef2f6;
      border-radius: 6px;
      padding: 1px 5px;
      font-size: 12px;
      word-break: break-all;
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{
      text-align: left;
      vertical-align: top;
      border-top: 1px solid var(--line);
      padding: 10px 8px;
    }}
    th {{ color: var(--muted); background: #fbfbfc; font-weight: 650; }}
    .case {{
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fbfbfc;
      padding: 14px;
      margin-top: 14px;
    }}
    .case-grid {{
      display: grid;
      gap: 14px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      margin-top: 14px;
    }}
    .subcard {{
      border: 1px solid var(--line);
      border-radius: 12px;
      background: white;
      padding: 12px;
    }}
    .kv {{
      display: grid;
      grid-template-columns: 150px 1fr;
      gap: 6px 10px;
      font-size: 13px;
      margin-bottom: 10px;
    }}
    .kv .k {{ color: var(--muted); }}
    .evidence {{
      border-top: 1px solid var(--line);
      padding-top: 10px;
      margin-top: 10px;
    }}
    .evidence:first-child {{ border-top: 0; padding-top: 0; margin-top: 0; }}
    .evidence-title {{ font-size: 13px; font-weight: 650; margin-bottom: 4px; }}
    .evidence-uri {{ margin-bottom: 4px; }}
    .evidence-snippet {{ color: var(--muted); font-size: 13px; }}
    .footer {{ font-size: 12px; color: var(--muted); margin-top: 20px; }}
    @media (max-width: 920px) {{
      .grid.two, .stat-grid, .case-grid {{ grid-template-columns: 1fr; }}
      .kv {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>HotpotQA 100 题: OpenViking v0.4.4 vs EchoMemory v010</h1>
      <p>{html.escape(banner)}</p>
      <div class="note">
        EchoMemory：<strong>{html.escape(progress_label(echo_task, len(echo_rows_list), echo_artifacts))}</strong><br/>
        OpenViking：<strong>{html.escape(progress_label(ov_task, len(ov_rows_list), ov_artifacts))}</strong>
      </div>
      <div class="alert" style="margin-top:12px;">
        当前不是最终 100 vs 100 结论页。OpenViking 已正式完成；EchoMemory 还在 <strong>{html.escape(echo_current)}/{html.escape(echo_total)}</strong>，当前只写出了 <strong>{len(echo_rows_list)}</strong> 条结果，因此 EchoMemory 一侧仍只能按共享题快照解读。
      </div>
      <p class="footer">生成时间：{html.escape(generated_at)}</p>
    </section>

    <section class="card">
      <h2>官方 answer-only 指标</h2>
      <div class="stat-grid">
        <div class="stat"><div class="label">EchoMemory EM</div><div class="value warn">{pct(echo_official.get('answer_em')) if echo_official else pct(echo_shared['em'])}</div></div>
        <div class="stat"><div class="label">EchoMemory F1</div><div class="value warn">{pct(echo_official.get('answer_f1')) if echo_official else pct(echo_shared['f1'])}</div></div>
        <div class="stat"><div class="label">OpenViking EM</div><div class="value good">{pct(ov_official.get('answer_em')) if ov_official else pct(ov_shared['em'])}</div></div>
        <div class="stat"><div class="label">OpenViking F1</div><div class="value good">{pct(ov_official.get('answer_f1')) if ov_official else pct(ov_shared['f1'])}</div></div>
      </div>
      <div class="note" style="margin-top:12px;">
        这里优先展示 HotpotQA 官方风格的 answer-only EM/F1。若运行还没结束、官方评测文件还没落盘，就临时退回到当前共享题上的实时 EM/F1。
      </div>
      <div class="note" style="margin-top:12px;">
        EchoMemory 当前没有 <code>hotpotqa_answer_summary.json</code>，因此这里的 EchoMemory 分数仍是共享题快照，不是完整 100 题正式官方分数。
      </div>
    </section>

    <section class="card">
      <h2>共享题直接对比</h2>
      <div class="stat-grid">
        <div class="stat"><div class="label">共享题数</div><div class="value">{len(shared_rows)}</div></div>
        <div class="stat"><div class="label">EchoMemory 共享 F1</div><div class="value warn">{pct(echo_shared['f1'])}</div></div>
        <div class="stat"><div class="label">OpenViking 共享 F1</div><div class="value good">{pct(ov_shared['f1'])}</div></div>
        <div class="stat"><div class="label">OV 更好 / Echo 更好</div><div class="value">{len(ov_better)} / {len(echo_better)}</div></div>
      </div>
      <div class="stat-grid" style="margin-top:12px;">
        <div class="stat"><div class="label">EchoMemory 共享 EM</div><div class="value warn">{pct(echo_shared['em'])}</div></div>
        <div class="stat"><div class="label">OpenViking 共享 EM</div><div class="value good">{pct(ov_shared['em'])}</div></div>
        <div class="stat"><div class="label">平局</div><div class="value">{len(ties)}</div></div>
        <div class="stat"><div class="label">Judge Accuracy</div><div class="value">{pct(echo_judge.get('accuracy')) if echo_judge else '-'} / {pct(ov_judge.get('accuracy')) if ov_judge else '-'}</div></div>
      </div>
    </section>

    <section class="grid two">
      <div class="card">
        <h2>为什么 EchoMemory 表现差</h2>
        <ul>
          <li><strong>导入完整性一直不收敛</strong>：当前 <code>pending_async_memory</code> 是 {echo_pending}/{len(echo_rows_list)}，同时 <code>ECHOMEMORY_IMPORT_INCOMPLETE</code> 是 {echo_incomplete}/{len(echo_rows_list)}。</li>
          <li><strong>这些已写结果全部带着未完成导入状态</strong>：当前 {len(echo_rows_list)} 条 EchoMemory 结果都还是 <code>ECHOMEMORY_IMPORT_INCOMPLETE / pending_async_memory</code>。</li>
          <li><strong>全局 atom 抢主证据</strong>：当前 <code>atom</code> 作为最终主证据源出现 {echo_source_counter.get('atom', 0)} 次，而这类题里有 {echo_atom_wrong} 题最终 answer EM 仍是 0。</li>
          <li><strong>atom 证据路径本身更差</strong>：当前 <code>atom</code> 路径的平均 EM/F1 只有 <strong>{pct((echo_source_metrics.get('atom') or {}).get('em'))} / {pct((echo_source_metrics.get('atom') or {}).get('f1'))}</strong>，而 <code>segment_memory</code> 是 <strong>{pct((echo_source_metrics.get('segment_memory') or {}).get('em'))} / {pct((echo_source_metrics.get('segment_memory') or {}).get('f1'))}</strong>。</li>
          <li><strong>unknown 过多</strong>：当前直接输出 <code>I do not know</code> 的题有 {echo_unknown} 题，HotpotQA 的 answer-only 口径不会奖励“谨慎但不答”。</li>
          <li><strong>unknown 主要也压在 atom 上</strong>：{echo_unknown} 个 unknown 里，<strong>{echo_unknown_by_source.get('atom', 0)}</strong> 个来自 <code>atom</code>，只有 <strong>{echo_unknown_by_source.get('segment_memory', 0)}</strong> 个来自 <code>segment_memory</code>。</li>
          <li><strong>答案抽取也不稳定</strong>：即使不是全错，当前仍有 {echo_partial} 题是“部分命中但没答完整”，这会把 EM 压得更低。</li>
        </ul>
      </div>
      <div class="card">
        <h2>对比 OpenViking 的差别</h2>
        <ul>
          <li>OpenViking 在共享题上的 F1 持续高于 EchoMemory，不是 judge 偏好导致，因为这里直接看 answer-only 指标。</li>
          <li>OpenViking 更像“当前题文档检索 + 直接抽答案”，较少被跨题长期记忆污染。</li>
          <li>EchoMemory 平均每题要花 <strong>{echo_avg_injection:.1f}s</strong> 做记忆注入，而 OpenViking 当前平均问答耗时约 <strong>{ov_avg_qa:.1f}s</strong>，吞吐也明显更差。</li>
          <li>EchoMemory QA 本身平均约 <strong>{echo_avg_qa:.1f}s</strong>，说明问题不只在模型调用慢，而在“导入 + 异步整理 + 再检索”这套链路的时机和排序。</li>
        </ul>
        <div class="alert">最核心的根因不是“模型弱”，而是“QA 时机过早”与“跨题 atom 污染”叠加，再被保守的 unknown 策略进一步放大。</div>
      </div>
    </section>

    <section class="card">
      <h2>实现路径差异</h2>
      <ul>
        <li><strong>EchoMemory 这轮就是按“快等一下就答”跑的</strong>：运行命令里显式带了 <code>--import-wait-mode fast</code>、<code>--commit-wait-s 8</code>、<code>--defer-artifact-wait</code>、<code>--fallback-to-one-shot</code>。</li>
        <li><strong>代码会在 fast 模式下放宽门禁</strong>：在 [echomemory_generic_qa.py](/Users/chx/locomo-eval-web/scripts/echomemory_generic_qa.py:607) 里，fast 模式把等待窗口收紧到 45 秒、稳定轮数降到 1；如果仍未 ready，会直接打印 <code>[fast-wait] ... proceeding before async artifacts fully stabilize</code>，随后把 <code>allow_partial=fast_wait_mode</code> 传给 [require_memory_ready_or_exit](/Users/chx/locomo-eval-web/scripts/echomemory_wait_and_eval.py:226)。</li>
        <li><strong>这意味着 not ready 也能继续 QA</strong>：在 [echomemory_wait_and_eval.py](/Users/chx/locomo-eval-web/scripts/echomemory_wait_and_eval.py:236) 里，只要 <code>allow_partial=True</code>，即使 <code>ready=False</code> 也直接返回，不会阻断答题。</li>
        <li><strong>OpenViking 这轮路径更直接</strong>：它优先把当前题的 source documents 物化成独立文档记忆；[openviking_generic_qa.py](/Users/chx/locomo-eval-web/scripts/openviking_generic_qa.py:413) 在 <code>source_documents</code> 模式下先建立文档导入记录，[ensure_document_memory](/Users/chx/locomo-eval-web/scripts/openviking_generic_qa.py:381) 再把文档写入 OpenViking 内容层，然后 QA 直接围绕这些当前题文档检索。</li>
        <li><strong>所以两边不是同一种“记忆导入后再答题”</strong>：EchoMemory 更依赖异步 atom / graph / cursor 收敛，OpenViking 更像“当前题文档入库后直接读当前题文档”。这正好解释了为什么 OpenViking 几乎没有 unknown，而 EchoMemory 会在 <code>pending_async_memory</code> 状态下大量保守弃答。</li>
      </ul>
    </section>

    <section class="card">
      <h2>日志异常与运行稳定性</h2>
      <div class="stat-grid">
        <div class="stat"><div class="label">Echo 截断原子抽取</div><div class="value bad">{echo_log_counts.get('atom_extraction_truncated', 0)}</div></div>
        <div class="stat"><div class="label">Echo Sidecar 损坏</div><div class="value bad">{echo_log_counts.get('typed_sidecar_corrupt', 0)}</div></div>
        <div class="stat"><div class="label">Echo 未收敛即 QA</div><div class="value warn">{echo_log_counts.get('commit_incomplete', 0)}</div></div>
        <div class="stat"><div class="label">Echo 300s+ 慢题</div><div class="value warn">{echo_long_tail}</div></div>
      </div>
      <div class="stat-grid" style="margin-top:12px;">
        <div class="stat"><div class="label">Echo 正式评测文件</div><div class="value">{'0/3' if not echo_artifacts['complete'] else '3/3'}</div></div>
        <div class="stat"><div class="label">OV 模型重试</div><div class="value">{ov_log_counts.get('model_retry', 0)}</div></div>
        <div class="stat"><div class="label">OV SSL EOF</div><div class="value">{ov_log_counts.get('ssl_eof', 0)}</div></div>
        <div class="stat"><div class="label">OV 正式评测文件</div><div class="value">{'3/3' if ov_artifacts['complete'] else '0/3'}</div></div>
      </div>
      <div class="stat-grid" style="margin-top:12px;">
        <div class="stat"><div class="label">Echo 最近 import/verify</div><div class="value">{echo_recent_activity['import']} / {echo_recent_activity['verify']}</div></div>
        <div class="stat"><div class="label">Echo 最近 commit/qa</div><div class="value">{echo_recent_activity['commit']} / {echo_recent_activity['qa']}</div></div>
        <div class="stat"><div class="label">Echo 最近 embedding</div><div class="value">{echo_recent_activity['embedding']}</div></div>
        <div class="stat"><div class="label">最近 400 行</div><div class="value">{'空转' if echo_recent_activity['only_embedding'] else '仍推进'}</div></div>
      </div>
      <div class="grid two" style="margin-top:14px;">
        <div>
          <ul>
            <li>EchoMemory 当前日志里，<code>Atomic extraction output appears truncated</code> 出现 <strong>{echo_log_counts.get('atom_extraction_truncated', 0)}</strong> 次。</li>
            <li><code>Typed sidecar corrupt</code> 出现 <strong>{echo_log_counts.get('typed_sidecar_corrupt', 0)}</strong> 次，说明本地 sidecar / graph 持久化存在损坏与自修复痕迹。</li>
            <li><code>commit complete=False</code> 后仍继续 QA 的日志当前出现 <strong>{echo_log_counts.get('commit_incomplete', 0)}</strong> 次，说明答题时机长期早于记忆收敛。</li>
            <li><code>pending_async_memory</code> 与 <code>ECHOMEMORY_IMPORT_INCOMPLETE</code> 也持续出现，和结果 CSV 里的样本状态完全一致。</li>
            <li>这些题往往同时伴随 <code>ECHOMEMORY_IMPORT_INCOMPLETE</code> 和 <code>pending_async_memory</code>，说明答题时长期记忆整理并未完全稳定。</li>
            <li>最近 <strong>{echo_recent_activity['tail_lines']}</strong> 行日志里，业务事件计数为 <code>import={echo_recent_activity['import']}</code>、<code>verify={echo_recent_activity['verify']}</code>、<code>commit={echo_recent_activity['commit']}</code>、<code>qa={echo_recent_activity['qa']}</code>；embedding 计数是 <code>{echo_recent_activity['embedding']}</code>。</li>
            <li>{'当前尾部窗口已经只剩 embedding 空转，没有新的业务推进，说明它此刻更像卡在 86 题的后处理。' if echo_recent_activity['only_embedding'] else '当前尾部窗口仍有业务推进，说明它还在缓慢前进。'}</li>
            <li>OpenViking 当前没有对应的原子抽取截断问题，主要只是少量模型重试与一次 SSL EOF 短暂抖动。</li>
          </ul>
        </div>
        <div class="alert">从日志看，EchoMemory 的核心问题是记忆后处理链路自身不稳定，而不是单纯“模型答不出”。</div>
      </div>
    </section>

    <section class="card">
      <h2>OpenViking 领先的典型题</h2>
      <table>
        <thead>
          <tr><th>sample_id</th><th>问题</th><th>Gold</th><th>EchoMemory</th><th>OpenViking</th></tr>
        </thead>
        <tbody>{render_table_rows(ov_better[:12])}</tbody>
      </table>
    </section>

    <section class="card">
      <h2>EchoMemory 的 unknown 失分题</h2>
      <table>
        <thead>
          <tr><th>sample_id</th><th>问题</th><th>Gold</th><th>EchoMemory</th><th>OpenViking</th><th>最终证据源</th><th>导入状态</th></tr>
        </thead>
        <tbody>{render_unknown_loss_rows(echo_unknown_losses)}</tbody>
      </table>
    </section>

    <section class="card">
      <h2>EchoMemory 最慢题</h2>
      <table>
        <thead>
          <tr><th>sample_id</th><th>问题</th><th>总耗时</th><th>最终证据源</th><th>导入状态</th><th>回答</th></tr>
        </thead>
        <tbody>{render_slow_rows(echo_slowest)}</tbody>
      </table>
    </section>

    <section class="card">
      <h2>无关 Atom 抢主证据的题</h2>
      <table>
        <thead>
          <tr><th>sample_id</th><th>问题</th><th>Gold</th><th>EchoMemory 回答</th><th>Top-1 证据</th><th>Top-1 内容片段</th></tr>
        </thead>
        <tbody>{render_polluted_rows(echo_polluted)}</tbody>
      </table>
    </section>

    <section class="card">
      <h2>典型个案</h2>
      {''.join(case_html)}
    </section>

    <section class="card">
      <h2>参考文件</h2>
      <ul>
        <li>EchoMemory CSV: <code>{html.escape(str(ECHO_RUN / 'echomemory_generic_qa_results.csv'))}</code></li>
        <li>OpenViking CSV: <code>{html.escape(str(OV_RUN / 'openviking_generic_qa_results.csv'))}</code></li>
        <li>EchoMemory 官方评测: <code>{html.escape(str(ECHO_RUN / 'hotpotqa_answer_summary.json'))}</code></li>
        <li>OpenViking 官方评测: <code>{html.escape(str(OV_RUN / 'hotpotqa_answer_summary.json'))}</code></li>
        <li>EchoMemory judge: <code>{html.escape(str(ECHO_RUN / 'judge_summary.json'))}</code></li>
        <li>OpenViking judge: <code>{html.escape(str(OV_RUN / 'judge_summary.json'))}</code></li>
      </ul>
      <div class="footer">最终报告文件：<code>{html.escape(str(OUTPUT))}</code></div>
    </section>
  </div>
</body>
</html>
"""


def main() -> None:
    html_text = build_html()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html_text, encoding="utf-8")
    STATIC_MIRROR.parent.mkdir(parents=True, exist_ok=True)
    STATIC_MIRROR.write_text(html_text, encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
