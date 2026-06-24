#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import html
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
import importlib.util


ROOT = Path("/Users/chx/locomo-eval-web")
ECHO_RUN = ROOT / "runs/echomemory_generic_qa_20260622_183050_943ead/echomemory_generic_qa"
OV_RUN = ROOT / "runs/openviking_generic_qa_20260622_155611_b1b37f/openviking_generic_qa"
OUT = ROOT / "web/static/generated-reports/hotpotqa_echomemory_running_analysis_20260622.html"
OUT_MIRROR = ROOT / "static/generated-reports/hotpotqa_echomemory_running_analysis_20260622.html"
TASK_ID = "echomemory_generic_qa_20260622_183050_943ead"

# Snapshot at generation time; this report is intended to be refreshed periodically.
PROGRESS_CURRENT = 47
PROGRESS_TOTAL = 100
ELAPSED_SECONDS = 6238.0
ETA_SECONDS = 7035.2


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def short_text(text: str, limit: int = 220) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else f"{value[:limit - 1]}..."


def parse_memories(value: str) -> list[dict]:
    try:
        data = json.loads(value or "[]")
        return data if isinstance(data, list) else []
    except Exception:
        return []


def table_rows(rows: list[dict[str, str]]) -> str:
    body = []
    for row in rows:
        body.append(
            "<tr>"
            f"<td><code>{html.escape(row['sample_id'])}</code></td>"
            f"<td>{html.escape(row['question'])}</td>"
            f"<td>{html.escape(row['gold'])}</td>"
            f"<td>{html.escape(row['echo'])}</td>"
            f"<td>{html.escape(row['ov'])}</td>"
            "</tr>"
        )
    return "\n".join(body)


def main() -> None:
    spec = importlib.util.spec_from_file_location("hotpot_eval", ROOT / "scripts/hotpotqa_answer_eval.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    echo_rows = load_csv(ECHO_RUN / "echomemory_generic_qa_results.csv")
    ov_rows = load_csv(OV_RUN / "openviking_generic_qa_results.csv")
    echo_map = {row["sample_id"]: row for row in echo_rows}
    ov_map = {row["sample_id"]: row for row in ov_rows}
    shared = sorted(set(echo_map) & set(ov_map))

    partial_em = 0.0
    partial_f1 = 0.0
    unknown_count = 0
    final_counter = Counter()
    top1_counter = Counter()
    atom_top1_wrong: list[dict[str, str]] = []

    for row in echo_rows:
        prediction = row.get("response") or ""
        gold = row.get("answer") or ""
        partial_em += mod.exact_match(prediction, gold)
        partial_f1 += mod.f1_score(prediction, gold)
        if "i do not know" in prediction.lower():
            unknown_count += 1
        final_counter[row.get("final_evidence_source") or "none"] += 1

        rel = parse_memories(row.get("relevant_memory") or "")
        top = rel[0] if rel else {}
        top_uri = str(top.get("uri") or "")
        top_content = str(top.get("content") or "")
        top_type = "atom" if top_uri.startswith("atom://") else "segment" if "/segments/" in top_uri else "other"
        top1_counter[top_type] += 1
        f1 = mod.f1_score(prediction, gold)
        if top_type == "atom" and f1 == 0 and len(atom_top1_wrong) < 8:
            atom_top1_wrong.append(
                {
                    "sample_id": row["sample_id"],
                    "question": row["question"],
                    "gold": gold,
                    "response": prediction,
                    "top_uri": top_uri,
                    "top_snippet": short_text(top_content, 180),
                }
            )

    row_count = len(echo_rows) or 1
    partial_em /= row_count
    partial_f1 /= row_count

    ov_better: list[dict[str, str]] = []
    shared_echo_f1 = 0.0
    shared_ov_f1 = 0.0
    for sample_id in shared:
        echo_row = echo_map[sample_id]
        ov_row = ov_map[sample_id]
        gold = echo_row["answer"]
        echo_f1 = mod.f1_score(echo_row.get("response") or "", gold)
        ov_f1 = mod.f1_score(ov_row.get("response") or "", gold)
        shared_echo_f1 += echo_f1
        shared_ov_f1 += ov_f1
        if ov_f1 > echo_f1:
            ov_better.append(
                {
                    "sample_id": sample_id,
                    "question": echo_row["question"],
                    "gold": gold,
                    "echo": echo_row.get("response") or "",
                    "ov": ov_row.get("response") or "",
                }
            )

    shared_count = len(shared) or 1
    shared_echo_f1 /= shared_count
    shared_ov_f1 /= shared_count

    atom_case_html = "".join(
        (
            '<div class="case">'
            f"<h3>{html.escape(item['question'])}</h3>"
            f'<p><span class="muted">sample_id</span> <code>{html.escape(item["sample_id"])}</code> · '
            f'<span class="muted">Gold</span> <strong>{html.escape(item["gold"])}</strong></p>'
            f"<p><strong>回答：</strong> {html.escape(item['response'])}</p>"
            f"<p><strong>Top-1 URI：</strong> <code>{html.escape(item['top_uri'])}</code></p>"
            f'<div class="quote">{html.escape(item["top_snippet"])}</div>'
            "</div>"
        )
        for item in atom_top1_wrong
    )
    ov_better_html = table_rows(ov_better[:10])

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html_text = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>HotpotQA 运行中分析：为什么 EchoMemory 表现差</title>
<style>
:root{{--bg:#f5f6f8;--panel:#fff;--line:#d9dce3;--text:#16181d;--muted:#68707f;--blue:#1d4ed8;--green:#157f3b;--orange:#b4690e;--red:#b42318;--shadow:0 12px 28px rgba(16,24,40,.08);--radius:14px;}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Helvetica Neue",Arial,sans-serif;line-height:1.6}}
.wrap{{max-width:1180px;margin:0 auto;padding:24px 18px 60px}} .hero,.card{{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow)}} .hero{{padding:28px 24px;margin-bottom:18px}} .card{{padding:20px 18px;margin-bottom:16px}}
h1,h2,h3{{margin:0;letter-spacing:0}} h1{{font-size:30px;line-height:1.2;margin-bottom:10px}} h2{{font-size:22px;line-height:1.25;margin-bottom:14px}} h3{{font-size:17px;line-height:1.35;margin-bottom:10px}} p{{margin:0 0 12px}} .muted{{color:var(--muted)}}
.grid{{display:grid;gap:16px}} .grid.two{{grid-template-columns:repeat(2,minmax(0,1fr))}} .grid.three{{grid-template-columns:repeat(3,minmax(0,1fr))}} .stat-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}
.stat{{border:1px solid var(--line);border-radius:12px;padding:12px;background:#fbfbfc}} .label{{font-size:12px;color:var(--muted);margin-bottom:4px}} .value{{font-size:24px;line-height:1.2;font-weight:700}} .good{{color:var(--green)}} .warn{{color:var(--orange)}} .bad{{color:var(--red)}}
.tag-row{{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}} .tag{{border:1px solid var(--line);border-radius:999px;background:#fafbfd;color:var(--muted);padding:5px 10px;font-size:12px}}
.note{{padding:12px 14px;border-left:3px solid var(--blue);background:#f8fbff;color:var(--muted);border-radius:10px}} ul{{margin:0;padding-left:18px}} li{{margin:0 0 8px}} code{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:#eef2f6;border-radius:6px;padding:1px 5px;font-size:12px;word-break:break-all}}
table{{width:100%;border-collapse:collapse;font-size:14px}} th,td{{text-align:left;vertical-align:top;border-top:1px solid var(--line);padding:10px 8px}} th{{color:var(--muted);background:#fbfbfc;font-weight:650}}
.case{{border:1px solid var(--line);border-radius:12px;background:#fbfbfc;padding:14px;margin-top:14px}} .quote{{border-left:3px solid var(--line);padding-left:10px;color:var(--muted)}} .footer{{font-size:12px;color:var(--muted);margin-top:20px}}
a{{color:var(--blue);text-decoration:none}} @media (max-width:920px){{.grid.two,.grid.three,.stat-grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<div class="wrap">
<section class="hero">
  <h1>HotpotQA 运行中分析：为什么 EchoMemory 表现差</h1>
  <p>这是一份<strong>运行中</strong>的阶段性报告。当前并不是等 100 题全跑完才回看，而是边跑边观察：HotpotQA 的 answer-only 指标怎么计算、EchoMemory 目前为什么掉分、这些掉分更多来自检索污染、记忆注入策略，还是答案抽取本身。</p>
  <div class="tag-row">
    <span class="tag">任务: {html.escape(TASK_ID)}</span>
    <span class="tag">当前已落盘: {len(echo_rows)} 题</span>
    <span class="tag">OpenViking 对照: 30 题</span>
    <span class="tag">生成时间: {html.escape(generated_at)}</span>
  </div>
</section>
<section class="card">
  <h2>当前进度</h2>
  <div class="stat-grid">
    <div class="stat"><div class="label">运行进度</div><div class="value">{PROGRESS_CURRENT} / {PROGRESS_TOTAL}</div></div>
    <div class="stat"><div class="label">当前阶段</div><div class="value">QA</div></div>
    <div class="stat"><div class="label">已运行时长</div><div class="value">{ELAPSED_SECONDS/60:.1f}m</div></div>
    <div class="stat"><div class="label">当前 ETA</div><div class="value">{ETA_SECONDS/60:.1f}m</div></div>
  </div>
  <p class="note" style="margin-top:14px;">这份报告基于当前已写入 CSV 的题目做阶段性分析。它适合判断机制问题，不适合作为最终官方分数结论。最终 answer EM/F1 仍以任务跑完后的 <code>hotpotqa_answer_summary.json</code> 为准。</p>
</section>
<section class="card"><h2>这些指标怎么衡量</h2><div class="grid two"><div><h3>HotpotQA 官方 answer-only 指标</h3><ul><li><code>EM</code>：标准化后的预测答案与 gold 完全一致才记 1，否则记 0。</li><li><code>F1</code>：把标准化后的答案拆词，按 token overlap 计算精确率和召回率，再取调和平均。</li><li>这里的标准化会去掉大小写差异、标点，以及 <code>a/an/the</code> 这类冠词。</li><li>因此，简洁短答有时会比长解释句更占便宜。</li></ul></div><div><h3>运行时诊断指标</h3><ul><li><code>final_evidence_source</code>：最后被系统当成主证据的来源，常见是 <code>atom</code> 或 <code>segment_memory</code>。</li><li><code>relevant_memory</code> top-1：检索排序第一名的记忆对象。它能直接暴露有没有跨题污染。</li><li><code>retrieval_count</code> / <code>memory_hit_count</code>：看检索到底拿回了多少证据。</li><li><code>memory_injection_time_s</code> / <code>qa_time_s</code>：区分时间耗在记忆整理，还是耗在回答模型。</li></ul></div></div></section>
<section class="grid two"><div class="card"><h2>EchoMemory 当前阶段分数</h2><div class="stat-grid"><div class="stat"><div class="label">阶段性 EM</div><div class="value">{partial_em*100:.2f}%</div></div><div class="stat"><div class="label">阶段性 F1</div><div class="value warn">{partial_f1*100:.2f}%</div></div><div class="stat"><div class="label">I do not know</div><div class="value bad">{unknown_count}</div></div><div class="stat"><div class="label">已落盘题目</div><div class="value">{len(echo_rows)}</div></div></div><ul style="margin-top:14px;"><li>这不是最终 100 题总分，而是当前已经写进 CSV 的题目的即时 answer-only 分数。</li><li>到目前为止，<code>I do not know</code> 的比例依然很高，这是最直接的失分源。</li></ul></div><div class="card"><h2>与 OpenViking 的重合题对比</h2><div class="stat-grid"><div class="stat"><div class="label">重合题数</div><div class="value">{len(shared)}</div></div><div class="stat"><div class="label">EchoMemory F1</div><div class="value warn">{shared_echo_f1*100:.2f}%</div></div><div class="stat"><div class="label">OpenViking F1</div><div class="value good">{shared_ov_f1*100:.2f}%</div></div><div class="stat"><div class="label">OpenViking 更好</div><div class="value good">{len(ov_better)}</div></div></div><ul style="margin-top:14px;"><li>当前已重合的 30 题里，OpenViking 仍然显著领先。</li><li>这说明 EchoMemory 当前的差距不是“后面新题会自然抹平”的小波动，更像结构性问题。</li></ul></div></section>
<section class="card"><h2>为什么 EchoMemory 当前表现差</h2><div class="grid three"><div class="card" style="margin:0;"><h3>1. Top-1 证据经常不是当前题段落</h3><ul><li>当前已落盘题里，top-1 证据大量是 <code>atom</code>，不是当前样本 segment。</li><li>一旦 atom 排到最前面，后续回答模型就更容易被带偏。</li><li>这类问题本质上是排序优先级问题，不只是回答模型本身弱。</li></ul></div><div class="card" style="margin:0;"><h3>2. 跨题污染仍然存在</h3><ul><li>当前 CSV 里能直接看到，很多题的上下文预览前几条就是无关 atom。</li><li>这意味着系统在当前题局部证据和全局旧 atom 之间，没有把边界切干净。</li><li>HotpotQA 是按题隔离评测，这种全局混检会被放大成明显掉分。</li></ul></div><div class="card" style="margin:0;"><h3>3. 放弃作答太早</h3><ul><li><code>I do not know</code> 数量高，说明 answerability gate 偏保守。</li><li>很多题并不是完全没信息，而是看到了不够纯的证据后直接放弃。</li><li>这会同时拉低 EM 和 F1。</li></ul></div></div></section>
<section class="card"><h2>当前最值得盯的运行中指标</h2><div class="grid two"><div><h3>证据指标</h3><ul><li><code>final_evidence_source</code> 当前分布：<code>{html.escape(str(dict(final_counter)))}</code></li><li><code>top-1</code> 类型分布：<code>{html.escape(str(dict(top1_counter)))}</code></li><li>如果 <code>atom</code> 持续压过 <code>segment</code>，最终成绩大概率还会继续偏低。</li></ul></div><div><h3>结果指标</h3><ul><li>阶段性部分分数：<code>EM {partial_em:.4f}</code>，<code>F1 {partial_f1:.4f}</code></li><li>和 OpenViking 重合题比较：<code>Echo F1 {shared_echo_f1:.4f}</code> vs <code>OV F1 {shared_ov_f1:.4f}</code></li><li>如果后续跑完后仍维持这个差距，结论就很稳了。</li></ul></div></div></section>
<section class="card"><h2>跨题污染样例</h2><p class="muted">下面这些题的 top-1 是 atom，而且当前题 answer F1 直接为 0。这是最典型的坏信号。</p>{atom_case_html}</section>
<section class="card"><h2>OpenViking 更好的典型题</h2><table><thead><tr><th>sample_id</th><th>问题</th><th>Gold</th><th>EchoMemory</th><th>OpenViking</th></tr></thead><tbody>{ov_better_html}</tbody></table><p class="note" style="margin-top:14px;">这些题的共同点是：OpenViking 至少把题内文档信息稳定带到了答案阶段，而 EchoMemory 这边经常直接 <code>I do not know</code>，或者被错误 top-1 证据带歪。</p></section>
<section class="card"><h2>当前判断</h2><div class="grid two"><div><h3>更像什么问题</h3><ul><li>主问题更像<strong>检索排序与记忆边界控制</strong>，不是单纯回答模型差。</li><li>因为同一回答模型 <code>deepseek-v4-flash</code> 在 OpenViking 上并没有掉成这样。</li><li>所以根因更可能在“把什么证据送给模型”这一步。</li></ul></div><div><h3>怎么改更有效</h3><ul><li>HotpotQA 模式下优先启用 <code>sample-local retrieval</code>。</li><li>把当前 sample 的 segment 设成高于全局 atom 的排序优先级。</li><li>只有当前题局部证据不足时，再回退到全局 atom。</li></ul></div></div></section>
<section class="card"><h2>文件入口</h2><ul><li>当前运行目录：<a href="file://{ECHO_RUN.parent}">{html.escape(str(ECHO_RUN.parent))}</a></li><li>当前结果 CSV：<a href="file://{ECHO_RUN / 'echomemory_generic_qa_results.csv'}">{html.escape(str(ECHO_RUN / 'echomemory_generic_qa_results.csv'))}</a></li><li>OpenViking 对照 CSV：<a href="file://{OV_RUN / 'openviking_generic_qa_results.csv'}">{html.escape(str(OV_RUN / 'openviking_generic_qa_results.csv'))}</a></li><li>报告文件：<code>{html.escape(str(OUT))}</code></li></ul><div class="footer">这是一份阶段性 HTML。等 100 题跑完后，可以在这份基础上再出最终版完整报告。</div></section>
</div>
</body>
</html>"""

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html_text, encoding="utf-8")
    OUT_MIRROR.parent.mkdir(parents=True, exist_ok=True)
    OUT_MIRROR.write_text(html_text, encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
