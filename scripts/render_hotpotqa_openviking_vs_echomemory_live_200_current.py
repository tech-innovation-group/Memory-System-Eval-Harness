#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import importlib.util
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen


ROOT = Path("/Users/chx/locomo-eval-web")
RUNS = ROOT / "runs"
OUTPUT = ROOT / "web/static/generated-reports/hotpotqa_openviking_vs_echomemory_live_200_current.html"
STATIC_MIRROR = ROOT / "static/generated-reports/hotpotqa_openviking_vs_echomemory_live_200_current.html"
REFERENCE = ROOT / "dataset/full/hotpotqa_dev_distractor.json"

ECHO_TASK_ID = "echomemory_generic_qa_20260624_014713_conv30-clean-20260624-000324_132c5d"
OV_TASK_ID = "openviking_generic_qa_20260624_015359_df563c"

ECHO_RUN_DIR = RUNS / ECHO_TASK_ID
OV_RUN_DIR = RUNS / OV_TASK_ID
ECHO_CSV = ECHO_RUN_DIR / "echomemory_generic_qa/echomemory_generic_qa_results.csv"
OV_CSV = OV_RUN_DIR / "openviking_generic_qa/openviking_generic_qa_results.csv"


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_hotpot_metric_module() -> Any:
    script = ROOT / "scripts/hotpotqa_answer_eval.py"
    spec = importlib.util.spec_from_file_location("hotpot_eval_live_200_current", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def task_snapshot(task_id: str) -> dict[str, Any]:
    try:
        with urlopen(f"http://127.0.0.1:19181/api/tasks/{task_id}", timeout=10) as response:
            data = json.load(response)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {"id": task_id, "status": "unknown", "progress": {}}


def short_text(text: Any, limit: int = 160) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else f"{value[: limit - 3]}..."


def pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.2f}%"


def safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def is_unknown_response(text: Any) -> bool:
    normalized = " ".join(str(text or "").strip().lower().split())
    return normalized in {"", "unknown", "i do not know", "i do not know."}


def rows_by_id(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        qid = str(row.get("question_id") or row.get("sample_id") or row.get("native_question_id") or "").strip()
        if qid:
            result[qid] = row
    return result


def build_shared_rows(hotpot_eval: Any, echo_rows: list[dict[str, str]], ov_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    echo_by_id = rows_by_id(echo_rows)
    ov_by_id = rows_by_id(ov_rows)
    shared_ids = sorted(set(echo_by_id) & set(ov_by_id))
    shared: list[dict[str, Any]] = []
    for qid in shared_ids:
        echo = echo_by_id[qid]
        ov = ov_by_id[qid]
        gold = str(echo.get("answer") or ov.get("answer") or "")
        echo_resp = str(echo.get("response") or "")
        ov_resp = str(ov.get("response") or "")
        shared.append(
            {
                "question_id": qid,
                "question": str(echo.get("question") or ov.get("question") or ""),
                "gold": gold,
                "category": str(echo.get("category") or ov.get("category") or ""),
                "echo_response": echo_resp,
                "ov_response": ov_resp,
                "echo_em": hotpot_eval.exact_match(echo_resp, gold),
                "echo_f1": hotpot_eval.f1_score(echo_resp, gold),
                "ov_em": hotpot_eval.exact_match(ov_resp, gold),
                "ov_f1": hotpot_eval.f1_score(ov_resp, gold),
                "echo_import_status": str(echo.get("import_status") or ""),
                "echo_import_integrity": str(echo.get("import_integrity") or ""),
                "echo_reasoning": str(echo.get("reasoning") or ""),
                "echo_final_source": str(echo.get("final_evidence_source") or ""),
                "ov_retrieval_status": str(ov.get("retrieval_status") or ""),
                "ov_reasoning": str(ov.get("reasoning") or ""),
            }
        )
    return shared


def average(rows: list[dict[str, Any]], key: str) -> float | None:
    if not rows:
        return None
    return sum(float(row.get(key) or 0.0) for row in rows) / len(rows)


def by_type(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = "comparison" if "comparison" in row.get("category", "") else ("bridge" if "bridge" in row.get("category", "") else "unknown")
        grouped.setdefault(key, []).append(row)
    return {
        key: {
            "count": len(items),
            "echo_em": average(items, "echo_em") or 0.0,
            "echo_f1": average(items, "echo_f1") or 0.0,
            "ov_em": average(items, "ov_em") or 0.0,
            "ov_f1": average(items, "ov_f1") or 0.0,
        }
        for key, items in grouped.items()
    }


def artifact_flags(run_dir: Path) -> dict[str, bool]:
    return {
        "report": (run_dir / "report.html").exists(),
        "judge": (run_dir / run_dir.name.split("/")[-1]).exists(),
        "official": (run_dir / "openviking_generic_qa/hotpotqa_answer_summary.json").exists() or (run_dir / "echomemory_generic_qa/hotpotqa_answer_summary.json").exists(),
    }


def render_type_rows(type_stats: dict[str, dict[str, float | int]]) -> str:
    rows = []
    for key in ("bridge", "comparison", "unknown"):
        if key not in type_stats:
            continue
        item = type_stats[key]
        rows.append(
            "<tr>"
            f"<td>{html.escape(key)}</td>"
            f"<td>{int(item['count'])}</td>"
            f"<td>{pct(float(item['echo_em']))}</td>"
            f"<td>{pct(float(item['echo_f1']))}</td>"
            f"<td>{pct(float(item['ov_em']))}</td>"
            f"<td>{pct(float(item['ov_f1']))}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def render_case_rows(rows: list[dict[str, Any]]) -> str:
    parts = []
    for row in rows:
        parts.append(
            "<tr>"
            f"<td><code>{html.escape(row['question_id'])}</code></td>"
            f"<td>{html.escape(short_text(row['question'], 120))}</td>"
            f"<td>{html.escape(short_text(row['gold'], 48))}</td>"
            f"<td>{html.escape(short_text(row['echo_response'], 60))}</td>"
            f"<td>{html.escape(short_text(row['ov_response'], 60))}</td>"
            f"<td>{pct(float(row['echo_f1']))}</td>"
            f"<td>{pct(float(row['ov_f1']))}</td>"
            "</tr>"
        )
    return "\n".join(parts)


def main() -> None:
    hotpot_eval = load_hotpot_metric_module()
    echo_task = task_snapshot(ECHO_TASK_ID)
    ov_task = task_snapshot(OV_TASK_ID)
    echo_rows = load_csv(ECHO_CSV)
    ov_rows = load_csv(OV_CSV)
    shared = build_shared_rows(hotpot_eval, echo_rows, ov_rows)

    echo_unknown = sum(is_unknown_response(row.get("response")) for row in echo_rows)
    ov_unknown = sum(is_unknown_response(row.get("response")) for row in ov_rows)
    echo_import_not_ready = sum("memory not ready before QA" in str(row.get("reasoning") or "") for row in echo_rows)
    echo_pending_async = sum(str(row.get("import_integrity") or "") == "pending_async_memory" for row in echo_rows)
    echo_incomplete = sum(str(row.get("import_status") or "") == "ECHOMEMORY_IMPORT_INCOMPLETE" for row in echo_rows)
    ov_ok = sum(str(row.get("retrieval_status") or "") == "ok" for row in ov_rows)
    ov_empty = sum(str(row.get("retrieval_status") or "") == "empty" for row in ov_rows)

    ov_better = [row for row in shared if row["ov_f1"] > row["echo_f1"]]
    echo_better = [row for row in shared if row["echo_f1"] > row["ov_f1"]]
    ties = [row for row in shared if row["echo_f1"] == row["ov_f1"]]
    worst_echo = sorted(
        [row for row in shared if row["ov_f1"] > row["echo_f1"]],
        key=lambda item: (item["ov_f1"] - item["echo_f1"]),
        reverse=True,
    )[:8]

    type_stats = by_type(shared)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html_text = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>HotpotQA 200 Live Compare</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", sans-serif; margin: 0; background: #f5f6f8; color: #16181d; }}
    .wrap {{ max-width: 1180px; margin: 0 auto; padding: 24px 18px 40px; }}
    .card {{ background: #fff; border: 1px solid #d9dce3; border-radius: 12px; padding: 18px; margin-bottom: 16px; }}
    h1, h2, h3 {{ margin: 0 0 10px; letter-spacing: 0; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 12px; }}
    .stat {{ background: #fbfbfc; border: 1px solid #d9dce3; border-radius: 10px; padding: 12px; }}
    .label {{ color: #68707f; font-size: 12px; margin-bottom: 4px; }}
    .value {{ font-size: 22px; font-weight: 700; }}
    .note {{ background: #f8fbff; border-left: 3px solid #1d4ed8; padding: 12px 14px; border-radius: 8px; margin-top: 12px; }}
    .warn {{ background: #fff7f0; border-left-color: #b4690e; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ border-top: 1px solid #d9dce3; padding: 10px 8px; text-align: left; vertical-align: top; }}
    th {{ background: #fbfbfc; color: #68707f; }}
    code {{ background: #eef2f6; border-radius: 6px; padding: 1px 5px; font-size: 12px; }}
    @media (max-width: 900px) {{ .grid {{ grid-template-columns: 1fr 1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="card">
      <h1>HotpotQA 200 Live 对比</h1>
      <p>当前比较的是平台里正在跑的两条 200 题任务：OpenViking clean rerun 与 EchoMemory 200。</p>
      <div class="note">
        历史入口已经可见：任务列表 `/api/tasks` 和历史列表 `/api/runs?include_history=1` 都能看到当前两条 run，以及之前失败的 OpenViking 旧 run。
      </div>
      <p style="color:#68707f;margin-top:10px;">生成时间：{html.escape(generated_at)}</p>
    </section>

    <section class="card">
      <h2>运行状态</h2>
      <div class="grid">
        <div class="stat"><div class="label">EchoMemory 进度</div><div class="value">{html.escape(str((echo_task.get("progress") or {}).get("current") or len(echo_rows)))}/{html.escape(str((echo_task.get("progress") or {}).get("total") or 200))}</div></div>
        <div class="stat"><div class="label">OpenViking 进度</div><div class="value">{html.escape(str((ov_task.get("progress") or {}).get("current") or len(ov_rows)))}/{html.escape(str((ov_task.get("progress") or {}).get("total") or 200))}</div></div>
        <div class="stat"><div class="label">EchoMemory 已落盘</div><div class="value">{len(echo_rows)}</div></div>
        <div class="stat"><div class="label">OpenViking 已落盘</div><div class="value">{len(ov_rows)}</div></div>
      </div>
      <div class="note warn">
        EchoMemory 当前主要瓶颈不是答案模型，而是导入后还要继续做 atom/overview/abstract/indexing；它已多次以 <code>memory not ready before QA</code> 进入问答，说明这轮答案质量差的首要原因是“记忆未稳定就开答”。
      </div>
    </section>

    <section class="card">
      <h2>共享题即时分数</h2>
      <div class="grid">
        <div class="stat"><div class="label">共享题数</div><div class="value">{len(shared)}</div></div>
        <div class="stat"><div class="label">EchoMemory EM / F1</div><div class="value">{pct(average(shared, "echo_em"))} / {pct(average(shared, "echo_f1"))}</div></div>
        <div class="stat"><div class="label">OpenViking EM / F1</div><div class="value">{pct(average(shared, "ov_em"))} / {pct(average(shared, "ov_f1"))}</div></div>
        <div class="stat"><div class="label">OV 更好 / Echo 更好 / 平局</div><div class="value">{len(ov_better)} / {len(echo_better)} / {len(ties)}</div></div>
      </div>
    </section>

    <section class="card">
      <h2>按题型拆分</h2>
      <table>
        <thead>
          <tr><th>类型</th><th>共享题数</th><th>Echo EM</th><th>Echo F1</th><th>OV EM</th><th>OV F1</th></tr>
        </thead>
        <tbody>
          {render_type_rows(type_stats)}
        </tbody>
      </table>
    </section>

    <section class="card">
      <h2>为什么 EchoMemory 当前更差</h2>
      <div class="grid">
        <div class="stat"><div class="label">Echo unknown/empty</div><div class="value">{echo_unknown}</div></div>
        <div class="stat"><div class="label">memory not ready before QA</div><div class="value">{echo_import_not_ready}</div></div>
        <div class="stat"><div class="label">pending_async_memory</div><div class="value">{echo_pending_async}</div></div>
        <div class="stat"><div class="label">ECHOMEMORY_IMPORT_INCOMPLETE</div><div class="value">{echo_incomplete}</div></div>
      </div>
      <div class="grid" style="margin-top:12px;">
        <div class="stat"><div class="label">OV retrieval ok</div><div class="value">{ov_ok}</div></div>
        <div class="stat"><div class="label">OV retrieval empty</div><div class="value">{ov_empty}</div></div>
        <div class="stat"><div class="label">Echo run dir</div><div class="value"><code>{html.escape(str(ECHO_RUN_DIR))}</code></div></div>
        <div class="stat"><div class="label">OV run dir</div><div class="value"><code>{html.escape(str(OV_RUN_DIR))}</code></div></div>
      </div>
      <div class="note">
        这轮 HotpotQA 更偏“给定文档后直接检索原文回答”。OpenViking 当前路径是把每题 source documents 直接物化成可检索文档记忆，再答题；EchoMemory 当前路径是先把这些文档过一遍原子抽取、摘要和索引，结果 QA 常常发生在记忆稳定前，因此容易出现空答、unknown 或证据不完整。
      </div>
    </section>

    <section class="card">
      <h2>OpenViking 优势样本</h2>
      <table>
        <thead>
          <tr><th>question_id</th><th>问题</th><th>gold</th><th>Echo</th><th>OV</th><th>Echo F1</th><th>OV F1</th></tr>
        </thead>
        <tbody>
          {render_case_rows(worst_echo)}
        </tbody>
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
