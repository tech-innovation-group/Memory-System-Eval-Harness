#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("/Users/chx/locomo-eval-web")
HTML_PATH = ROOT / "runs" / "echomemory_v010_search_intent_rule_ablation_20260617.html"
RUNS = [
    ROOT / "runs" / "echomemory_v010_subset20_trigger_windowbudget50_ruleintent_aligned_20260617",
    ROOT / "runs" / "echomemory_v010_subset20_trigger_windowbudget75_ruleintent_aligned_20260617",
    ROOT / "runs" / "echomemory_v010_subset20_trigger_windowbudget100_ruleintent_aligned_20260617",
]
START = "    <!-- trigger-windowbudget-section:start -->"
END = "    <!-- trigger-windowbudget-section:end -->"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def num(v: object) -> str:
    try:
        return f"{int(v):,}"
    except Exception:
        return str(v)


def build_row(run_dir: Path) -> dict:
    data = load_json(run_dir / "trigger_windowbudget_summary.json")
    import_usage = dict(data.get("import_token_usage") or {})
    import_log_usage = dict(data.get("import_log_usage") or {})
    import_sites = dict(import_log_usage.get("by_call_site") or {}) or dict(import_usage.get("call_sites") or {})
    qa = dict(data.get("qa_summary") or {})
    judge = dict(data.get("judge_summary") or {})
    return {
        "label": str(data.get("label") or run_dir.name),
        "window_turns": int(data.get("pending_window_turns") or 0),
        "pending_tokens": int(data.get("pending_token_threshold") or 0),
        "judge_correct": int(judge.get("correct") or 0),
        "judge_count": int(judge.get("count") or qa.get("count") or 0),
        "import_total": int(import_log_usage.get("total_tokens") or import_usage.get("import_total_tokens") or 0),
        "import_llm_total": int(import_log_usage.get("total_tokens") or import_usage.get("import_llm_total_tokens") or 0),
        "import_embedding_total": int(import_usage.get("import_embedding_total_tokens") or 0),
        "atom_extraction_total": int((import_sites.get("atom_extraction") or {}).get("total") or (import_sites.get("atom_extraction") or {}).get("total_tokens") or 0),
        "atom_extraction_calls": int((import_sites.get("atom_extraction") or {}).get("calls") or (import_sites.get("atom_extraction") or {}).get("call_count") or 0),
        "answer_total_tokens": int(qa.get("answer_total_tokens") or 0),
        "search_intent_total_tokens": int(qa.get("search_intent_total_tokens") or 0),
        "retrieval_tokens_est_total": int(qa.get("retrieval_tokens_est_total") or 0),
        "avg_retrieval_count": float(qa.get("avg_retrieval_count") or 0),
        "retrieval_empty_count": int(qa.get("retrieval_empty_count") or 0),
        "combined_visible": int(import_usage.get("import_total_tokens") or 0) + int(qa.get("answer_total_tokens") or 0) + int(qa.get("search_intent_total_tokens") or 0),
        "run_dir": run_dir,
    }


def build_section(rows: list[dict]) -> str:
    best = max(rows, key=lambda row: (row["judge_correct"], -row["combined_visible"]))
    row_html = "\n".join(
        f"""        <tr>
          <td>{row['label']}</td>
          <td><code>{row['window_turns']}</code> / <code>{row['pending_tokens']}</code></td>
          <td>{row['judge_correct']} / {row['judge_count']}</td>
          <td>{num(row['import_total'])}</td>
          <td>{num(row['import_llm_total'])}</td>
          <td>{num(row['import_embedding_total'])}</td>
          <td>{num(row['atom_extraction_total'])} / {num(row['atom_extraction_calls'])} calls</td>
          <td>{num(row['answer_total_tokens'])}</td>
          <td>{num(row['search_intent_total_tokens'])}</td>
          <td>{num(row['combined_visible'])}</td>
          <td>{num(row['retrieval_tokens_est_total'])}</td>
          <td>{row['avg_retrieval_count']}</td>
          <td>{row['retrieval_empty_count']}</td>
        </tr>"""
        for row in rows
    )
    return f"""
{START}
    <h2>后续机制实验：触发条件改为 pending token + window</h2>
    <p>这轮不再用固定 <code>flush 条数</code> 触发提取，而是把 <code>message.persisted</code> 的触发条件改成：<strong>pending turns 达到窗口阈值，或 pending estimated tokens 达到预算阈值</strong> 就立刻跑 atom extraction；不足阈值的尾部消息仍由 idle timer 收尾。代码现在走的是 adapter 自己维护的 pending 计数，不再依赖固定 <code>_FLUSH_THRESHOLD</code>。这里的 <code>flush</code> 只是内部把 pending 批次送进提取管线的动作，不是额外的对外配置轴。</p>
    <p>三组阈值都基于同一条 QA 链路：<code>rule-only search intent + score_threshold=0.75 + vikingboat_lite + no tool loop + no initial prefetch</code>。这里只对比真实导入 token 与 QA token，不混估算账。</p>
    <table>
      <thead>
        <tr>
          <th>配置</th>
          <th>触发阈值<br><code>window / pending_tokens</code></th>
          <th>Judge 准确率</th>
          <th>导入总 tokens</th>
          <th>导入 LLM</th>
          <th>导入 embedding</th>
          <th>atom_extraction</th>
          <th>QA answer tokens</th>
          <th>QA search_intent</th>
          <th>可见总计</th>
          <th>retrieval_tokens_est_total</th>
          <th>avg_retrieval_count</th>
          <th>retrieval_empty_count</th>
        </tr>
      </thead>
      <tbody>
{row_html}
      </tbody>
    </table>
    <ul>
      <li>当前三组里，准确率最高的是 <code>{best['label']}</code>：<code>{best['judge_correct']}/{best['judge_count']}</code>，可见总计 <code>{num(best['combined_visible'])}</code>。</li>
      <li>这里的“导入总 tokens”来自 workspace 下真实 <code>metrics/llm_tokens/*.jsonl</code> 聚合，已经包含 <code>embedding</code>；“QA answer tokens”来自 <code>echomemory_memory_qa.py</code> 的 <code>summary.json</code>；<code>search_intent</code> 单独列出方便确认 rule-only 是否真的把这项压成 0。</li>
      <li>导入脚本里仍能看到 <code>flush-call-timeout-s</code>、<code>flush-attempts</code> 这类参数，但它们只负责等待异步提取完成和失败重试，不参与“何时触发提取”的判定。</li>
      <li>如果某一档阈值把 <code>atom_extraction</code> 压低了，但 QA 侧 <code>answer_total_tokens</code> 或 judge 明显变差，就说明它只是把导入阶段的重复提取换成了后续 recall/answer 的补偿成本，不算真正更优。</li>
    </ul>
{END}
""".strip("\n")


def main() -> None:
    rows = [build_row(run_dir) for run_dir in RUNS if (run_dir / "trigger_windowbudget_summary.json").exists()]
    if len(rows) != len(RUNS):
        missing = [str(run_dir) for run_dir in RUNS if not (run_dir / "trigger_windowbudget_summary.json").exists()]
        raise SystemExit(f"missing summaries: {missing}")
    html = HTML_PATH.read_text(encoding="utf-8")
    section = build_section(rows)
    if START in html and END in html:
        start_idx = html.index(START)
        end_idx = html.index(END) + len(END)
        html = html[:start_idx] + section + html[end_idx:]
    else:
        anchor = "    <h2>相关路径</h2>"
        if anchor not in html:
            raise SystemExit("anchor not found")
        html = html.replace(anchor, section + "\n\n" + anchor, 1)
    HTML_PATH.write_text(html, encoding="utf-8")
    print(HTML_PATH)


if __name__ == "__main__":
    main()
