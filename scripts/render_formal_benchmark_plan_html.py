#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATUS_PATH = ROOT / "runs" / "formal_benchmark_status.json"
SOTA_PATH = ROOT / "dataset" / "sota_registry.json"
OUT_PATH = ROOT / "web" / "static" / "formal-benchmark-plan-20260606.html"
LEGACY_OUT_PATH = ROOT / "static" / "formal-benchmark-plan-20260606.html"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def pct(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{float(value) * 100:.2f}%"
    except Exception:
        return esc(value)


def sota_entries() -> dict[str, dict[str, Any]]:
    data = read_json(SOTA_PATH) if SOTA_PATH.exists() else {}
    return {item.get("dataset_format"): item for item in data.get("benchmarks", []) if item.get("dataset_format")}


def status_label(status: str) -> str:
    if status == "completed":
        return '<span class="pill good">完成</span>'
    if status in {"running", "running_or_partial", "prepared_or_running", "supervised_running"}:
        return '<span class="pill warn">运行中</span>'
    if status == "not_started":
        return '<span class="pill">未开始</span>'
    return f'<span class="pill">{esc(status)}</span>'


def tau2_active_note(item: dict[str, Any]) -> str:
    if not item.get("active_pids"):
        return ""
    progress = item.get("log_progress") or {}
    active = progress.get("active_running") or []
    if not active:
        return ""
    parts = []
    for entry in active:
        seconds = entry.get("elapsed_seconds")
        if isinstance(seconds, (int, float)):
            elapsed = f"{int(seconds)}s"
        else:
            elapsed = "-"
        retry = entry.get("retry") or ""
        suffix = f" {retry}" if retry else ""
        parts.append(f"{entry.get('task_id', '-')}: {elapsed}{suffix}")
    note = "running " + ", ".join(parts)
    stall = item.get("stall_warning") or {}
    if stall.get("status") == "timeout_exceeded":
        note += f"; timeout exceeded, result age {stall.get('result_seconds_since_mtime', '-')}s"
    return note


def render() -> str:
    status = read_json(STATUS_PATH)
    sota = sota_entries()
    longmem = status["runs"]["longmemeval"]
    longmem_off = longmem.get("official_summary") or {}
    hotpot = status["runs"]["hotpotqa"]
    tau = status["tau2bench"]
    tau_total = tau.get("formal_total") or {}
    tau_domains = tau.get("formal_by_domain") or {}
    tau_supervisor = tau.get("supervisor") or {}
    tau_current_scope = tau.get("current_scope") or "4 official domains, 1 trial each; banking_knowledge uses bm25 retrieval"
    tau_leaderboard_scope = tau.get("leaderboard_scope") or "full leaderboard requirements pending"
    updated = status.get("updated_at") or datetime.now().isoformat(timespec="seconds")
    longmem_score = longmem_off.get("overall_accuracy") or longmem.get("summary", {}).get("official_score")
    hotpot_rows = f"{hotpot.get('rows', 0)}/{hotpot.get('expected_rows', '-')}"
    tau_rows = f"{tau_total.get('evaluated_simulations', 0)}/{tau_total.get('expected_simulations', '-')}"
    tau_sim_rows = f"{tau_total.get('simulation_count', 0)}/{tau_total.get('expected_simulations', '-')}"
    tau_score = tau_total.get("pass_hat_1")
    longmem_sota = (sota.get("longmemeval") or {}).get("sota") or {}
    hotpot_sota = (sota.get("hotpotqa") or {}).get("sota") or {}
    proagent_sota = (sota.get("proagentbench") or {}).get("sota") or {}
    evolving_sota = (sota.get("evolvingevents") or {}).get("sota") or {}

    tau_domain_rows = []
    for domain in tau.get("base_domains", ["airline", "retail", "telecom", "banking_knowledge"]):
        item = tau_domains.get(domain) or {}
        tau_domain_rows.append(
            f"""
            <tr>
              <td>{esc(domain)}</td>
              <td>{status_label(str(item.get('status') or 'not_started'))}</td>
              <td>{esc(item.get('simulation_count', 0))}/{esc(item.get('expected_simulations', '-'))}</td>
              <td>{esc(item.get('evaluated_simulations', 0))}/{esc(item.get('expected_simulations', '-'))}</td>
              <td>{esc(item.get('infra_error_count', 0))}</td>
              <td>{pct(item.get('pass_hat_1'))}</td>
              <td>{pct(item.get('avg_reward'))}</td>
              <td>{esc(item.get('last_task_id') or '-')}<br><small>PID {esc(','.join(str(pid) for pid in item.get('active_pids', [])) or '-')}</small>{f"<br><small>{esc(tau2_active_note(item))}</small>" if tau2_active_note(item) else ""}</td>
              <td><code>{esc(item.get('result_path') or '-')}</code></td>
            </tr>
            """
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>其它数据集正式评测方案与 SOTA 对比</title>
  <style>
    :root {{ color-scheme: light; --ink:#17202a; --muted:#617080; --line:#d8e0e8; --panel:#fff; --band:#f5f8fb; --good:#0f7a55; --warn:#a15c02; --bad:#a73737; --blue:#235b98; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:var(--ink); background:var(--band); line-height:1.55; }}
    header {{ padding:34px 40px 26px; background:#0d2236; color:#fff; }}
    main {{ padding:26px 40px 44px; max-width:1320px; margin:0 auto; }}
    h1 {{ margin:0 0 8px; font-size:30px; letter-spacing:0; }}
    h2 {{ margin:28px 0 12px; font-size:21px; }}
    h3 {{ margin:18px 0 8px; font-size:16px; }}
    p {{ margin:8px 0; }}
    a {{ color:var(--blue); }}
    code {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:12px; }}
    .sub {{ color:#d7e4ef; max-width:980px; }}
    .stamp {{ color:#a9c2d8; font-size:13px; }}
    .grid {{ display:grid; gap:14px; }}
    .kpis {{ grid-template-columns:repeat(4,minmax(0,1fr)); }}
    .card,.panel {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; }}
    .card span {{ display:block; color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
    .card strong {{ display:block; margin-top:5px; font-size:24px; }}
    .card small {{ display:block; margin-top:4px; color:var(--muted); }}
    .good {{ color:var(--good); }} .warn {{ color:var(--warn); }} .bad {{ color:var(--bad); }} .muted {{ color:var(--muted); }}
    table {{ width:100%; border-collapse:collapse; background:var(--panel); border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
    th,td {{ padding:10px 11px; border-bottom:1px solid var(--line); vertical-align:top; text-align:left; font-size:14px; }}
    th {{ background:#eef3f8; color:#304456; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
    tr:last-child td {{ border-bottom:0; }}
    .pill {{ display:inline-block; padding:2px 8px; border-radius:999px; border:1px solid var(--line); background:#f7fafc; font-size:12px; color:#33485a; white-space:nowrap; }}
    .pill.good {{ color:var(--good); border-color:#b8dcca; background:#f0faf5; }}
    .pill.warn {{ color:var(--warn); border-color:#e8c991; background:#fff8ec; }}
    .pill.bad {{ color:var(--bad); border-color:#e2b8b8; background:#fff3f3; }}
    .note {{ border-left:4px solid var(--blue); padding:10px 12px; background:#eef6ff; border-radius:6px; }}
    .steps {{ counter-reset:step; }}
    .step {{ position:relative; padding-left:36px; margin:13px 0; }}
    .step::before {{ counter-increment:step; content:counter(step); position:absolute; left:0; top:0; width:24px; height:24px; border-radius:50%; background:#1e5a93; color:#fff; display:grid; place-items:center; font-size:13px; font-weight:700; }}
    .path {{ display:block; margin-top:4px; word-break:break-all; color:#405468; }}
    @media (max-width:900px) {{ header,main {{ padding-left:18px; padding-right:18px; }} .kpis {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} table {{ display:block; overflow-x:auto; }} }}
  </style>
</head>
<body>
  <header>
    <p class="stamp">状态时间：{esc(updated)} · 页面生成：{esc(datetime.now().isoformat(timespec="seconds"))}</p>
    <h1>其它数据集正式评测方案与 SOTA 对比</h1>
    <p class="sub">目标：对 LoCoMo 之外的数据集跑完整 MemoryBench 分数，必须真实调用大模型；官方原榜单指标和 SOTA 对比只在 runner/scorer 对齐时标注。样例、小样本核验、probe 和未完成长跑不冒充完整数据成绩。</p>
  </header>
  <main>
    <section class="grid kpis">
      <article class="card"><span>LongMemEval-S</span><strong class="good">{pct(longmem_score)}</strong><small>{esc(longmem.get('rows'))}/{esc(longmem.get('expected_rows'))} 完成 · overall_accuracy</small></article>
      <article class="card"><span>HotpotQA</span><strong class="warn">{esc(hotpot_rows)}</strong><small>全量 OpenViking + gpt-5.5 正在跑 · {esc(hotpot.get('progress_pct'))}%</small></article>
      <article class="card"><span>tau2-bench</span><strong class="warn">{esc(tau_rows)}</strong><small>可计分 evaluated / expected；总 simulations {esc(tau_sim_rows)}，infra errors {esc(tau_total.get('infra_error_count', 0))} · {esc(tau_current_scope)} · 中间 Pass^1 {pct(tau_score)}，不是最终 leaderboard 分数 · supervisor PID {esc(','.join(str(pid) for pid in tau_supervisor.get('active_pids', [])) or '-')}</small></article>
      <article class="card"><span>官方指标待补</span><strong>2 个</strong><small>EvolvingEvents / proAgentBench 可跑 MemoryBench QA，但缺官方 runner/scorer 对齐</small></article>
    </section>
    <section class="panel">
      <h2>判定原则</h2>
      <p class="note">正式分数必须同时满足：完整数据集或官方 split 跑完、真实 LLM 调用有日志与结果文件、严格失败为 0 或已解释、计分器与 SOTA 口径一致。否则只报告为“运行中”或“不可比”。</p>
      <table>
        <thead><tr><th>数据集</th><th>正式 runner / 链路</th><th>当前状态</th><th>正式指标</th><th>SOTA 对比口径</th></tr></thead>
        <tbody>
          <tr><td><strong>LongMemEval-S</strong></td><td>OpenViking document memory + gpt-5.5 answer + gpt-5.5 judge</td><td>{status_label(longmem.get('status'))} {esc(longmem.get('rows'))}/{esc(longmem.get('expected_rows'))}，strict failures {esc(longmem.get('failed_rows'))}</td><td>overall_accuracy = {esc(longmem_off.get('overall_accuracy'))}；task_averaged_accuracy = {esc(longmem_off.get('task_averaged_accuracy'))}</td><td>只能谨慎对照 paper reference/oracle-style baseline；不同 judge/model 与 runner 时必须标注。</td></tr>
          <tr><td><strong>HotpotQA distractor dev</strong></td><td>OpenViking document memory + gpt-5.5 answer + gpt-5.5 judge；完成后自动 answer EM/F1</td><td>{status_label(hotpot.get('status'))} {esc(hotpot_rows)}，strict failures {esc(hotpot.get('failed_rows'))}</td><td>本地当前只支持 answer EM/F1；supporting fact / joint F1 尚未生成</td><td>官方 leaderboard 主口径是 joint F1；answer-only 不可直接声称击败官方榜。</td></tr>
          <tr><td><strong>tau2-bench base</strong></td><td>官方 <code>tau2 run</code>；agent/user simulator 都使用 gpt-5.5，经 LiteLLM 调 OpenAI-compatible endpoint</td><td>{status_label(tau.get('status'))} {esc(tau_rows)} evaluated；总 simulations {esc(tau_sim_rows)}，infra errors {esc(tau_total.get('infra_error_count', 0))}；当前只代表未完成中间值</td><td>Pass^1 / avg_reward；当前 overall 中间 Pass^1 = {pct(tau_score)}</td><td>{esc(tau_leaderboard_scope)}；当前 4-domain 1-trial 进度不能当 leaderboard final。</td></tr>
          <tr><td><strong>EvolvingEvents</strong></td><td>MemoryBench OpenViking event-memory QA：写入事件上下文、检索、answer、Judge、报告；官方 full dataset/scorer 待定位</td><td><span class="pill warn">MemoryBench 可跑</span> 官方指标未对齐</td><td>MemoryBench memory-QA accuracy；官方指标待补</td><td>不做 SOTA 对比，直到找到官方数据与 scorer。</td></tr>
          <tr><td><strong>proAgentBench</strong></td><td>MemoryBench OpenViking task-memory QA：写入任务上下文、检索、answer、Judge、报告；公开 HF full asset 是 imagefolder + SQLite + screenshots，需独立主动代理 runner</td><td><span class="pill warn">MemoryBench 可跑</span> 官方 runner 未接入</td><td>MemoryBench task-memory QA accuracy；官方 proactive 指标待补</td><td>不把 MemoryBench QA 分数伪装成 proAgentBench 官方主动代理指标。</td></tr>
        </tbody>
      </table>
    </section>
    <section class="panel">
      <h2>tau2-bench 官方 runner 细节</h2>
      <table><thead><tr><th>Domain</th><th>Status</th><th>Sims</th><th>Evaluated</th><th>Infra errors</th><th>Pass^1</th><th>Avg reward</th><th>Last task</th><th>Results</th></tr></thead><tbody>{''.join(tau_domain_rows)}</tbody></table>
    </section>
    <section class="panel">
      <h2>执行方案</h2>
      <div class="steps">
        <div class="step"><h3>继续 HotpotQA 全量长跑</h3><p>保持现有进程，不重启、不截断 CSV。完成 7405 行后运行 answer EM/F1 scorer，并在报告中明确“不是官方 joint F1”。</p><code class="path">{esc(hotpot.get('csv'))}</code></div>
        <div class="step"><h3>完成 tau2-bench 官方四域</h3><p>当前由 supervisor 防重复接力：airline 已完成，retail 正在跑，之后接 telecom 和 banking_knowledge。四域完成后得到 1-trial official-runner Pass^1 / avg_reward；完整 leaderboard 对比仍要 4+ trials。</p><code class="path">python3 scripts/supervise_formal_tau2.py --domains retail telecom banking_knowledge --poll-seconds 60</code></div>
        <div class="step"><h3>刷新正式状态与报告</h3><p>每次长跑推进后执行状态脚本，更新 JSON 和 Markdown；本页面由同一状态 JSON 渲染。</p><code class="path">python3 scripts/formal_benchmark_status.py && python3 scripts/render_formal_benchmark_plan_html.py</code></div>
        <div class="step"><h3>接入剩余官方指标</h3><p>EvolvingEvents/proAgentBench 当前先跑 MemoryBench memory-QA 正式链路；官方 dataset/scorer、HF full asset、SQLite、截图、事件流和主动代理指标 runner 对齐后，再补官方榜单分数。</p></div>
      </div>
    </section>
    <section class="panel">
      <h2>当前证据文件</h2>
      <table><thead><tr><th>项目</th><th>路径</th><th>说明</th></tr></thead>
        <tbody>
          <tr><td>正式状态 JSON</td><td><code>{esc(STATUS_PATH)}</code></td><td>当前所有正式/运行中状态的权威本地汇总。</td></tr>
          <tr><td>正式 Markdown 报告</td><td><code>{esc(ROOT / 'runs' / 'formal_benchmark_report.md')}</code></td><td>按状态 JSON 渲染，含 LongMemEval / HotpotQA / tau2 表格。</td></tr>
          <tr><td>LongMemEval official summary</td><td><code>{esc(longmem.get('artifacts', {}).get('official_summary', {}).get('path'))}</code></td><td>500 条已判；overall_accuracy {esc(longmem_off.get('overall_accuracy'))}。</td></tr>
          <tr><td>HotpotQA CSV</td><td><code>{esc(hotpot.get('csv'))}</code></td><td>运行中；当前 {esc(hotpot_rows)}，strict failures {esc(hotpot.get('failed_rows'))}。</td></tr>
          <tr><td>tau2 airline results</td><td><code>{esc((tau_domains.get('airline') or {}).get('result_path'))}</code></td><td>官方 runner 结果；当前 evaluated {(tau_domains.get('airline') or {}).get('evaluated_simulations', 0)}/{(tau_domains.get('airline') or {}).get('expected_simulations', '-')}。</td></tr>
          <tr><td>tau2 leaderboard scope</td><td><code>{esc(' / '.join(tau.get('leaderboard_domains') or []))}</code></td><td>{esc(tau_leaderboard_scope)}</td></tr>
          <tr><td>tau2 supervisor log</td><td><code>{esc((tau_supervisor.get('log') or {}).get('path'))}</code></td><td>防重复接力日志；当前 PID {esc(','.join(str(pid) for pid in tau_supervisor.get('active_pids', [])) or '-')}。</td></tr>
        </tbody>
      </table>
    </section>
    <section class="panel">
      <h2>SOTA 来源与可比性</h2>
      <table><thead><tr><th>Benchmark</th><th>当前采用的 reference / SOTA</th><th>来源</th><th>比较限制</th></tr></thead>
        <tbody>
          <tr><td>LongMemEval-S</td><td>paper/reference baseline：{esc(longmem_sota.get('model'))}，{esc(longmem_sota.get('metric'))} {pct(longmem_sota.get('score'))}；另有非官方公开 claimed SOTA 需单独审计。</td><td><a href="https://arxiv.org/abs/2410.10813">arXiv 2410.10813</a> / <a href="https://openreview.net/forum?id=pZiyCaVuti">ICLR 2025 OpenReview</a> / <a href="https://github.com/xiaowu0162/LongMemEval">official repo</a></td><td>本地用 gpt-5.5 judge，属于 official-style；该 reference 不是同类 memory-backend leaderboard SOTA。</td></tr>
          <tr><td>HotpotQA distractor</td><td>{esc(hotpot_sota.get('model'))}，{esc(hotpot_sota.get('metric'))} {pct(hotpot_sota.get('score'))}</td><td><a href="https://hotpotqa.github.io/">HotpotQA official leaderboard</a></td><td>本地当前 answer-only；必须实现 supporting fact prediction 才能对 joint F1。</td></tr>
          <tr><td>tau2-bench</td><td>以官网 live leaderboard 的 Text Overall / Pass^1 为准；本地当前是官方 runner 进度，不是最终 leaderboard submission。</td><td><a href="https://taubench.com/">taubench.com</a> / <a href="https://github.com/sierra-research/tau2-bench">official repo</a></td><td>必须跑官方 <code>tau2 run</code>，不能用 OpenViking QA 替代；完整对比需要 banking_knowledge 与多 trial submission。</td></tr>
          <tr><td>proAgentBench</td><td>{esc(proagent_sota.get('model'))}；指标是 {esc(proagent_sota.get('metric')) or 'timing/intention metrics'}，不是 QA accuracy。</td><td><a href="https://huggingface.co/datasets/qv9n2xk7m1z8pt4/ProAgentBench">HF dataset</a> / <a href="https://arxiv.org/abs/2602.04482">paper</a></td><td>当前没有正式可比结果；下载完整资产并实现 SQLite、截图、事件流和多模态主动代理 runner 前不能出分。</td></tr>
          <tr><td>EvolvingEvents</td><td>{esc(evolving_sota.get('model') or 'unknown')}；未定位权威 official metric/source。</td><td>focused local/web audit，暂无可复现官方 dataset/scorer</td><td>MemoryBench memory-QA 可以作为本平台分数；不能把 M-FLOW demo 或 QA 数字写成官方 SOTA 对比。</td></tr>
        </tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""


def main() -> None:
    html_text = render()
    OUT_PATH.write_text(html_text, encoding="utf-8")
    LEGACY_OUT_PATH.write_text(html_text, encoding="utf-8")
    print(json.dumps({"html": str(OUT_PATH), "legacy_html": str(LEGACY_OUT_PATH)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
