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
OUTPUT = ROOT / "web/static/generated-reports/hotpotqa_openviking_vs_echomemory_live_100_20260622.html"
STATIC_MIRROR = ROOT / "static/generated-reports/hotpotqa_openviking_vs_echomemory_live_100_20260622.html"

ECHO_TASK_ID = "echomemory_generic_qa_20260622_233948_1a19e0"
OV_TASK_ID = "openviking_generic_qa_20260622_231559_1bd882"

ECHO_RUN = RUNS / "echomemory_generic_qa_20260622_183050_943ead" / "echomemory_generic_qa"
OV_RUN = RUNS / "openviking_generic_qa_20260622_231559_1bd882" / "openviking_generic_qa"

CASE_SAMPLES = [
    "5a8e3ea95542995a26add48d",
    "5a8c7595554299585d9e36b6",
    "5ab51dae5542991779162d82",
    "5a85ea095542994775f606a8",
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


def latest_verify_without_qa(path: Path) -> dict[str, str]:
    if not path.exists():
        return {"sample_id": "", "verify_line": "", "pending": ""}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    latest_verify = ""
    latest_sample = ""
    latest_qa_sample = ""
    verify_re = re.compile(r"\[verify\] hotpotqa/(\S+) added_total=")
    qa_re = re.compile(r"\[qa\]\s+\d+/\d+\s+(\S+)\s")
    for line in lines:
        m = verify_re.search(line)
        if m:
            latest_sample = m.group(1)
            latest_verify = line.strip()
        m = qa_re.search(line)
        if m:
            latest_qa_sample = m.group(1)
    pending = latest_sample and latest_sample != latest_qa_sample
    return {
        "sample_id": latest_sample,
        "verify_line": latest_verify,
        "pending": "true" if pending else "false",
    }


def latest_progress_markers(path: Path) -> dict[str, str]:
    if not path.exists():
        return {
            "import_progress": "",
            "import_sample": "",
            "qa_progress": "",
            "qa_sample": "",
        }
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    latest_import_progress = ""
    latest_import_sample = ""
    latest_qa_progress = ""
    latest_qa_sample = ""
    import_re = re.compile(r"\[import\]\s+(\d+/\d+)\s+sample=(\S+)")
    qa_re = re.compile(r"\[qa\]\s+(\d+/\d+)\s+(\S+)\s")
    for line in lines:
        m = import_re.search(line)
        if m:
            latest_import_progress = m.group(1)
            latest_import_sample = m.group(2)
        m = qa_re.search(line)
        if m:
            latest_qa_progress = m.group(1)
            latest_qa_sample = m.group(2)
    return {
        "import_progress": latest_import_progress,
        "import_sample": latest_import_sample,
        "qa_progress": latest_qa_progress,
        "qa_sample": latest_qa_sample,
    }


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


def load_hotpot_metric_module() -> Any:
    script = ROOT / "scripts/hotpotqa_answer_eval.py"
    spec = importlib.util.spec_from_file_location("hotpot_eval_live_compare", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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


def build_html() -> str:
    hotpot_eval = load_hotpot_metric_module()
    echo_task = task_snapshot(ECHO_TASK_ID)
    ov_task = task_snapshot(OV_TASK_ID)

    echo_rows_list = load_csv(ECHO_RUN / "echomemory_generic_qa_results.csv")
    ov_rows_list = load_csv(OV_RUN / "openviking_generic_qa_results.csv")
    echo_rows = {row["sample_id"]: row for row in echo_rows_list}
    ov_rows = {row["sample_id"]: row for row in ov_rows_list}
    echo_artifacts = artifact_state(ECHO_RUN)
    ov_artifacts = artifact_state(OV_RUN)

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

    echo_shared_f1 = sum(item["echo_f1"] for item in shared_rows) / len(shared_rows) if shared_rows else 0.0
    ov_shared_f1 = sum(item["ov_f1"] for item in shared_rows) / len(shared_rows) if shared_rows else 0.0
    echo_shared_em = sum(item["echo_em"] for item in shared_rows) / len(shared_rows) if shared_rows else 0.0
    ov_shared_em = sum(item["ov_em"] for item in shared_rows) / len(shared_rows) if shared_rows else 0.0

    ov_better = [item for item in shared_rows if item["ov_f1"] > item["echo_f1"]]
    echo_better = [item for item in shared_rows if item["echo_f1"] > item["ov_f1"]]
    ties = [item for item in shared_rows if item["echo_f1"] == item["ov_f1"]]

    echo_unknown = sum(is_unknown_response(item["echo"] or "") for item in shared_rows)
    ov_unknown = sum(is_unknown_response(item["ov"] or "") for item in shared_rows)
    echo_atom = sum((row.get("final_evidence_source") or "") == "atom" for row in echo_rows_list)
    echo_pending = sum((row.get("import_integrity") or "") == "pending_async_memory" for row in echo_rows_list)
    echo_incomplete = sum((row.get("import_status") or "") == "ECHOMEMORY_IMPORT_INCOMPLETE" for row in echo_rows_list)
    echo_source_counter = Counter((row.get("final_evidence_source") or "none") for row in echo_rows_list)
    echo_unknown_by_source = Counter(
        (row.get("final_evidence_source") or "none")
        for row in echo_rows_list
        if is_unknown_response(row.get("response") or "")
    )
    echo_log_counts = log_anomaly_counts(RUNS / ECHO_TASK_ID / "run.log")
    ov_log_counts = log_anomaly_counts(RUNS / OV_TASK_ID / "run.log")
    echo_recent_activity = recent_log_activity(RUNS / ECHO_TASK_ID / "run.log")
    echo_pending_verify = latest_verify_without_qa(RUNS / ECHO_TASK_ID / "run.log")
    echo_progress_markers = latest_progress_markers(RUNS / ECHO_TASK_ID / "run.log")
    echo_long_tail = sum(safe_float(row.get("end_to_end_time_s")) >= 300.0 for row in echo_rows_list)
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
    )[:6]

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
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>HotpotQA OpenViking vs EchoMemory 100 题 Live 对比</title>
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
    .wrap {{ max-width: 1260px; margin: 0 auto; padding: 24px 18px 60px; }}
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
      <h1>HotpotQA OpenViking v0.4.4 vs EchoMemory v010</h1>
      <p>这是 100 题 HotpotQA 对比的 live 版本。OpenViking 已经完成并产出正式结果；这里主要跟踪 EchoMemory 继续推进时，双方在当前重叠题上的实时差异。</p>
      <div class="note">
        EchoMemory：<strong>{html.escape(progress_label(echo_task, len(echo_rows_list), echo_artifacts))}</strong><br/>
        OpenViking：<strong>{html.escape(progress_label(ov_task, len(ov_rows_list), ov_artifacts))}</strong>
      </div>
      <div class="alert" style="margin-top:12px;">
        当前还不是最终 100 vs 100 定稿。OpenViking 的 100 题正式产物已经齐全；EchoMemory 仍在 <strong>{html.escape(echo_current)}/{html.escape(echo_total)}</strong>，当前 CSV 已落到 <strong>{len(echo_rows_list)}</strong> 题。
      </div>
      <p class="footer">生成时间：{html.escape(generated_at)}</p>
    </section>

    <section class="card">
      <h2>当前共享题结果</h2>
      <div class="stat-grid">
        <div class="stat"><div class="label">共享题数</div><div class="value">{len(shared_rows)}</div></div>
        <div class="stat"><div class="label">EchoMemory F1</div><div class="value warn">{pct(echo_shared_f1)}</div></div>
        <div class="stat"><div class="label">OpenViking F1</div><div class="value good">{pct(ov_shared_f1)}</div></div>
        <div class="stat"><div class="label">OV 更好 / Echo 更好</div><div class="value">{len(ov_better)} / {len(echo_better)}</div></div>
      </div>
      <div class="stat-grid" style="margin-top:12px;">
        <div class="stat"><div class="label">EchoMemory EM</div><div class="value warn">{pct(echo_shared_em)}</div></div>
        <div class="stat"><div class="label">OpenViking EM</div><div class="value good">{pct(ov_shared_em)}</div></div>
        <div class="stat"><div class="label">平局</div><div class="value">{len(ties)}</div></div>
        <div class="stat"><div class="label">运行行数</div><div class="value">{len(echo_rows_list)} / {len(ov_rows_list)}</div></div>
      </div>
      <div class="note" style="margin-top:12px;">
        截至当前共享 <strong>{len(shared_rows)}</strong> 题，EchoMemory 的即时 <strong>EM {pct(echo_shared_em)} / F1 {pct(echo_shared_f1)}</strong>，其中 <code>I do not know</code> 已有 <strong>{echo_unknown}</strong> 题；OpenViking 同口径为 <strong>EM {pct(ov_shared_em)} / F1 {pct(ov_shared_f1)}</strong>，当前 unknown 为 <strong>{ov_unknown}</strong>。
      </div>
      <div class="note" style="margin-top:12px;">
        当前这 <strong>{len(echo_rows_list)}</strong> 条 EchoMemory 结果全部仍带着 <code>ECHOMEMORY_IMPORT_INCOMPLETE / pending_async_memory</code> 进入 QA；状态文件也还停在 <code>importing_memory</code>。
      </div>
      <div class="note" style="margin-top:12px;">
        这轮 EchoMemory 当前更像<strong>尾部导入/索引极慢</strong>，不是已经死掉：任务接口最新显示 <strong>{html.escape(str((echo_task.get('progress') or {}).get('current') or len(echo_rows_list)))}/100</strong>，阶段是 <code>{html.escape(str((echo_task.get('progress') or {}).get('phase') or '-'))}</code>；日志里最新 <code>[import]</code> 已到 <strong>{html.escape(echo_progress_markers['import_progress'] or '-')}</strong>，最新 <code>[qa]</code> 到 <strong>{html.escape(echo_progress_markers['qa_progress'] or '-')}</strong>，而 CSV 只落到 <strong>{len(echo_rows_list)}</strong> 行。
      </div>
      <div class="note" style="margin-top:12px;">
        从最近几题的节奏看，尾部样本并不是“消息没写进去”，而是 <strong>10 条文档消息写入完成后，还要经历一次 commit async_settling，再带着 <code>complete=False</code> 进入 QA</strong>。当前最新日志就是这个模式：最新 <code>[qa]</code> 样本 <code>{html.escape(echo_progress_markers['qa_sample'] or '-')}</code> 之前，仍会先出现一次 <code>complete=False commit_index=9/9 atom_pipeline_index=-1/9 flush_complete=False</code>。
      </div>
      <div class="note" style="margin-top:12px;">
        最近 <strong>{echo_recent_activity['tail_lines']}</strong> 行 EchoMemory 日志里，业务事件计数是：
        <code>import={echo_recent_activity['import']}</code>、
        <code>verify={echo_recent_activity['verify']}</code>、
        <code>commit={echo_recent_activity['commit']}</code>、
        <code>qa={echo_recent_activity['qa']}</code>。
        {'当前这一段已经只剩 embedding 空转，没有新的业务推进，可视为尾部停滞。' if echo_recent_activity['only_embedding'] else '当前这一段仍有业务推进，还不能算彻底停滞。'}
      </div>
      <div class="note" style="margin-top:12px;">
        最新日志还显示一个更具体的尾部症状：样本 <code>{html.escape(echo_pending_verify['sample_id'])}</code> 已经完成 <code>verify 10/10</code>，但如果其后还没有新的 <code>[qa]</code> 行，就说明它再次卡在 <strong>verify 之后、QA 之前</strong> 的后处理阶段。{'当前这个症状正在发生。' if echo_pending_verify['pending'] == 'true' else '当前最新 verify 已经进入 QA。'}
      </div>
      <div class="note" style="margin-top:12px;">
        进程级证据也支持“超慢长尾”而不是“已退出”：当前 <code>echomemory_generic_qa.py</code> 进程仍然存活，日志里最新 <code>[import]</code> / <code>[qa]</code> 已推进到 <strong>{html.escape(echo_progress_markers['import_progress'] or '-')}</strong> / <strong>{html.escape(echo_progress_markers['qa_progress'] or '-')}</strong>，但正式 CSV 还停在 <strong>{len(echo_rows_list)}</strong> 行。结合最新进程采样，它更像是带着未收敛状态继续执行，而不是正常完成一题就快速落盘。
      </div>
    </section>

    <section class="grid two">
      <div class="card">
        <h2>为什么 EchoMemory 差</h2>
        <ul>
          <li>当前 <strong>{echo_pending}/{len(echo_rows_list)}</strong> 题仍是 <code>pending_async_memory</code>，说明 QA 发生在记忆整理尚未完全收敛时。</li>
          <li>当前 <strong>{echo_atom}/{len(echo_rows_list)}</strong> 题最终主证据源是 <code>atom</code>，这会把跨题残留事实顶到前面。</li>
          <li>当前 <strong>{echo_unknown}</strong> 题直接回答 <code>I do not know</code>，在 HotpotQA answer-only 口径下会直接拉低 EM/F1。</li>
          <li><code>atom</code> 路径当前平均只有 <strong>EM {pct((echo_source_metrics.get('atom') or {}).get('em'))} / F1 {pct((echo_source_metrics.get('atom') or {}).get('f1'))}</strong>；<code>segment_memory</code> 是 <strong>EM {pct((echo_source_metrics.get('segment_memory') or {}).get('em'))} / F1 {pct((echo_source_metrics.get('segment_memory') or {}).get('f1'))}</strong>。</li>
          <li>这 <strong>{echo_unknown}</strong> 个 unknown 里，有 <strong>{echo_unknown_by_source.get('atom', 0)}</strong> 个来自 <code>atom</code>，只有 <strong>{echo_unknown_by_source.get('segment_memory', 0)}</strong> 个来自 <code>segment_memory</code>。</li>
          <li>从已完成共享题看，OpenViking 更稳定地把问题限定在当前题导入的文档上下文里，较少被全局历史污染。</li>
        </ul>
      </div>
      <div class="card">
        <h2>当前信号</h2>
        <ul>
          <li>EchoMemory 最终证据源分布：<code>atom</code> {echo_source_counter.get('atom', 0)}，<code>segment_memory</code> {echo_source_counter.get('segment_memory', 0)}。</li>
          <li>EchoMemory 当前 <code>ECHOMEMORY_IMPORT_INCOMPLETE</code> 已出现 <strong>{echo_incomplete}</strong> 次，300 秒以上长尾题有 <strong>{echo_long_tail}</strong> 道。</li>
          <li>OpenViking 当前也不是满速，仍在持续推进，但共享题上已经明显领先。</li>
          <li>这不是 judge 偏好造成的差距，因为这里直接用 HotpotQA 官方 answer-only EM/F1 做比较。</li>
        </ul>
        <div class="alert">就当前证据看，EchoMemory 的核心问题不是“模型太弱”，而是“记忆整理时机 + 全局 atom 抢主证据 + 过度保守输出 unknown”。</div>
      </div>
    </section>

    <section class="card">
      <h2>实现路径差异</h2>
      <ul>
        <li>EchoMemory 这轮运行参数本身就是 <code>--import-wait-mode fast</code>、<code>--commit-wait-s 8</code>、<code>--defer-artifact-wait</code>、<code>--fallback-to-one-shot</code>。</li>
        <li>在 [echomemory_generic_qa.py](/Users/chx/locomo-eval-web/scripts/echomemory_generic_qa.py:607) 里，fast 模式把稳定等待收紧，并在未 ready 时打印 <code>[fast-wait] ... proceeding before async artifacts fully stabilize</code>。</li>
        <li>随后它把 <code>allow_partial=fast_wait_mode</code> 传给 [require_memory_ready_or_exit](/Users/chx/locomo-eval-web/scripts/echomemory_wait_and_eval.py:226)；而 [require_memory_ready_or_exit](/Users/chx/locomo-eval-web/scripts/echomemory_wait_and_eval.py:236) 里只要 <code>allow_partial=True</code> 就直接放行。</li>
        <li>OpenViking 这轮更偏“当前题文档直接入库后再检索”：见 [openviking_generic_qa.py](/Users/chx/locomo-eval-web/scripts/openviking_generic_qa.py:413) 的 <code>source_documents</code> 路径和 [openviking_generic_qa.py](/Users/chx/locomo-eval-web/scripts/openviking_generic_qa.py:381) 的文档物化流程。</li>
      </ul>
    </section>

    <section class="card">
      <h2>日志异常快照</h2>
      <table>
        <thead>
          <tr><th>信号</th><th>EchoMemory</th><th>OpenViking</th><th>解释</th></tr>
        </thead>
        <tbody>
          <tr><td><code>atom_extraction_truncated</code></td><td>{echo_log_counts.get('atom_extraction_truncated', 0)}</td><td>{ov_log_counts.get('atom_extraction_truncated', 0)}</td><td>EchoMemory 原子抽取输出被截断，说明记忆整理链条本身有不稳定点。</td></tr>
          <tr><td><code>typed_sidecar_corrupt</code></td><td>{echo_log_counts.get('typed_sidecar_corrupt', 0)}</td><td>{ov_log_counts.get('typed_sidecar_corrupt', 0)}</td><td>EchoMemory sidecar 持久化出现损坏并触发自修复，说明本地图存储状态不稳定。</td></tr>
          <tr><td><code>commit_incomplete</code></td><td>{echo_log_counts.get('commit_incomplete', 0)}</td><td>{ov_log_counts.get('commit_incomplete', 0)}</td><td>EchoMemory 在 commit 未完整收敛时就进入 QA，直接影响检索质量。</td></tr>
          <tr><td><code>pending_async_memory</code></td><td>{echo_log_counts.get('pending_async_memory', 0)}</td><td>{ov_log_counts.get('pending_async_memory', 0)}</td><td>导入提交后仍有异步记忆未稳定，这和当前样本状态是一致的。</td></tr>
          <tr><td><code>ECHOMEMORY_IMPORT_INCOMPLETE</code></td><td>{echo_log_counts.get('import_incomplete', 0)}</td><td>{ov_log_counts.get('import_incomplete', 0)}</td><td>样本在导入未完成时就进入后续流程，是本轮最直接的流程性问题。</td></tr>
          <tr><td><code>model_retry</code></td><td>{echo_log_counts.get('model_retry', 0)}</td><td>{ov_log_counts.get('model_retry', 0)}</td><td>模型侧重试次数。当前 OpenViking 只有轻微瞬时重试。</td></tr>
          <tr><td><code>ssl_eof</code></td><td>{echo_log_counts.get('ssl_eof', 0)}</td><td>{ov_log_counts.get('ssl_eof', 0)}</td><td>网络瞬时 EOF。它存在，但不是这轮主要分差来源。</td></tr>
          <tr><td><code>import_timeout</code></td><td>{echo_log_counts.get('import_timeout', 0)}</td><td>{ov_log_counts.get('import_timeout', 0)}</td><td>导入超时。当前更大的问题是未完全收敛而不是硬超时。</td></tr>
        </tbody>
      </table>
    </section>

    <section class="card">
      <h2>OpenViking 领先的共享题</h2>
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
      <h2>典型个案</h2>
      {''.join(case_html)}
    </section>

    <section class="card">
      <h2>文件</h2>
      <ul>
        <li>EchoMemory CSV: <code>{html.escape(str(ECHO_RUN / 'echomemory_generic_qa_results.csv'))}</code></li>
        <li>OpenViking CSV: <code>{html.escape(str(OV_RUN / 'openviking_generic_qa_results.csv'))}</code></li>
        <li>EchoMemory resume task log: <code>{html.escape(str(RUNS / ECHO_TASK_ID / 'run.log'))}</code></li>
        <li>OpenViking task log: <code>{html.escape(str(RUNS / OV_TASK_ID / 'run.log'))}</code></li>
      </ul>
      <div class="footer">Live 对比页文件：<code>{html.escape(str(OUTPUT))}</code></div>
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
