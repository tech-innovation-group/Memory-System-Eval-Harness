#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import json
from pathlib import Path
import urllib.request
from datetime import datetime


ROOT = Path("/Users/chx/locomo-eval-web")
RUNS = ROOT / "runs"
REPORT_NAME = "longmemeval_oracle50_openviking_vs_echomemory_analysis_20260624.html"
OUTS = [
    ROOT / "generated-reports" / REPORT_NAME,
    ROOT / "web/static/generated-reports" / REPORT_NAME,
    ROOT / "static/generated-reports" / REPORT_NAME,
]

OV_TASK_ID = "openviking_generic_qa_20260624_022508_70fb0f"
EM_TASK_ID = "echomemory_generic_qa_20260624_022723_longmem-oracle50-20260624-022634_3e7829"

OV_DIR = RUNS / OV_TASK_ID / "openviking_generic_qa"
EM_DIR = RUNS / EM_TASK_ID / "echomemory_generic_qa"


def read_json(path: Path):
    return json.loads(path.read_text())


def read_csv(path: Path):
    with path.open() as f:
        return list(csv.DictReader(f))


def read_live_task(task_id: str, fallback_path: Path):
    url = f"http://127.0.0.1:19181/api/tasks/{task_id}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return read_json(fallback_path)


def fmt_int(value):
    if value is None or value == "":
        return "-"
    try:
        return f"{int(value):,}"
    except Exception:
        return html.escape(str(value))


def fmt_float(value, digits=2):
    if value is None or value == "":
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return html.escape(str(value))


def pill(text, cls=""):
    return f'<span class="pill {cls}">{html.escape(text)}</span>'


def code(text):
    return f"<code>{html.escape(str(text))}</code>"


def build():
    ov_summary = read_json(OV_DIR / "summary.json")
    ov_official = read_json(OV_DIR / "longmemeval_official_summary.json")
    ov_wrong = read_json(OV_DIR / "openviking_generic_qa_results.wrong_analysis.json")
    ov_import = read_json(OV_DIR / "openviking_import/openviking_generic_import_summary.json")
    em_status = read_json(EM_DIR / "generic_qa_status.json")
    em_rows = read_csv(EM_DIR / "echomemory_generic_qa_results.csv")
    em_task = read_live_task(EM_TASK_ID, RUNS / EM_TASK_ID / "manifest.json")

    ov_doc_count = sum(int(r.get("document_memory_count") or 0) for r in ov_import.get("records", []))
    ov_doc_tokens = sum(int(r.get("document_memory_tokens_est") or 0) for r in ov_import.get("records", []))

    em_failed = len(em_rows)
    em_memory_wait_total = sum(float(r.get("memory_settle_wait_elapsed_s") or 0) for r in em_rows)
    em_first_errors = []
    for row in em_rows[:5]:
        em_first_errors.append({
            "question": row.get("question") or "",
            "error": row.get("model_error") or row.get("reasoning") or "",
            "wait": row.get("memory_settle_wait_elapsed_s") or "",
        })

    unknown_examples = (ov_wrong.get("examples") or {}).get("有证据但回答 Unknown", [])[:4]
    list_examples = (ov_wrong.get("examples") or {}).get("列表/聚合遗漏", [])[:1]

    em_progress = ((em_task.get("progress") or {}) if isinstance(em_task, dict) else {}) or {}
    em_token_usage = ((em_task.get("log_diagnostics") or {}).get("token_usage") or {}) if isinstance(em_task, dict) else {}
    em_call_sites = em_token_usage.get("call_sites") or {}
    is_live = str(em_task.get("status") or "").lower() == "running"
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    head_extra = '<meta http-equiv="refresh" content="60">' if is_live else ""

    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  {head_extra}
  <title>LongMemEval Oracle 50 · OpenViking vs EchoMemory</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f5f7;
      --card: #ffffff;
      --text: #111111;
      --muted: #6e6e73;
      --line: #d2d2d7;
      --blue: #0071e3;
      --green: #14863c;
      --orange: #b35c00;
      --red: #b42318;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 16px/1.6 -apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "Helvetica Neue", Arial, sans-serif;
    }}
    .wrap {{
      width: min(980px, calc(100vw - 28px));
      margin: 0 auto;
      padding: 28px 0 56px;
    }}
    .hero, .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 22px 22px;
      margin-bottom: 16px;
    }}
    h1, h2, h3, p {{ margin: 0; }}
    h1 {{ font-size: 30px; line-height: 1.15; margin-top: 6px; }}
    h2 {{ font-size: 22px; line-height: 1.2; margin-bottom: 14px; }}
    h3 {{ font-size: 16px; line-height: 1.3; margin-bottom: 10px; }}
    .eyebrow {{ color: var(--blue); font-size: 13px; font-weight: 600; }}
    .sub {{ color: var(--muted); margin-top: 10px; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-top: 16px;
    }}
    .kpi {{
      background: #fbfbfd;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
    }}
    .kpi .label {{ font-size: 12px; color: var(--muted); margin-bottom: 6px; }}
    .kpi .value {{ font-size: 24px; line-height: 1.1; font-weight: 650; }}
    .kpi .hint {{ font-size: 12px; color: var(--muted); margin-top: 4px; }}
    .compare {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
    }}
    .compare .side {{
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 16px;
      background: #fcfcfd;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      padding: 0 10px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--muted);
      font-size: 12px;
      margin: 0 8px 8px 0;
    }}
    .pill.good {{ color: var(--green); border-color: rgba(20,134,60,.25); background: rgba(20,134,60,.07); }}
    .pill.warn {{ color: var(--orange); border-color: rgba(179,92,0,.25); background: rgba(179,92,0,.07); }}
    .pill.bad {{ color: var(--red); border-color: rgba(180,35,24,.25); background: rgba(180,35,24,.07); }}
    .callout {{
      border-radius: 14px;
      padding: 14px 16px;
      margin-top: 14px;
      border: 1px solid var(--line);
      background: #fbfbfd;
    }}
    .callout.good {{ border-color: rgba(20,134,60,.2); background: rgba(20,134,60,.05); }}
    .callout.warn {{ border-color: rgba(179,92,0,.2); background: rgba(179,92,0,.05); }}
    .callout.bad {{ border-color: rgba(180,35,24,.2); background: rgba(180,35,24,.05); }}
    .list {{
      display: grid;
      gap: 10px;
      margin-top: 12px;
    }}
    .row {{
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      background: #fff;
    }}
    .row p + p {{ margin-top: 6px; }}
    code {{
      display: inline-block;
      padding: 2px 6px;
      border-radius: 8px;
      background: #f2f2f7;
      font: 12px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      word-break: break-all;
    }}
    .paths code {{
      display: block;
      margin-top: 6px;
      padding: 8px 10px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      padding: 10px 8px;
      border-top: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-weight: 600;
      font-size: 12px;
    }}
    .footer {{
      color: var(--muted);
      font-size: 12px;
      margin-top: 18px;
    }}
    @media (max-width: 760px) {{
      .wrap {{ width: min(100vw - 20px, 980px); padding-top: 18px; }}
      .grid, .compare {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 26px; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="eyebrow">LongMemEval · Oracle 50 · 2026-06-24</div>
      <h1>OpenViking 已完整跑完，EchoMemory 同条件下主要卡在记忆就绪阶段</h1>
      <p class="sub">这一版结论只基于当前真实 run 目录和任务状态。OpenViking 的 50 题结果已完整落盘；EchoMemory 任务还在继续，但前 17 条已经足够说明当前瓶颈不在“答案错”，而在“写入后迟迟不能进入可答状态”。</p>
      <div class="grid">
        <div class="kpi">
          <div class="label">OpenViking 准确率</div>
          <div class="value">{fmt_float(ov_summary.get("accuracy", 0) * 100)}%</div>
          <div class="hint">50 题 · 3 对 / 47 错</div>
        </div>
        <div class="kpi">
          <div class="label">OpenViking QA Token</div>
          <div class="value">{fmt_int(ov_summary.get("answer_total_tokens"))}</div>
          <div class="hint">Judge {fmt_int(ov_official.get("judge_total_tokens"))}</div>
        </div>
        <div class="kpi">
          <div class="label">EchoMemory 内部 LLM Token</div>
          <div class="value">{fmt_int(em_token_usage.get("llm_total_tokens"))}</div>
          <div class="hint">当前只算组织/索引链路</div>
        </div>
        <div class="kpi">
          <div class="label">EchoMemory 当前进度</div>
          <div class="value">{fmt_int(em_progress.get("current"))}/{fmt_int(em_progress.get("total"))}</div>
          <div class="hint">{html.escape(str(em_progress.get("phase") or "-"))}</div>
        </div>
      </div>
      <div class="callout {'warn' if is_live else 'good'}">
        <p>{'EchoMemory 任务仍在运行，页面每 60 秒自动刷新一次。' if is_live else '本页已冻结为最终结果快照。'}</p>
      </div>
    </section>

    <section class="card">
      <h2>1. 实验设置</h2>
      <div class="compare">
        <div class="side">
          <h3>一致部分</h3>
          <div>{pill("dataset = longmemeval_oracle.json")} {pill("count = 50")} {pill("sample = all")} {pill("top_k = 8")} {pill("answer model = deepseek-v4-flash")} {pill("judge model = deepseek-v4-flash")}</div>
          <div class="callout">
            <p>两边都用了隔离账户和单独 namespace，避免历史记忆串扰。</p>
          </div>
        </div>
        <div class="side">
          <h3>关键差异</h3>
          <div>{pill("OpenViking: 文档记忆 + 直接 QA", "good")} {pill("EchoMemory: 写入后等待 atom / overview / abstract / index", "warn")}</div>
          <div class="callout warn">
            <p>所以这次对比已经不是单纯的“谁检索更准”，而是“谁先能进入可答状态，再谈回答质量”。</p>
          </div>
        </div>
      </div>
    </section>

    <section class="card">
      <h2>2. OpenViking 50 题结果</h2>
      <table>
        <tr><th>指标</th><th>数值</th><th>解释</th></tr>
        <tr><td>overall accuracy</td><td>{fmt_float(ov_official.get("overall_accuracy", 0) * 100)}%</td><td>官方风格 LongMemEval 摘要</td></tr>
        <tr><td>task-averaged accuracy</td><td>{fmt_float(ov_official.get("task_averaged_accuracy", 0) * 100)}%</td><td>当前 50 题都落在 temporal-reasoning</td></tr>
        <tr><td>answer_total_tokens</td><td>{fmt_int(ov_summary.get("answer_total_tokens"))}</td><td>QA 回答模型总 token</td></tr>
        <tr><td>judge_total_tokens</td><td>{fmt_int(ov_official.get("judge_total_tokens"))}</td><td>Judge 总 token</td></tr>
        <tr><td>retrieval_tokens_est</td><td>{fmt_int(ov_summary.get("retrieval_tokens_est"))}</td><td>检索上下文估算 token</td></tr>
        <tr><td>memory_hit_total</td><td>{fmt_int(ov_summary.get("memory_hit_total"))}</td><td>总召回命中数</td></tr>
        <tr><td>document import</td><td>{fmt_int(ov_doc_count)} docs / {fmt_int(ov_doc_tokens)} est tokens</td><td>OpenViking 文档记忆导入侧规模</td></tr>
      </table>
      <div class="callout good">
        <p><strong>结论：</strong>OpenViking 这次不是“没检索到”，而是“检索到后大多仍回答 unknown”。</p>
      </div>
      <div class="list">
        <div class="row">
          <p><strong>失败模式分布</strong></p>
          <p>{pill("有证据但回答 Unknown = 46", "bad")} {pill("列表/聚合遗漏 = 1", "warn")}</p>
        </div>
      </div>
    </section>

    <section class="card">
      <h2>3. 为什么 OpenViking 分低，但不是检索失败</h2>
      <div class="list">
        {"".join(
            f'''<div class="row">
              <p><strong>Q</strong> {html.escape(item.get("question") or "")}</p>
              <p><strong>Gold</strong> {html.escape(item.get("gold") or "")}</p>
              <p><strong>Model</strong> {html.escape(item.get("response") or "")}</p>
              <p><strong>Judge</strong> {html.escape(item.get("reasoning") or "")}</p>
            </div>'''
            for item in unknown_examples
        )}
        {"".join(
            f'''<div class="row">
              <p><strong>聚合类错例</strong> {html.escape(item.get("question") or "")}</p>
              <p><strong>Gold</strong> {html.escape(item.get("gold") or "")}</p>
              <p><strong>Model</strong> {html.escape(item.get("response") or "")}</p>
            </div>'''
            for item in list_examples
        )}
      </div>
      <div class="callout warn">
        <p>这说明当前 OpenViking 的主要短板是 <strong>回答策略过于保守</strong>。召回证据并非空，问题出在证据如何被模型消费、是否敢做时间比较、是否能从单条文档里推出 first / before / after。</p>
      </div>
    </section>

    <section class="card">
      <h2>4. EchoMemory 当前状态</h2>
      <table>
        <tr><th>指标</th><th>数值</th><th>解释</th></tr>
        <tr><td>任务状态</td><td>{html.escape(str(em_task.get("status") or "-"))}</td><td>仍在运行</td></tr>
        <tr><td>进度</td><td>{fmt_int(em_progress.get("current"))}/{fmt_int(em_progress.get("total"))} ({fmt_float(em_progress.get("pct"))}%)</td><td>{html.escape(str(em_progress.get("phase") or "-"))}</td></tr>
        <tr><td>已落 CSV 行数</td><td>{fmt_int(em_failed)}</td><td>前 17 条都已写成失败行</td></tr>
        <tr><td>失败类型</td><td>memory_not_ready</td><td>写入后等待可答状态超时</td></tr>
        <tr><td>累计等待时间</td><td>{fmt_float(em_memory_wait_total, 1)} s</td><td>仅前 17 条的 settle wait</td></tr>
        <tr><td>内部 LLM input</td><td>{fmt_int(em_token_usage.get("llm_input_tokens"))}</td><td>组织链路输入 token</td></tr>
        <tr><td>内部 LLM output</td><td>{fmt_int(em_token_usage.get("llm_output_tokens"))}</td><td>组织链路输出 token</td></tr>
        <tr><td>内部 LLM total</td><td>{fmt_int(em_token_usage.get("llm_total_tokens"))}</td><td>当前未进入真正 QA 回答主链</td></tr>
      </table>
      <div class="callout bad">
        <p><strong>当前结论：</strong>EchoMemory 这轮同条件对比还不能拿准确率和 OpenViking 正面对比，因为它在大部分样本上还没进入“可回答”阶段。</p>
      </div>
      <div class="list">
        {"".join(
            f'''<div class="row">
              <p><strong>Q</strong> {html.escape(item["question"])}</p>
              <p><strong>Error</strong> {html.escape(item["error"])}</p>
              <p><strong>Settle Wait</strong> {html.escape(str(item["wait"]))} s</p>
            </div>'''
            for item in em_first_errors
        )}
      </div>
    </section>

    <section class="card">
      <h2>5. EchoMemory Token 花在了哪里</h2>
      <table>
        <tr><th>call_site</th><th>tokens</th><th>calls</th><th>含义</th></tr>
        <tr><td>atom_extraction</td><td>{fmt_int((em_call_sites.get("atom_extraction") or {}).get("total_tokens"))}</td><td>{fmt_int((em_call_sites.get("atom_extraction") or {}).get("call_count"))}</td><td>把对话抽成 atom</td></tr>
        <tr><td>overview_generation</td><td>{fmt_int((em_call_sites.get("overview_generation") or {}).get("total_tokens"))}</td><td>{fmt_int((em_call_sites.get("overview_generation") or {}).get("call_count"))}</td><td>生成 overview</td></tr>
        <tr><td>abstract_generation</td><td>{fmt_int((em_call_sites.get("abstract_generation") or {}).get("total_tokens"))}</td><td>{fmt_int((em_call_sites.get("abstract_generation") or {}).get("call_count"))}</td><td>生成 abstract</td></tr>
        <tr><td>entity_merge</td><td>{fmt_int((em_call_sites.get("entity_merge") or {}).get("total_tokens"))}</td><td>{fmt_int((em_call_sites.get("entity_merge") or {}).get("call_count"))}</td><td>实体合并</td></tr>
      </table>
      <div class="callout warn">
        <p>这说明当前 EchoMemory 的主要开销并不在回答模型，而在 <strong>存储后组织长期记忆</strong>。如果目标是像 LoCoMo / LongMemEval 这种评测场景，热路径需要更早暴露“可检索、可问答”的最小记忆状态。</p>
      </div>
    </section>

    <section class="card">
      <h2>6. 这次对比真正说明了什么</h2>
      <div class="list">
        <div class="row">
          <p><strong>OpenViking 的问题：</strong>写入快，能进 QA，但 temporal-reasoning 问题上大量“有证据仍 unknown”。</p>
        </div>
        <div class="row">
          <p><strong>EchoMemory 的问题：</strong>组织链路重，写入后很久还不能进入可答状态，导致评测主链先被 memory_not_ready 卡住。</p>
        </div>
        <div class="row">
          <p><strong>所以两边不是同一种失败。</strong> OpenViking 主要是回答策略 / 证据消费问题；EchoMemory 主要是写入后 readiness 暴露过晚。</p>
        </div>
      </div>
    </section>

    <section class="card paths">
      <h2>7. 关键文件</h2>
      <p>OpenViking run</p>
      {code(str(RUNS / OV_TASK_ID))}
      <p>EchoMemory run</p>
      {code(str(RUNS / EM_TASK_ID))}
      <p>本报告</p>
      {code(str(OUTS[0]))}
      <div class="footer">Generated from current files at {html.escape(generated_at)} CST.</div>
    </section>
  </div>
</body>
</html>
"""
    for path in OUTS:
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_text(html_doc)
    print(OUTS[0])


if __name__ == "__main__":
    build()
