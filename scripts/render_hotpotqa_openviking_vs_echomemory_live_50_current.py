#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import importlib.util
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen


ROOT = Path("/Users/chx/locomo-eval-web")
RUNS = ROOT / "runs"
OUTPUT = ROOT / "web/static/generated-reports/hotpotqa_openviking_vs_echomemory_live_50_current.html"
STATIC_MIRROR = ROOT / "static/generated-reports/hotpotqa_openviking_vs_echomemory_live_50_current.html"

OV_TASK_ID = "openviking_generic_qa_20260624_020054_5e57e5"
ECHO_TASK_ID = "echomemory_generic_qa_20260624_020101_conv30-clean-20260624-000324_443cd0"

OV_CSV = RUNS / OV_TASK_ID / "openviking_generic_qa/openviking_generic_qa_results.csv"
ECHO_CSV = RUNS / ECHO_TASK_ID / "echomemory_generic_qa/echomemory_generic_qa_results.csv"
OV_OFFICIAL = RUNS / OV_TASK_ID / "openviking_generic_qa/hotpotqa_answer_summary.json"
ECHO_RUNNING = RUNS / ECHO_TASK_ID / "echomemory_generic_qa/running_summary.json"
ECHO_STATUS = RUNS / ECHO_TASK_ID / "echomemory_generic_qa/generic_qa_status.json"


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def task_snapshot(task_id: str) -> dict[str, Any]:
    try:
        with urlopen(f"http://127.0.0.1:19181/api/tasks/{task_id}", timeout=10) as response:
            data = json.load(response)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {"id": task_id, "status": "unknown", "progress": {}}


def short_text(text: Any, limit: int = 120) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else f"{value[: limit - 3]}..."


def pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.2f}%"


def normalize_category(value: str) -> str:
    lowered = str(value or "").lower()
    if "comparison" in lowered:
        return "comparison"
    if "bridge" in lowered:
        return "bridge"
    return "unknown"


def load_hotpot_eval() -> Any:
    script = ROOT / "scripts/hotpotqa_answer_eval.py"
    spec = importlib.util.spec_from_file_location("hotpot_eval_live_50", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def rows_by_id(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    result = {}
    for row in rows:
        qid = str(row.get("question_id") or row.get("sample_id") or row.get("native_question_id") or "").strip()
        if qid:
            result[qid] = row
    return result


def average(items: list[dict[str, Any]], key: str) -> float | None:
    if not items:
        return None
    return sum(float(item.get(key) or 0.0) for item in items) / len(items)


def build_shared(eval_mod: Any, ov_rows: list[dict[str, str]], echo_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    ov = rows_by_id(ov_rows)
    echo = rows_by_id(echo_rows)
    shared = []
    for qid in sorted(set(ov) & set(echo)):
        ov_row = ov[qid]
        echo_row = echo[qid]
        gold = str(ov_row.get("answer") or echo_row.get("answer") or "")
        ov_resp = str(ov_row.get("response") or "")
        echo_resp = str(echo_row.get("response") or "")
        shared.append(
            {
                "question_id": qid,
                "question": str(ov_row.get("question") or echo_row.get("question") or ""),
                "gold": gold,
                "category": normalize_category(str(ov_row.get("category") or echo_row.get("category") or "")),
                "ov_response": ov_resp,
                "echo_response": echo_resp,
                "ov_em": eval_mod.exact_match(ov_resp, gold),
                "ov_f1": eval_mod.f1_score(ov_resp, gold),
                "echo_em": eval_mod.exact_match(echo_resp, gold),
                "echo_f1": eval_mod.f1_score(echo_resp, gold),
                "echo_reasoning": str(echo_row.get("reasoning") or ""),
                "echo_import_status": str(echo_row.get("import_status") or ""),
                "echo_import_integrity": str(echo_row.get("import_integrity") or ""),
            }
        )
    return shared


def render_type_rows(shared: list[dict[str, Any]]) -> str:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in shared:
        grouped[row["category"]].append(row)
    parts = []
    for key in ("bridge", "comparison", "unknown"):
        if key not in grouped:
            continue
        rows = grouped[key]
        parts.append(
            "<tr>"
            f"<td>{html.escape(key)}</td>"
            f"<td>{len(rows)}</td>"
            f"<td>{pct(average(rows, 'echo_em'))}</td>"
            f"<td>{pct(average(rows, 'echo_f1'))}</td>"
            f"<td>{pct(average(rows, 'ov_em'))}</td>"
            f"<td>{pct(average(rows, 'ov_f1'))}</td>"
            "</tr>"
        )
    return "\n".join(parts)


def render_case_rows(rows: list[dict[str, Any]]) -> str:
    parts = []
    for row in rows:
        parts.append(
            "<tr>"
            f"<td><code>{html.escape(row['question_id'])}</code></td>"
            f"<td>{html.escape(short_text(row['question']))}</td>"
            f"<td>{html.escape(short_text(row['gold'], 40))}</td>"
            f"<td>{html.escape(short_text(row['echo_response'], 40))}</td>"
            f"<td>{html.escape(short_text(row['ov_response'], 40))}</td>"
            f"<td>{pct(float(row['echo_f1']))}</td>"
            f"<td>{pct(float(row['ov_f1']))}</td>"
            "</tr>"
        )
    return "\n".join(parts)


def main() -> None:
    eval_mod = load_hotpot_eval()
    ov_task = task_snapshot(OV_TASK_ID)
    echo_task = task_snapshot(ECHO_TASK_ID)
    ov_official = load_json(OV_OFFICIAL)
    echo_running = load_json(ECHO_RUNNING)
    echo_status = load_json(ECHO_STATUS)
    ov_rows = load_csv(OV_CSV)
    echo_rows = load_csv(ECHO_CSV)
    shared = build_shared(eval_mod, ov_rows, echo_rows)

    ov_better = sorted(shared, key=lambda item: (item["ov_f1"] - item["echo_f1"]), reverse=True)
    echo_memory_not_ready = sum("memory not ready before QA" in row["echo_reasoning"] for row in shared)
    echo_pending = sum(row["echo_import_integrity"] == "pending_async_memory" for row in shared)
    echo_incomplete = sum(row["echo_import_status"] == "ECHOMEMORY_IMPORT_INCOMPLETE" for row in shared)
    echo_failed = sum(row["echo_import_status"] == "ECHOMEMORY_IMPORT_FAILED" for row in shared)
    ov_progress = ov_task.get("progress") or {}
    echo_progress = echo_task.get("progress") or {}
    ov_status = str(ov_task.get("status") or "unknown")
    echo_status_label = str(echo_task.get("status") or "unknown")
    echo_stage = str(echo_status.get("stage") or echo_progress.get("phase") or "")

    html_text = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>HotpotQA 50 Live Compare</title>
  <style>
    body {{ margin:0; background:#f5f6f8; color:#16181d; font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC",sans-serif; }}
    .wrap {{ max-width:1100px; margin:0 auto; padding:24px 18px 40px; }}
    .card {{ background:#fff; border:1px solid #d9dce3; border-radius:12px; padding:18px; margin-bottom:16px; }}
    .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }}
    .stat {{ background:#fbfbfc; border:1px solid #d9dce3; border-radius:10px; padding:12px; }}
    .label {{ font-size:12px; color:#68707f; margin-bottom:4px; }}
    .value {{ font-size:22px; font-weight:700; }}
    .note {{ margin-top:12px; padding:12px 14px; background:#f8fbff; border-left:3px solid #1d4ed8; border-radius:8px; }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; }}
    th,td {{ padding:10px 8px; border-top:1px solid #d9dce3; text-align:left; vertical-align:top; }}
    th {{ background:#fbfbfc; color:#68707f; }}
    code {{ background:#eef2f6; border-radius:6px; padding:1px 5px; font-size:12px; }}
    @media (max-width:900px) {{ .grid {{ grid-template-columns:1fr 1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="card">
      <h1>HotpotQA 50 对比</h1>
      <p>这页只看当前 50 题运行结果：OpenViking 50 已正式完成，EchoMemory 50 在 21 题处中断。历史结果仍统一去结果中心查看。</p>
      <div class="note">
        OpenViking 任务：<code>{html.escape(OV_TASK_ID)}</code> · 状态 {html.escape(ov_status)} · 当前 {ov_progress.get("current", 0)}/50<br/>
        EchoMemory 任务：<code>{html.escape(ECHO_TASK_ID)}</code> · 状态 {html.escape(echo_status_label)} · 当前 {echo_progress.get("current", 0)}/50
      </div>
      <p style="color:#68707f;">生成时间：{html.escape(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}</p>
    </section>

    <section class="card">
      <h2>正式结果 vs 运行中状态</h2>
      <div class="grid">
        <div class="stat"><div class="label">OV 正式 Answer EM</div><div class="value">{pct(ov_official.get("answer_em"))}</div></div>
        <div class="stat"><div class="label">OV 正式 Answer F1</div><div class="value">{pct(ov_official.get("answer_f1"))}</div></div>
        <div class="stat"><div class="label">Echo 已落盘</div><div class="value">{len(echo_rows)}/50</div></div>
        <div class="stat"><div class="label">Echo 终态/阶段</div><div class="value">{html.escape((echo_status_label + (' / ' + echo_stage if echo_stage else '')).strip(' /') or "-")}</div></div>
      </div>
      <div class="note">
        OpenViking 已产出正式 <code>hotpotqa_answer_summary.json</code>。EchoMemory 没有跑完整个 50 题，也没有正式 HotpotQA answer 汇总；这里展示的是中断前的已落盘结果和共享题诊断，不把它冒充成最终正式分数。
      </div>
    </section>

    <section class="card">
      <h2>共享题即时分数</h2>
      <div class="grid">
        <div class="stat"><div class="label">共享题数</div><div class="value">{len(shared)}</div></div>
        <div class="stat"><div class="label">Echo EM / F1</div><div class="value">{pct(average(shared, "echo_em"))} / {pct(average(shared, "echo_f1"))}</div></div>
        <div class="stat"><div class="label">OV EM / F1</div><div class="value">{pct(average(shared, "ov_em"))} / {pct(average(shared, "ov_f1"))}</div></div>
        <div class="stat"><div class="label">OV 更好题数</div><div class="value">{sum(1 for row in shared if row["ov_f1"] > row["echo_f1"])}</div></div>
      </div>
    </section>

    <section class="card">
      <h2>按题型拆分</h2>
      <table>
        <thead><tr><th>类型</th><th>共享题数</th><th>Echo EM</th><th>Echo F1</th><th>OV EM</th><th>OV F1</th></tr></thead>
        <tbody>{render_type_rows(shared)}</tbody>
      </table>
    </section>

    <section class="card">
      <h2>为什么 EchoMemory 当前差</h2>
      <div class="grid">
        <div class="stat"><div class="label">memory not ready before QA</div><div class="value">{echo_memory_not_ready}</div></div>
        <div class="stat"><div class="label">pending_async_memory</div><div class="value">{echo_pending}</div></div>
        <div class="stat"><div class="label">ECHOMEMORY_IMPORT_INCOMPLETE</div><div class="value">{echo_incomplete}</div></div>
        <div class="stat"><div class="label">ECHOMEMORY_IMPORT_FAILED</div><div class="value">{echo_failed}</div></div>
      </div>
      <div class="note">
        当前证据非常直接：EchoMemory 很多题在记忆还没稳定时就进入 QA，<code>reasoning</code> 里已经写出了 <code>memory not ready before QA</code>。这不是单纯模型答不好，而是 HotpotQA 这种“给定文档直接多跳问答”场景下，EchoMemory 这条先抽象再答的链路太慢、太重，导致答案阶段吃到未完成的记忆状态。
      </div>
      <div class="note">
        中断前运行态显示：阶段 <code>{html.escape(echo_stage or "-")}</code>，已写结果 <code>{len(echo_rows)}</code> 行，运行态摘要里平均记忆写入时间约 <code>{html.escape(str(echo_running.get("avg_memory_injection_time_s", "-")))}</code>s，平均 QA 时间仍是 <code>{html.escape(str(echo_running.get("avg_qa_time_s", "-")))}</code>s。这说明主要瓶颈仍在“写入稳定”之前，不在答案生成本身。
      </div>
    </section>

    <section class="card">
      <h2>OpenViking 优势样本</h2>
      <table>
        <thead><tr><th>question_id</th><th>问题</th><th>gold</th><th>Echo</th><th>OV</th><th>Echo F1</th><th>OV F1</th></tr></thead>
        <tbody>{render_case_rows(ov_better[:8])}</tbody>
      </table>
    </section>
  </div>
</body>
</html>
"""

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html_text, encoding="utf-8")
    STATIC_MIRROR.parent.mkdir(parents=True, exist_ok=True)
    STATIC_MIRROR.write_text(html_text, encoding="utf-8")
    print(str(OUTPUT))


if __name__ == "__main__":
    main()
