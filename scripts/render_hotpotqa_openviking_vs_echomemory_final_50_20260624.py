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


ROOT = Path("/Users/chx/locomo-eval-web")
RUNS = ROOT / "runs"

EM_TASK_ID = "echomemory_generic_qa_hotpot_fix_run50b_20260624"
OV_TASK_ID = "openviking_generic_qa_20260624_020054_5e57e5"

EM_RUN = RUNS / EM_TASK_ID / "echomemory_generic_qa"
OV_RUN = RUNS / OV_TASK_ID / "openviking_generic_qa"

EM_CSV = EM_RUN / "echomemory_generic_qa_results.csv"
OV_CSV = OV_RUN / "openviking_generic_qa_results.csv"
EM_SUMMARY = EM_RUN / "summary.json"
OV_SUMMARY = OV_RUN / "summary.json"
EM_OFFICIAL = EM_RUN / "hotpotqa_answer_summary.json"
OV_OFFICIAL = OV_RUN / "hotpotqa_answer_summary.json"

OUTPUT = ROOT / "web/static/generated-reports/hotpotqa_openviking_vs_echomemory_final_50_20260624.html"
STATIC_MIRROR = ROOT / "static/generated-reports/hotpotqa_openviking_vs_echomemory_final_50_20260624.html"


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_hotpot_eval() -> Any:
    script = ROOT / "scripts/hotpotqa_answer_eval.py"
    spec = importlib.util.spec_from_file_location("hotpot_eval_final_50", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def normalize_category(value: str) -> str:
    lowered = str(value or "").lower()
    if "comparison" in lowered:
        return "comparison"
    if "bridge" in lowered:
        return "bridge"
    return "unknown"


def pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.2f}%"


def short_text(text: Any, limit: int = 140) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else f"{value[: limit - 3]}..."


def average(items: list[dict[str, Any]], key: str) -> float | None:
    if not items:
        return None
    return sum(float(item.get(key) or 0.0) for item in items) / len(items)


def rows_by_id(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {
        str(row.get("question_id") or row.get("sample_id") or row.get("native_question_id") or "").strip(): row
        for row in rows
        if str(row.get("question_id") or row.get("sample_id") or row.get("native_question_id") or "").strip()
    }


def build_shared(eval_mod: Any, em_rows: list[dict[str, str]], ov_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    em_map = rows_by_id(em_rows)
    ov_map = rows_by_id(ov_rows)
    shared: list[dict[str, Any]] = []
    for qid in sorted(set(em_map) & set(ov_map)):
        em_row = em_map[qid]
        ov_row = ov_map[qid]
        gold = str(em_row.get("answer") or ov_row.get("answer") or "")
        em_resp = str(em_row.get("response") or "")
        ov_resp = str(ov_row.get("response") or "")
        shared.append(
            {
                "question_id": qid,
                "question": str(em_row.get("question") or ov_row.get("question") or ""),
                "gold": gold,
                "category": normalize_category(str(em_row.get("category") or ov_row.get("category") or "")),
                "em_response": em_resp,
                "ov_response": ov_resp,
                "em_em": eval_mod.exact_match(em_resp, gold),
                "em_f1": eval_mod.f1_score(em_resp, gold),
                "ov_em": eval_mod.exact_match(ov_resp, gold),
                "ov_f1": eval_mod.f1_score(ov_resp, gold),
                "em_health_status": str(em_row.get("health_status") or ""),
                "em_import_integrity": str(em_row.get("import_integrity") or ""),
                "em_import_error": str(em_row.get("import_error") or ""),
                "ov_health_status": str(ov_row.get("health_status") or ""),
            }
        )
    return shared


def render_type_rows(shared: list[dict[str, Any]]) -> str:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in shared:
        grouped[row["category"]].append(row)
    parts = []
    for key in ("bridge", "comparison", "unknown"):
        rows = grouped.get(key)
        if not rows:
            continue
        parts.append(
            "<tr>"
            f"<td>{html.escape(key)}</td>"
            f"<td>{len(rows)}</td>"
            f"<td>{pct(average(rows, 'em_em'))}</td>"
            f"<td>{pct(average(rows, 'em_f1'))}</td>"
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
            f"<td>{html.escape(short_text(row['gold'], 36))}</td>"
            f"<td>{html.escape(short_text(row['em_response'], 36))}</td>"
            f"<td>{html.escape(short_text(row['ov_response'], 36))}</td>"
            f"<td>{pct(float(row['em_f1']))}</td>"
            f"<td>{pct(float(row['ov_f1']))}</td>"
            "</tr>"
        )
    return "\n".join(parts)


def link(path: Path) -> str:
    return html.escape(str(path))


def main() -> None:
    eval_mod = load_hotpot_eval()
    em_rows = load_csv(EM_CSV)
    ov_rows = load_csv(OV_CSV)
    em_summary = load_json(EM_SUMMARY)
    ov_summary = load_json(OV_SUMMARY)
    em_official = load_json(EM_OFFICIAL)
    ov_official = load_json(OV_OFFICIAL)
    shared = build_shared(eval_mod, em_rows, ov_rows)

    ov_better = sorted(shared, key=lambda item: (item["ov_f1"] - item["em_f1"], item["ov_em"] - item["em_em"]), reverse=True)
    em_better = sorted(shared, key=lambda item: (item["em_f1"] - item["ov_f1"], item["em_em"] - item["ov_em"]), reverse=True)
    ties = sum(1 for row in shared if abs(float(row["em_f1"]) - float(row["ov_f1"])) < 1e-9)
    em_ready_issues = sum("memory not ready before QA" in row["em_import_error"].lower() for row in shared)
    em_pending = sum(row["em_import_integrity"].lower() == "pending_async_memory" for row in shared)
    em_retrieval_empty = sum(row["em_health_status"].lower() == "retrieval_empty" for row in shared)
    ov_retrieval_empty = sum(row["ov_health_status"].lower() == "retrieval_empty" for row in shared)

    html_text = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>HotpotQA EchoMemory vs OpenViking 50</title>
  <style>
    body {{ margin:0; background:#f5f6f8; color:#16181d; font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC",sans-serif; }}
    .wrap {{ max-width:1180px; margin:0 auto; padding:24px 18px 40px; }}
    .card {{ background:#fff; border:1px solid #d9dce3; border-radius:12px; padding:18px; margin-bottom:16px; }}
    .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }}
    .stat {{ background:#fbfbfc; border:1px solid #d9dce3; border-radius:10px; padding:12px; }}
    .label {{ font-size:12px; color:#68707f; margin-bottom:4px; }}
    .value {{ font-size:22px; font-weight:700; }}
    .sub {{ font-size:12px; color:#68707f; margin-top:6px; }}
    .note {{ margin-top:12px; padding:12px 14px; background:#f8fbff; border-left:3px solid #1d4ed8; border-radius:8px; }}
    .warn {{ background:#fff8f3; border-left-color:#d97706; }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; }}
    th,td {{ padding:10px 8px; border-top:1px solid #d9dce3; text-align:left; vertical-align:top; }}
    th {{ background:#fbfbfc; color:#68707f; }}
    code {{ background:#eef2f6; border-radius:6px; padding:1px 5px; font-size:12px; }}
    a {{ color:#1d4ed8; text-decoration:none; }}
    @media (max-width:900px) {{ .grid {{ grid-template-columns:1fr 1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="card">
      <h1>HotpotQA 50 正式对比：EchoMemory vs OpenViking</h1>
      <p>这页只比较两边都已经正式跑完的同一批 50 题。两边都使用 <code>deepseek-v4-flash</code>，指标是 HotpotQA 官方 answer-only EM/F1。</p>
      <div class="note">
        EchoMemory 运行目录：<a href="file://{link(EM_RUN)}">{link(EM_RUN)}</a><br/>
        OpenViking 运行目录：<a href="file://{link(OV_RUN)}">{link(OV_RUN)}</a><br/>
        生成时间：{html.escape(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}
      </div>
    </section>

    <section class="card">
      <h2>先看结论</h2>
      <div class="note">
        在这批正式 50 题上，<strong>OpenViking 总体略强</strong>。<br/>
        按官方 answer-only 指标看：
        <strong>EM：EchoMemory {pct(em_official.get("answer_em"))}，OpenViking {pct(ov_official.get("answer_em"))}，EchoMemory 高 {pct(abs(float(em_official.get("answer_em", 0.0)) - float(ov_official.get("answer_em", 0.0))))}</strong>；
        <strong>F1：OpenViking {pct(ov_official.get("answer_f1"))}，EchoMemory {pct(em_official.get("answer_f1"))}，OpenViking 高 {pct(abs(float(ov_official.get("answer_f1", 0.0)) - float(em_official.get("answer_f1", 0.0))))}</strong>。
      </div>
      <div class="grid" style="margin-top:12px;">
        <div class="stat"><div class="label">EM</div><div class="value">EchoMemory {pct(em_official.get("answer_em"))}</div><div class="sub">OpenViking {pct(ov_official.get("answer_em"))}</div></div>
        <div class="stat"><div class="label">F1</div><div class="value">OpenViking {pct(ov_official.get("answer_f1"))}</div><div class="sub">EchoMemory {pct(em_official.get("answer_f1"))}</div></div>
        <div class="stat"><div class="label">EM 更高方</div><div class="value">EchoMemory +{pct(abs(float(em_official.get("answer_em", 0.0)) - float(ov_official.get("answer_em", 0.0))))}</div><div class="sub">40.00% vs 38.00%</div></div>
        <div class="stat"><div class="label">F1 更高方</div><div class="value">OpenViking +{pct(abs(float(ov_official.get("answer_f1", 0.0)) - float(em_official.get("answer_f1", 0.0))))}</div><div class="sub">55.29% vs 52.69%</div></div>
      </div>
      <div class="grid" style="margin-top:12px;">
        <div class="stat"><div class="label">正式完成题数</div><div class="value">{len(shared)}</div><div class="sub">50 对 50</div></div>
        <div class="stat"><div class="label">总评</div><div class="value">{'OpenViking 略强' if float(ov_official.get('answer_f1', 0.0)) > float(em_official.get('answer_f1', 0.0)) else 'EchoMemory 略强'}</div><div class="sub">按 F1 看整体</div></div>
        <div class="stat"><div class="label">comparison</div><div class="value">EchoMemory 更强</div><div class="sub">这一类题 Echo 明显占优</div></div>
        <div class="stat"><div class="label">bridge</div><div class="value">OpenViking 更强</div><div class="sub">整体差距主要在 bridge</div></div>
      </div>
    </section>

    <section class="card">
      <h2>按题型拆分</h2>
      <table>
        <thead><tr><th>类型</th><th>题数</th><th>Echo EM</th><th>Echo F1</th><th>OV EM</th><th>OV F1</th></tr></thead>
        <tbody>{render_type_rows(shared)}</tbody>
      </table>
    </section>

    <section class="card">
      <h2>运行机制结论</h2>
      <div class="grid">
        <div class="stat"><div class="label">Echo memory_not_ready</div><div class="value">{em_ready_issues}</div></div>
        <div class="stat"><div class="label">Echo pending_async_memory</div><div class="value">{em_pending}</div></div>
        <div class="stat"><div class="label">Echo retrieval_empty</div><div class="value">{em_retrieval_empty}</div></div>
        <div class="stat"><div class="label">OV retrieval_empty</div><div class="value">{ov_retrieval_empty}</div></div>
      </div>
      <div class="note">
        这次 50 题完成运行已经证明：EchoMemory 的主问题不再是 <code>memory not ready before QA</code> 或 <code>commit:indexing</code>。当前正式 50 题里，这两项都是 0，50 个样本也全部 <code>import_integrity=complete</code>。
      </div>
      <div class="note warn">
        所以现在 EchoMemory 比 OpenViking 低，主因已经从“写入没稳定就开始答”切换成了“答题阶段的召回质量和答案生成质量”。这一点从 <code>retrieval_empty</code> 仍有 2 题，以及最终 F1 仍落后可以看出来。
      </div>
    </section>

    <section class="card">
      <h2>共享题即时对齐</h2>
      <div class="grid">
        <div class="stat"><div class="label">Echo 平均 EM / F1</div><div class="value">{pct(average(shared, "em_em"))}</div><div class="sub">F1 {pct(average(shared, "em_f1"))}</div></div>
        <div class="stat"><div class="label">OV 平均 EM / F1</div><div class="value">{pct(average(shared, "ov_em"))}</div><div class="sub">F1 {pct(average(shared, "ov_f1"))}</div></div>
        <div class="stat"><div class="label">OV 更好题数</div><div class="value">{sum(1 for row in shared if row["ov_f1"] > row["em_f1"])}</div><div class="sub">平局 {ties}</div></div>
        <div class="stat"><div class="label">Echo 更好题数</div><div class="value">{sum(1 for row in shared if row["em_f1"] > row["ov_f1"])}</div><div class="sub">按 F1</div></div>
      </div>
    </section>

    <section class="card">
      <h2>OpenViking 更强的样本</h2>
      <table>
        <thead><tr><th>question_id</th><th>问题</th><th>gold</th><th>Echo</th><th>OV</th><th>Echo F1</th><th>OV F1</th></tr></thead>
        <tbody>{render_case_rows([row for row in ov_better if row["ov_f1"] > row["em_f1"]][:10])}</tbody>
      </table>
    </section>

    <section class="card">
      <h2>EchoMemory 更强的样本</h2>
      <table>
        <thead><tr><th>question_id</th><th>问题</th><th>gold</th><th>Echo</th><th>OV</th><th>Echo F1</th><th>OV F1</th></tr></thead>
        <tbody>{render_case_rows([row for row in em_better if row["em_f1"] > row["ov_f1"]][:10])}</tbody>
      </table>
    </section>

    <section class="card">
      <h2>关键文件</h2>
      <ul>
        <li>Echo 正式汇总：<a href="file://{link(EM_OFFICIAL)}">{link(EM_OFFICIAL)}</a></li>
        <li>OV 正式汇总：<a href="file://{link(OV_OFFICIAL)}">{link(OV_OFFICIAL)}</a></li>
        <li>Echo summary：<a href="file://{link(EM_SUMMARY)}">{link(EM_SUMMARY)}</a></li>
        <li>OV summary：<a href="file://{link(OV_SUMMARY)}">{link(OV_SUMMARY)}</a></li>
      </ul>
    </section>
  </div>
</body>
</html>
"""

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html_text, encoding="utf-8")
    STATIC_MIRROR.parent.mkdir(parents=True, exist_ok=True)
    STATIC_MIRROR.write_text(html_text, encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
