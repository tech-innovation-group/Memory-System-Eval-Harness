#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import html
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def int0(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def pct(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "0.00%"
    return f"{numerator / denominator:.2%}"


def token_char_estimate(tokens: Any, chars_per_token: int = 4) -> int:
    try:
        token_count = max(0, int(tokens or 0))
    except Exception:
        return 0
    try:
        multiplier = max(1, int(chars_per_token or 4))
    except Exception:
        multiplier = 4
    return token_count * multiplier


def compact(text: Any, limit: int = 180) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value if len(value) <= limit else value[: limit - 3] + "..."


LOG_PATTERN = re.compile(
    r"\[LLM\] call_site=(?P<call_site>\S+) "
    r"model=(?P<model_alias>\S+)/(?P<model_id>\S+) "
    r"input=(?P<input>\d+) output=(?P<output>\d+) "
    r"total=(?P<total>\d+) latency=(?P<latency>[\d.]+)ms "
    r"account=(?P<account>\S+) session=(?P<session>\S+)"
)


def parse_llm_logs(paths: list[Path]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = LOG_PATTERN.search(line)
            if not match:
                continue
            data = match.groupdict()
            data["input"] = int(data["input"])
            data["output"] = int(data["output"])
            data["total"] = int(data["total"])
            data["latency"] = float(data["latency"])
            records.append(data)
    by_call_site: Counter[str] = Counter()
    by_model: Counter[str] = Counter()
    for record in records:
        by_call_site[str(record["call_site"])] += int(record["total"])
        by_model[f'{record["model_alias"]}/{record["model_id"]}'] += int(record["total"])
    return {
        "records": records,
        "input_tokens": sum(int(record["input"]) for record in records),
        "output_tokens": sum(int(record["output"]) for record in records),
        "total_tokens": sum(int(record["total"]) for record in records),
        "total_calls": len(records),
        "by_call_site": dict(by_call_site),
        "by_model": dict(by_model),
    }


def load_openviking_run(label: str, summary_path: Path, judge_path: Path, csv_path: Path) -> dict[str, Any]:
    summary = read_json(summary_path) if summary_path.exists() else {}
    judge = read_json(judge_path) if judge_path.exists() else {}
    rows = read_csv_rows(csv_path)
    tool_name_counts: Counter[str] = Counter()
    for row in rows:
        try:
            tool_name_counts.update(json.loads(row.get("tool_call_name_counts") or "{}"))
        except Exception:
            pass
    answer_prompt_tokens = sum(int0(r.get("answer_prompt_tokens")) for r in rows)
    answer_completion_tokens = sum(int0(r.get("answer_completion_tokens")) for r in rows)
    answer_total_tokens = sum(int0(r.get("answer_total_tokens")) for r in rows)
    answer_prompt_chars_actual = sum(int0(r.get("answer_prompt_chars_actual")) for r in rows)
    answer_completion_chars_actual = sum(int0(r.get("answer_completion_chars_actual")) for r in rows)
    answer_total_chars_actual = sum(int0(r.get("answer_total_chars_actual")) for r in rows)
    tool_calls = sum(int0(r.get("tool_call_count")) for r in rows)
    healthy = {
        "model_ok": sum(1 for r in rows if r.get("model_status") == "ok"),
        "retrieval_ok": sum(1 for r in rows if r.get("retrieval_status") == "ok"),
        "health_ok": sum(1 for r in rows if r.get("health_status") == "ok"),
        "retrieval_errors": sum(1 for r in rows if str(r.get("retrieval_error") or "").strip()),
    }
    outliers = sorted(rows, key=lambda r: int0(r.get("answer_total_tokens")), reverse=True)[:5]
    return {
        "label": label,
        "summary": summary,
        "judge": judge,
        "rows": rows,
        "count": int(judge.get("count") or len(rows) or 0),
        "correct": int(judge.get("correct") or 0),
        "wrong": int(judge.get("wrong") or 0),
        "accuracy": float(judge.get("accuracy") or 0.0),
        "tool_set": summary.get("openviking_tool_set") or summary.get("tool_set") or "",
        "loop_enabled": bool(summary.get("openviking_tool_loop_enabled")),
        "tool_calls": tool_calls,
        "answer_prompt_tokens": answer_prompt_tokens,
        "answer_completion_tokens": answer_completion_tokens,
        "answer_total_tokens": answer_total_tokens,
        "answer_prompt_chars_actual": answer_prompt_chars_actual,
        "answer_completion_chars_actual": answer_completion_chars_actual,
        "answer_total_chars_actual": answer_total_chars_actual,
        "answer_prompt_chars_est": int(summary.get("answer_prompt_chars_est") or token_char_estimate(answer_prompt_tokens)),
        "answer_completion_chars_est": int(summary.get("answer_completion_chars_est") or token_char_estimate(answer_completion_tokens)),
        "answer_total_chars_est": int(summary.get("answer_total_chars_est") or token_char_estimate(answer_total_tokens)),
        "internal_llm_input_tokens": int(summary.get("internal_llm_input_tokens") or 0),
        "internal_llm_output_tokens": int(summary.get("internal_llm_output_tokens") or 0),
        "internal_llm_total_tokens": int(summary.get("internal_llm_total_tokens") or 0),
        "internal_llm_skipped_duplicate_tokens": int(summary.get("internal_llm_skipped_duplicate_tokens") or 0),
        "tool_name_counts": dict(tool_name_counts),
        "healthy": healthy,
        "outliers": outliers,
        "summary_path": str(summary_path),
        "judge_path": str(judge_path),
        "csv_path": str(csv_path),
        "workspace": str(summary.get("workspace") or ""),
        "account": str(summary.get("account") or ""),
        "openviking_url": str(summary.get("openviking_url") or ""),
        "dataset": str(summary.get("dataset") or ""),
        "sample": str(summary.get("sample") or ""),
        "judge_model": str(summary.get("judge_model") or judge.get("judge_model") or ""),
        "answer_model": str(summary.get("answer_model") or ""),
        "prompt_mode": str(summary.get("prompt_mode") or ""),
    }


def load_import_summary(path: Path) -> dict[str, Any]:
    data = read_json(path) if path.exists() else {}
    records = data.get("records") or []
    first = records[0] if records else {}
    return {
        "path": str(path),
        "status": str(data.get("status") or ""),
        "sample": str(data.get("sample") or ""),
        "group_chat": bool(data.get("group_chat")),
        "identity_mode": str(data.get("identity_mode") or ""),
        "openviking_url": str(data.get("openviking_url") or ""),
        "samples": int(data.get("samples") or 0),
        "complete_samples": int(data.get("complete_samples") or 0),
        "estimated_import_tokens": int(data.get("estimated_import_tokens") or 0),
        "import_llm_prompt_tokens": int(data.get("import_llm_prompt_tokens") or 0),
        "import_llm_completion_tokens": int(data.get("import_llm_completion_tokens") or 0),
        "import_llm_total_tokens": int(data.get("import_llm_total_tokens") or 0),
        "import_embedding_total_tokens": int(data.get("import_embedding_total_tokens") or 0),
        "import_total_tokens": int(data.get("import_total_tokens") or 0),
        "import_llm_prompt_chars_est": int(data.get("import_llm_prompt_chars_est") or token_char_estimate(data.get("import_llm_prompt_tokens"))),
        "import_llm_completion_chars_est": int(data.get("import_llm_completion_chars_est") or token_char_estimate(data.get("import_llm_completion_tokens"))),
        "import_llm_total_chars_est": int(data.get("import_llm_total_chars_est") or token_char_estimate(data.get("import_llm_total_tokens"))),
        "import_embedding_chars_est": int(data.get("import_embedding_chars_est") or token_char_estimate(data.get("import_embedding_total_tokens"))),
        "import_total_chars_est": int(data.get("import_total_chars_est") or token_char_estimate(data.get("import_total_tokens"))),
        "session_count": int(first.get("session_count") or 0),
    }


def render_comparison_table(openviking_runs: list[dict[str, Any]], echomemory: dict[str, Any]) -> str:
    rows = []
    for run in openviking_runs:
        rows.append(
            f"""
            <tr>
              <td><strong>{esc(run["label"])}</strong><div class="small">{esc(run["tool_set"] or "-")}</div></td>
              <td>{run["correct"]}/{run["count"]} = {pct(run["correct"], run["count"])}</td>
              <td>{run["answer_total_tokens"]:,}</td>
              <td>{run["combined_total_tokens"]:,}</td>
              <td>{run["tool_calls"]:,}</td>
              <td>{esc(run["judge_model"] or "-")}</td>
            </tr>
            """
        )
    rows.append(
        f"""
        <tr>
          <td><strong>EchoMemory v0.1.0</strong></td>
          <td>{echomemory["accuracy"]}</td>
          <td>{echomemory["qa_tokens"]:,}</td>
          <td>{echomemory["total_tokens"]:,}</td>
          <td>-</td>
          <td>{esc(echomemory["judge_model"])}</td>
        </tr>
        """
    )
    return "\n".join(rows)


def render_html(openviking_runs: list[dict[str, Any]], echomemory: dict[str, Any], import_summary: dict[str, Any], generated_at: str) -> str:
    tool_on = next(run for run in openviking_runs if run["label"] == "tool_on")
    tool_off = next(run for run in openviking_runs if run["label"] == "tool_off")
    search_only = next(run for run in openviking_runs if run["label"] == "search_only")
    same_on_off = sum(1 for row in tool_on["rows"] if row.get("response") == next((r.get("response") for r in tool_off["rows"] if r.get("question_id") == row.get("question_id")), None))
    same_on_search = sum(1 for row in tool_on["rows"] if row.get("response") == next((r.get("response") for r in search_only["rows"] if r.get("question_id") == row.get("question_id")), None))
    import_tokens = int(import_summary.get("import_total_tokens") or 0)
    import_prompt_tokens = int(import_summary.get("import_llm_prompt_tokens") or 0)
    import_completion_tokens = int(import_summary.get("import_llm_completion_tokens") or 0)
    accuracy_drop_points = tool_on["accuracy"] - tool_off["accuracy"]
    accuracy_drop_correct = tool_on["correct"] - tool_off["correct"]
    for run in openviking_runs:
        run["combined_total_tokens"] = run["answer_total_tokens"] + import_tokens
        run["combined_prompt_tokens"] = run["answer_prompt_tokens"] + import_prompt_tokens
        run["combined_completion_tokens"] = run["answer_completion_tokens"] + import_completion_tokens
        run["combined_prompt_chars_est"] = token_char_estimate(run["combined_prompt_tokens"])
        run["combined_completion_chars_est"] = token_char_estimate(run["combined_completion_tokens"])
        run["combined_total_chars_est"] = token_char_estimate(run["combined_total_tokens"])

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="refresh" content="3600" />
  <title>OpenViking / EchoMemory conv-30 工具公平性复盘</title>
  <style>
    :root {{
      --bg:#f5f5f7; --panel:#fff; --text:#1d1d1f; --muted:#6e6e73; --line:#d2d2d7;
      --soft:#f2f2f5; --blue:#0071e3; --green:#1d9b5f; --amber:#b26a00; --red:#c9342f;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.7 -apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif; }}
    main {{ max-width:1180px; margin:0 auto; padding:28px 18px 60px; }}
    section {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:24px; margin-bottom:16px; }}
    h1,h2,h3 {{ margin:0 0 12px; line-height:1.28; }}
    h1 {{ font-size:30px; }} h2 {{ font-size:21px; }} h3 {{ font-size:17px; margin-top:18px; }}
    p,li {{ margin:8px 0; }}
    ul,ol {{ margin:8px 0 8px 20px; }}
    code,.mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
    code {{ background:var(--soft); padding:2px 5px; border-radius:4px; }}
    pre {{ margin:10px 0; padding:12px 14px; border:1px solid var(--line); border-radius:10px; background:var(--soft); overflow:auto; }}
    table {{ width:100%; border-collapse:collapse; margin-top:12px; }}
    th,td {{ border:1px solid var(--line); padding:10px 12px; text-align:left; vertical-align:top; }}
    th {{ background:var(--soft); }}
    .small {{ color:var(--muted); font-size:13px; }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }}
    .kpi-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-top:12px; }}
    .card {{ border:1px solid var(--line); border-radius:12px; padding:14px 16px; background:#fff; }}
    .kpi .label {{ font-size:13px; color:var(--muted); margin-bottom:6px; }}
    .kpi .value {{ font-size:26px; font-weight:700; }}
    .tag {{ display:inline-block; border-radius:999px; padding:3px 8px; font-size:12px; font-weight:600; margin-right:6px; }}
    .good {{ color:var(--green); background:#ecf9f1; }}
    .warn {{ color:var(--amber); background:#fff7e8; }}
    .bad {{ color:var(--red); background:#fff1f0; }}
    .blue {{ color:var(--blue); background:#edf4ff; }}
    .callout {{ margin-top:12px; padding:14px 16px; border:1px solid var(--line); border-left:4px solid var(--blue); border-radius:12px; background:#fff; }}
    .path {{ word-break:break-all; }}
    @media (max-width:960px) {{
      .grid,.kpi-grid {{ grid-template-columns:1fr; }}
    }}
  </style>
</head>
<body>
<main>
  <section>
    <div class="small">生成时间：{esc(generated_at)}</div>
    <h1>OpenViking / EchoMemory conv-30 工具公平性复盘</h1>
    <p class="small">先看结论：<strong>不建议为了“公平”直接关闭工具调用</strong>。工具关掉后准确率掉得太明显；真正该修的是工具预算和循环控制。</p>
    <div class="callout">
      <strong>一句话判断：</strong> 这组对比里没有看到“工具调用本身不稳定”的证据，看到的是“工具策略一变，路径和 token 就大幅变”。</span>
    </div>
    <div class="kpi-grid">
      <div class="card kpi"><div class="label">OpenViking tool_on</div><div class="value">{tool_on["correct"]}/{tool_on["count"]}</div><div class="small">{pct(tool_on["correct"], tool_on["count"])} · QA {tool_on["answer_total_tokens"]:,} · total {tool_on["combined_total_tokens"]:,}</div></div>
      <div class="card kpi"><div class="label">OpenViking search_only</div><div class="value">{search_only["correct"]}/{search_only["count"]}</div><div class="small">{pct(search_only["correct"], search_only["count"])} · QA {search_only["answer_total_tokens"]:,} · total {search_only["combined_total_tokens"]:,}</div></div>
      <div class="card kpi"><div class="label">OpenViking tool_off</div><div class="value">{tool_off["correct"]}/{tool_off["count"]}</div><div class="small">{pct(tool_off["correct"], tool_off["count"])} · QA {tool_off["answer_total_tokens"]:,} · total {tool_off["combined_total_tokens"]:,}</div></div>
      <div class="card kpi"><div class="label">EchoMemory v0.1.0</div><div class="value">{echomemory["correct"]}/{echomemory["count"]}</div><div class="small">{echomemory["total_tokens"]:,} internal tokens</div></div>
    </div>
  </section>

  <section>
    <h2>1. 这次对比公平吗</h2>
    <div class="grid">
      <div class="card">
        <h3>相对公平的部分</h3>
        <ul>
          <li>同一数据集：LoCoMo <code>conv-30</code>，81 题。</li>
          <li>同一 judge：<code>deepseek-v4-flash</code>。</li>
          <li>同一 OpenViking 版本：<code>v0.3.24</code>，同一导入记忆。</li>
          <li>三组 OpenViking 运行都 <code>model/retrieval/health = ok</code>，没有接口错误。</li>
        </ul>
      </div>
      <div class="card">
        <h3>不完全可直接硬比的部分</h3>
        <ul>
          <li>EchoMemory 的 <code>1,054,535</code> 是内部 gateway 日志口径。</li>
          <li>OpenViking 当前主要展示的是 QA provider usage + import telemetry，边界不完全一样。</li>
          <li>所以“总 token 绝对值”先做参考，不要直接下最终结论。</li>
        </ul>
      </div>
    </div>
  </section>

  <section>
    <h2>2. OpenViking 三种工具策略</h2>
    <table>
      <thead>
        <tr>
          <th>模式</th>
          <th>准确率</th>
          <th>QA tokens</th>
          <th>Import + QA</th>
          <th>tool calls</th>
          <th>状态</th>
        </tr>
      </thead>
      <tbody>
        {render_comparison_rows(openviking_runs)}
      </tbody>
    </table>
    <div class="callout">
      <strong>结论：</strong> 关掉工具后准确率掉到 <span class="mono">48.15%</span>，这已经不是“更公平”，而是把系统的检索能力砍掉了。
      <br/>和 <span class="mono">tool_on</span> 相比，准确率少了 <span class="mono">{accuracy_drop_correct}</span> 题，下降 <span class="mono">{accuracy_drop_points:.2%}</span>（即 <span class="mono">{accuracy_drop_points * 100:.2f}</span> 个百分点）。
      <br/>保留 search-only 时准确率还能维持 <span class="mono">69.14%</span>，但 QA token 飙到 <span class="mono">7,139,641</span>，总 token 也到 <span class="mono">{search_only["combined_total_tokens"]:,}</span>，说明问题在循环控制，不在“有没有工具”。
    </div>
  </section>

  <section>
    <h2>3. 工具是不稳定，还是策略变了</h2>
    <table>
      <thead>
        <tr>
          <th>检查项</th>
          <th>tool_on</th>
          <th>search_only</th>
          <th>tool_off</th>
        </tr>
      </thead>
      <tbody>
        <tr><td>model_status = ok</td><td>{tool_on["healthy"]["model_ok"]}/81</td><td>{search_only["healthy"]["model_ok"]}/81</td><td>{tool_off["healthy"]["model_ok"]}/81</td></tr>
        <tr><td>retrieval_status = ok</td><td>{tool_on["healthy"]["retrieval_ok"]}/81</td><td>{search_only["healthy"]["retrieval_ok"]}/81</td><td>{tool_off["healthy"]["retrieval_ok"]}/81</td></tr>
        <tr><td>health_status = ok</td><td>{tool_on["healthy"]["health_ok"]}/81</td><td>{search_only["healthy"]["health_ok"]}/81</td><td>{tool_off["healthy"]["health_ok"]}/81</td></tr>
        <tr><td>retrieval_error</td><td>{tool_on["healthy"]["retrieval_errors"]}</td><td>{search_only["healthy"]["retrieval_errors"]}</td><td>{tool_off["healthy"]["retrieval_errors"]}</td></tr>
        <tr><td>与 tool_on 的 response 完全相同</td><td colspan="3">{same_on_off} / 81（tool_off）· {same_on_search} / 81（search_only）</td></tr>
      </tbody>
    </table>
    <div class="callout">
      <strong>判断：</strong> 这里没有看到“工具调用本身报错或抖动”。更像是工具策略改变后，模型走了完全不同的推理轨迹。
    </div>
  </section>

  <section>
    <h2>4. EchoMemory 这边的真实口径</h2>
    <div class="grid">
      <div class="card">
        <h3>内部 LLM 日志</h3>
        <ul>
          <li>输入：<strong>{echomemory["input_tokens"]:,}</strong></li>
          <li>输出：<strong>{echomemory["output_tokens"]:,}</strong></li>
          <li>总计：<strong>{echomemory["total_tokens"]:,}</strong></li>
        </ul>
      </div>
      <div class="card">
        <h3>按阶段拆账</h3>
        <ul>
          <li>import.log：<strong>{echomemory["import_tokens"]:,}</strong></li>
          <li>qa_shard*/qa.log：<strong>{echomemory["qa_tokens"]:,}</strong></li>
          <li>其中 embedding 记为 0，主要是 chat 侧 usage。</li>
        </ul>
      </div>
    </div>
    <div class="callout">
      <strong>关键点：</strong> EchoMemory 的 token 是内部 gateway 账，OpenViking 当前展示的是 benchmark 侧可见 usage。两边口径没完全贴平之前，别拿一个总数直接说谁更省。
    </div>
  </section>

  <section>
    <h2>5. 输入 / 输出 token 与字符预估</h2>
    <table>
      <thead>
        <tr>
          <th>模式</th>
          <th>QA prompt / completion</th>
          <th>Import prompt / completion</th>
          <th>Import + QA prompt / completion</th>
          <th>字符预估</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>tool_on</strong></td>
          <td>{tool_on["answer_prompt_tokens"]:,} / {tool_on["answer_completion_tokens"]:,}</td>
          <td>{import_summary["import_llm_prompt_tokens"]:,} / {import_summary["import_llm_completion_tokens"]:,}</td>
          <td>{tool_on["combined_prompt_tokens"]:,} / {tool_on["combined_completion_tokens"]:,}</td>
          <td>{tool_on["combined_total_chars_est"]:,} chars est</td>
        </tr>
        <tr>
          <td><strong>search_only</strong></td>
          <td>{search_only["answer_prompt_tokens"]:,} / {search_only["answer_completion_tokens"]:,}</td>
          <td>{import_summary["import_llm_prompt_tokens"]:,} / {import_summary["import_llm_completion_tokens"]:,}</td>
          <td>{search_only["combined_prompt_tokens"]:,} / {search_only["combined_completion_tokens"]:,}</td>
          <td>{search_only["combined_total_chars_est"]:,} chars est</td>
        </tr>
        <tr>
          <td><strong>tool_off</strong></td>
          <td>{tool_off["answer_prompt_tokens"]:,} / {tool_off["answer_completion_tokens"]:,}</td>
          <td>{import_summary["import_llm_prompt_tokens"]:,} / {import_summary["import_llm_completion_tokens"]:,}</td>
          <td>{tool_off["combined_prompt_tokens"]:,} / {tool_off["combined_completion_tokens"]:,}</td>
          <td>{tool_off["combined_total_chars_est"]:,} chars est</td>
        </tr>
      </tbody>
    </table>
    <div class="callout">
      <strong>说明：</strong> 这里的字符数是按 <span class="mono">1 token ≈ 4 chars</span> 做的统一估算；QA 行里如果有真实字符字段，会另外保留在 summary.json 里。
    </div>
  </section>

  <section>
    <h2>6. 具体建议</h2>
    <ol>
      <li>主评测别关工具，保留 <code>search_only</code> 或受限工具集，但要卡住循环。</li>
      <li>把 <code>tool_off</code> 作为 lower-bound ablation，不要当主结果。</li>
      <li>如果要比后端，固定同一 agent、同一 judge、同一导入记忆、同一工具预算。</li>
      <li>后续再做同配置重复跑，看方差，才谈“稳定不稳定”。</li>
    </ol>
    <div class="callout">
      <strong>推荐的公平口径：</strong> 先比准确率，再比同边界 token；如果要比总成本，必须把 EchoMemory 和 OpenViking 的 token 边界统一。
    </div>
  </section>

  <section>
    <h2>7. 高耗费样本</h2>
    <table>
      <thead><tr><th>OpenViking search_only 高 token 题</th><th>QA tokens</th><th>tool calls</th><th>回答预览</th></tr></thead>
      <tbody>
        {render_outlier_rows(search_only["outliers"])}
      </tbody>
    </table>
  </section>

  <section>
    <h2>8. 原始路径</h2>
    <p class="path">OpenViking tool_on: <code>{esc(tool_on["summary_path"])}</code></p>
    <p class="path">OpenViking tool_off: <code>{esc(tool_off["summary_path"])}</code></p>
    <p class="path">OpenViking search_only: <code>{esc(search_only["summary_path"])}</code></p>
    <p class="path">EchoMemory report: <code>{esc(echomemory["report_path"])}</code></p>
    <p class="path">OpenViking import summary: <code>{esc(import_summary["path"])}</code></p>
  </section>
</main>
</body>
</html>
"""


def render_comparison_rows(openviking_runs: list[dict[str, Any]]) -> str:
    cells = []
    for run in openviking_runs:
        tag = "good" if run["accuracy"] >= 0.6 else "bad"
        status = f'<span class="tag {tag}">{pct(run["correct"], run["count"])}</span>'
        cells.append(
            f"""
            <tr>
              <td><strong>{esc(run["label"])}</strong><div class="small">{esc(run["tool_set"] or "-")} · loop={str(run["loop_enabled"]).lower()}</div></td>
              <td>{status}</td>
              <td>{run["answer_total_tokens"]:,}</td>
              <td>{run["tool_calls"]:,}</td>
              <td>
                <div class="small">model ok {run["healthy"]["model_ok"]}/81 · retrieval ok {run["healthy"]["retrieval_ok"]}/81</div>
                <div class="small">health ok {run["healthy"]["health_ok"]}/81 · errors {run["healthy"]["retrieval_errors"]}</div>
              </td>
            </tr>
            """
        )
    return "\n".join(cells)


def render_outlier_rows(rows: list[dict[str, str]]) -> str:
    out = []
    for row in rows:
        out.append(
            f"""
            <tr>
              <td>{esc(row.get("question_id") or "-")}</td>
              <td>{int0(row.get("answer_total_tokens")):,}</td>
              <td>{int0(row.get("tool_call_count")):,}</td>
              <td>{esc(compact(row.get("response") or row.get("answer") or "", 120))}</td>
            </tr>
            """
        )
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render OpenViking/EchoMemory conv-30 fairness report.")
    parser.add_argument(
        "--output",
        default=str(ROOT / "static" / "openviking_token_observability_design_20260615.html"),
        help="Primary output HTML path.",
    )
    parser.add_argument(
        "--publish-copy",
        default=str(ROOT / "web" / "static" / "generated-reports" / "openviking_token_observability_design_20260615.html"),
        help="Optional second copy for the web-generated reports directory.",
    )
    parser.add_argument(
        "--latest-copy",
        default=str(ROOT / "web" / "static" / "generated-reports" / "openviking_token_observability_latest.html"),
        help="Stable latest HTML path for browser/mobile access.",
    )
    parser.add_argument("--openviking-tool-on-summary", default=str(ROOT / "runs" / "openviking_v024_formal_conv30_fixed_20260616" / "openviking_qa" / "summary.json"))
    parser.add_argument("--openviking-tool-on-judge", default=str(ROOT / "runs" / "openviking_v024_formal_conv30_fixed_20260616" / "openviking_qa" / "judge_summary.json"))
    parser.add_argument("--openviking-tool-on-csv", default=str(ROOT / "runs" / "openviking_v024_formal_conv30_fixed_20260616" / "openviking_qa" / "openviking_memory_qa_results.csv"))
    parser.add_argument("--openviking-tool-off-summary", default=str(ROOT / "runs" / "openviking_v024_notool_full_20260616" / "summary.json"))
    parser.add_argument("--openviking-tool-off-judge", default=str(ROOT / "runs" / "openviking_v024_notool_full_20260616" / "judge_summary.json"))
    parser.add_argument("--openviking-tool-off-csv", default=str(ROOT / "runs" / "openviking_v024_notool_full_20260616" / "openviking_memory_qa_results.csv"))
    parser.add_argument("--openviking-search-only-summary", default=str(ROOT / "runs" / "openviking_v024_searchonly_full_20260616" / "summary.json"))
    parser.add_argument("--openviking-search-only-judge", default=str(ROOT / "runs" / "openviking_v024_searchonly_full_20260616" / "judge_summary.json"))
    parser.add_argument("--openviking-search-only-csv", default=str(ROOT / "runs" / "openviking_v024_searchonly_full_20260616" / "openviking_memory_qa_results.csv"))
    parser.add_argument("--openviking-import-summary", default=str(ROOT / "runs" / "openviking_v024_formal_import_20260616" / "openviking_import_summary.json"))
    parser.add_argument("--echomemory-report", default=str(ROOT / "runs" / "echomemory_v010_conv30_eval_20260615_123200" / "echomemory_v010_conv30_report.html"))
    parser.add_argument("--echomemory-run-dir", default=str(ROOT / "runs" / "echomemory_v010_conv30_eval_20260615_123200"))
    args = parser.parse_args()

    tool_on = load_openviking_run("tool_on", Path(args.openviking_tool_on_summary), Path(args.openviking_tool_on_judge), Path(args.openviking_tool_on_csv))
    tool_off = load_openviking_run("tool_off", Path(args.openviking_tool_off_summary), Path(args.openviking_tool_off_judge), Path(args.openviking_tool_off_csv))
    search_only = load_openviking_run("search_only", Path(args.openviking_search_only_summary), Path(args.openviking_search_only_judge), Path(args.openviking_search_only_csv))
    import_summary = load_import_summary(Path(args.openviking_import_summary))

    echomemory_run_dir = Path(args.echomemory_run_dir)
    echomemory_logs = parse_llm_logs(
        [
            echomemory_run_dir / "import.log",
            *sorted(echomemory_run_dir.glob("qa_shard*/qa.log")),
        ]
    )
    echomemory_judge = read_json(Path(args.echomemory_report).with_name("qa_merged") / "judge_summary.json")
    echomemory = {
        "count": int(echomemory_judge.get("count") or 0),
        "correct": int(echomemory_judge.get("correct") or 0),
        "accuracy": f'{int(echomemory_judge.get("correct") or 0)}/{int(echomemory_judge.get("count") or 0)} = {pct(int(echomemory_judge.get("correct") or 0), int(echomemory_judge.get("count") or 0))}',
        "input_tokens": int(echomemory_logs["input_tokens"]),
        "output_tokens": int(echomemory_logs["output_tokens"]),
        "total_tokens": int(echomemory_logs["total_tokens"]),
        "import_tokens": sum(int(v) for k, v in echomemory_logs["by_call_site"].items() if k != "search_intent"),
        "qa_tokens": int(echomemory_logs["by_call_site"].get("search_intent", 0)),
        "judge_model": str(echomemory_judge.get("judge_model") or ""),
        "report_path": str(args.echomemory_report),
    }
    openviking_runs = [tool_on, tool_off, search_only]
    generated_at = datetime.now().isoformat(timespec="seconds")
    html_text = render_html(openviking_runs, echomemory, import_summary, generated_at)

    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html_text, encoding="utf-8")

    publish_copy = Path(args.publish_copy).expanduser().resolve()
    publish_copy.parent.mkdir(parents=True, exist_ok=True)
    publish_copy.write_text(html_text, encoding="utf-8")

    latest_copy = Path(args.latest_copy).expanduser().resolve()
    latest_copy.parent.mkdir(parents=True, exist_ok=True)
    latest_copy.write_text(html_text, encoding="utf-8")

    print(output)
    print(publish_copy)
    print(latest_copy)


if __name__ == "__main__":
    main()
