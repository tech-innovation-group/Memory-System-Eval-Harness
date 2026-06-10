#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATUS_PATH = ROOT / "runs" / "formal_benchmark_status.json"
WEB_OUT = ROOT / "web" / "static" / "dataset-evaluation-guide.html"
STATIC_OUT = ROOT / "static" / "dataset-evaluation-guide.html"


def read_json(path: Path, fallback: Any = None) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def pct(value: Any, digits: int = 2) -> str:
    if value in (None, ""):
        return "-"
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except Exception:
        return esc(value)


def summary(path: Path) -> dict[str, Any]:
    return read_json(path, {}) or {}


def score_text(data: dict[str, Any]) -> str:
    if not data:
        return "无"
    graded = data.get("graded") or data.get("count")
    correct = data.get("correct")
    accuracy = data.get("accuracy")
    if accuracy is None:
        return "待判分"
    if graded and correct is not None:
        return f"{correct}/{graded} · {pct(accuracy)}"
    return pct(accuracy)


def status_badge(text: str, tone: str = "") -> str:
    klass = f"pill {tone}".strip()
    return f'<span class="{klass}">{esc(text)}</span>'


def artifact(path: Path | str) -> str:
    text = str(path or "")
    return f"<code>{esc(text)}</code>" if text else "-"


def render() -> str:
    status = read_json(STATUS_PATH, {}) or {}
    formal_runs = status.get("runs") or {}
    longmem = formal_runs.get("longmemeval") or {}
    longmem_official = longmem.get("official_summary") or {}
    hotpot = formal_runs.get("hotpotqa") or {}
    tau = status.get("tau2bench") or {}
    tau_total = tau.get("formal_total") or {}
    tau_domains = tau.get("formal_by_domain") or {}

    locomo_same_judge_aligned = summary(ROOT / "runs" / "vikingboat_custom_aligned_same_judge_20260606" / "aligned" / "judge_summary.json")
    locomo_same_judge_native = summary(ROOT / "runs" / "vikingboat_custom_aligned_same_judge_20260606" / "native" / "judge_summary.json")
    locomo_native_compare = summary(ROOT / "runs" / "vikingbot_native_compare_20260602" / "judge_summary.json")

    hotpot_rows = f"{hotpot.get('rows', 0)}/{hotpot.get('expected_rows', '-')}"
    tau_rows = f"{tau_total.get('evaluated_simulations', 0)}/{tau_total.get('expected_simulations', '-')}"
    tau_score = tau_total.get("pass_hat_1")
    delta = None
    if locomo_same_judge_aligned.get("accuracy") is not None and locomo_same_judge_native.get("accuracy") is not None:
        delta = (float(locomo_same_judge_aligned["accuracy"]) - float(locomo_same_judge_native["accuracy"])) * 100

    tau_domain_rows = []
    for domain in tau.get("base_domains", ["airline", "retail", "telecom", "banking_knowledge"]):
        item = tau_domains.get(domain) or {}
        sims = f"{item.get('simulation_count', 0)}/{item.get('expected_simulations', '-')}"
        evaluated = f"{item.get('evaluated_simulations', 0)}/{item.get('expected_simulations', '-')}"
        tone = "good" if item.get("status") == "completed" else ("warn" if item.get("status") in {"running_or_partial", "running"} else "")
        tau_domain_rows.append(
            f"""
            <tr>
              <td>{esc(domain)}</td>
              <td>{status_badge(item.get('status') or 'not_started', tone)}</td>
              <td>{esc(sims)}</td>
              <td>{esc(evaluated)}</td>
              <td>{pct(item.get('pass_hat_1'))}</td>
              <td>{esc(item.get('infra_error_count', 0))}</td>
              <td>{artifact(item.get('result_path') or '')}</td>
            </tr>
            """
        )

    rows = [
        {
            "dataset": "LoCoMo评测",
            "import": "左侧 LoCoMo评测 -> 校验数据集 -> 记忆导入。选择当前 account 和 OpenViking / EchoMemory 后端，可导入单个 conv 或 all；导入后做完整性检查。",
            "test": "LoCoMo评测 -> 问答测试。可选部分 QA 或全量；完成后跑 Judge，再到结果中心生成/比较报告。",
            "score": (
                f"自定义 Agent same-judge：{score_text(locomo_same_judge_aligned)}；"
                f"VikingBoat native same-judge：{score_text(locomo_same_judge_native)}；"
                f"delta {delta:.2f}pp。参考 native compare：{score_text(locomo_native_compare)}。"
                if delta is not None
                else "有 LoCoMo 历史 runs，但 same-judge 对齐结果未完整找到。"
            ),
            "evidence": artifact(ROOT / "runs" / "vikingboat_custom_aligned_same_judge_20260606"),
            "tone": "warn",
        },
        {
            "dataset": "LongMemEval评测",
            "import": "左侧 LongMemEval评测 -> 选择 LongMemEval-S Cleaned Full。正式 run 使用 document memory，把每个样本的长上下文写入 OpenViking 文档记忆。",
            "test": "题数填 0，样本范围 all，启动后真实调用 OpenViking 检索、gpt-5.5 answer，并运行 LongMemEval official-style evaluator。",
            "score": f"{longmem_official.get('correct', '-')}/{longmem_official.get('graded', '-')} · overall {pct(longmem_official.get('overall_accuracy'))}；task avg {pct(longmem_official.get('task_averaged_accuracy'))}。",
            "evidence": artifact(longmem.get("artifacts", {}).get("official_summary", {}).get("path") or ""),
            "tone": "good",
        },
        {
            "dataset": "EvolvingEvents评测",
            "import": "左侧 EvolvingEvents评测 -> 填写 bundled sample 或用户提供的完整 JSON/JSONL。完整路径可作为 MemoryBench event-memory QA 输入。",
            "test": "加载题目后可选 1-3 题做小样本核验；题数 0 或指定题集会真实调用 OpenViking 检索、答案模型、Judge，并生成报告。",
            "score": "MemoryBench memory-QA 分数可由完整数据路径产出；官方 EvolvingEvents 指标和 SOTA 对比需单独标注。",
            "evidence": artifact(ROOT / "dataset" / "evolvingevents.sample.json"),
            "tone": "warn",
        },
        {
            "dataset": "HotpotQA评测",
            "import": "左侧 HotpotQA评测 -> 使用 full/hotpotqa_dev_distractor.json。每条样本的 context documents 被写入 OpenViking document memory。",
            "test": "真实长跑使用 MemoryBench OpenViking memory-QA + gpt-5.5 answer；完成后自动生成 HotpotQA answer EM/F1。supporting-fact/joint F1 尚未接入。",
            "score": f"运行中 {hotpot_rows} · {hotpot.get('progress_pct', '-')}%；strict failures {hotpot.get('failed_rows', 0)}。完成前没有正式准确率；当前 partial exact-match reference 不当最终分。",
            "evidence": artifact(hotpot.get("csv") or ""),
            "tone": "warn",
        },
        {
            "dataset": "proAgentBench评测",
            "import": "左侧 proAgentBench评测 -> 填写 bundled sample 或完整 JSON/JSONL 任务文件。完整多模态官方资产仍需要单独挂载。",
            "test": "MemoryBench task-memory QA 会写入任务上下文、检索记忆、调用答案模型和 Judge；官方 proactive timing/intention 指标需官方 runner 单独标注。",
            "score": "可产出 MemoryBench task-memory QA 分数；不能用 QA accuracy 替代 proAgentBench 官方主动代理指标。",
            "evidence": artifact(ROOT / "dataset" / "proagentbench.sample.json"),
            "tone": "warn",
        },
        {
            "dataset": "Tau2-bench评测",
            "import": "Tau2-bench 正式分数不走记忆导入；使用 external/tau2-bench 的官方 domain tasks、tools 和 user simulator。页面 sample 只做字段检查。",
            "test": "正式测试用官方 tau2 run，当前 supervisor 串行跑 airline、retail、telecom、banking_knowledge；agent/user simulator 使用 gpt-5.5。",
            "score": f"官方 runner 运行中 {tau_rows} evaluated；中间 Pass^1 {pct(tau_score)}。未完成四域和多 trial 前不是 leaderboard final。",
            "evidence": artifact((tau.get("supervisor") or {}).get("log", {}).get("path") or ""),
            "tone": "warn",
        },
        {
            "dataset": "chenmo评测",
            "import": "左侧 chenmo评测 -> 校验数据集。当前入口仅保留数据校验和题目浏览；正式全量基线需要用 EchoMemory version_0.0.5 重新跑。",
            "test": "如果要对外展示 ChenMo 结果，需基于 EchoMemory version_0.0.5 重新生成结果与 HTML 报告，不再复用历史 v0.0.4 run。",
            "score": "当前页面不再把历史 EchoMemory v0.0.4 run 作为现行基线。",
            "evidence": artifact(ROOT / "dataset" / "chenmo_evaluation_scenario.md"),
            "tone": "warn",
        },
    ]

    body_rows = "\n".join(
        f"""
        <tr>
          <td><strong>{esc(row['dataset'])}</strong><br>{status_badge('已完成' if row['tone'] == 'good' else ('运行中/受限' if row['tone'] == 'warn' else '暂无正式分'), row['tone'])}</td>
          <td>{esc(row['import'])}</td>
          <td>{esc(row['test'])}</td>
          <td>{esc(row['score'])}</td>
          <td>{row['evidence']}</td>
        </tr>
        """
        for row in rows
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>数据集导入、测试与准确率总表</title>
  <style>
    :root {{ color-scheme: light; --ink:#172033; --muted:#5e6c80; --line:#dce4ee; --panel:#fff; --bg:#f6f8fb; --good:#10734d; --warn:#9a5b06; --bad:#b2333c; --blue:#235b98; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:var(--ink); background:var(--bg); line-height:1.55; }}
    header {{ background:#10253a; color:#fff; padding:30px 40px 24px; }}
    main {{ max-width:1320px; margin:0 auto; padding:24px 40px 44px; }}
    h1 {{ margin:0 0 8px; font-size:29px; letter-spacing:0; }}
    h2 {{ margin:26px 0 12px; font-size:21px; }}
    p {{ margin:7px 0; }}
    a {{ color:var(--blue); }}
    code {{ font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; word-break:break-all; }}
    table {{ width:100%; border-collapse:collapse; background:var(--panel); border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
    th,td {{ padding:11px 12px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; font-size:14px; }}
    th {{ background:#edf3f8; color:#31465a; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
    tr:last-child td {{ border-bottom:0; }}
    .sub {{ color:#d5e2ee; max-width:980px; }}
    .stamp {{ color:#a8c1d7; font-size:13px; }}
    .panel {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:16px; margin-top:16px; }}
    .note {{ border-left:4px solid var(--blue); background:#edf6ff; border-radius:6px; padding:10px 12px; }}
    .pill {{ display:inline-block; margin-top:6px; padding:2px 8px; border:1px solid var(--line); border-radius:999px; background:#f7fafc; color:#33485a; font-size:12px; white-space:nowrap; }}
    .pill.good {{ color:var(--good); border-color:#b8dcca; background:#f0faf5; }}
    .pill.warn {{ color:var(--warn); border-color:#e8c991; background:#fff8ec; }}
    .pill.bad {{ color:var(--bad); border-color:#e2b8b8; background:#fff3f3; }}
    .actions {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:13px; }}
    .button {{ display:inline-flex; align-items:center; min-height:34px; padding:0 12px; border:1px solid var(--line); border-radius:8px; background:#fff; color:var(--ink); text-decoration:none; font-weight:700; }}
    @media (max-width:900px) {{ header,main {{ padding-left:18px; padding-right:18px; }} table {{ display:block; overflow-x:auto; }} }}
  </style>
</head>
<body>
  <header>
    <p class="stamp">生成时间：{esc(datetime.now().isoformat(timespec='seconds'))} · 数据来源：本机 dataset/ 与 runs/</p>
    <h1>数据集导入、测试与准确率总表</h1>
    <p class="sub">回答“这几个数据集分别怎么导入、怎么测试、准确率怎么样”。所有分数都按当前本机证据标注；sample 小样本核验不冒充完整数据或官方榜单。</p>
  </header>
  <main>
    <section class="panel">
      <p class="note">当前后端范围只有 OpenViking 和 EchoMemory。LoCoMo/LongMemEval/HotpotQA 可走记忆后端；EvolvingEvents/proAgentBench 可产出 MemoryBench memory-QA 分数；Tau2-bench 官方 Pass^k/reward 必须走官方 tau2 runner。缺官方 runner/scorer 的数据集不冒充官方榜单分。</p>
      <div class="actions">
        <a class="button" href="/">返回测试台</a>
        <a class="button" href="/formal-benchmark-plan-20260606.html">正式评测与 SOTA</a>
        <a class="button" href="/accuracy-strategy.html">准确率策略</a>
      </div>
    </section>
    <section class="panel">
      <h2>总表</h2>
      <table>
        <thead>
          <tr><th>数据集</th><th>怎么导入</th><th>怎么测试</th><th>当前准确率 / 状态</th><th>证据</th></tr>
        </thead>
        <tbody>{body_rows}</tbody>
      </table>
    </section>
    <section class="panel">
      <h2>Tau2-bench 运行细节</h2>
      <table>
        <thead><tr><th>Domain</th><th>Status</th><th>Simulations</th><th>Evaluated</th><th>Pass^1</th><th>Infra errors</th><th>Results</th></tr></thead>
        <tbody>{''.join(tau_domain_rows)}</tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""


def main() -> None:
    WEB_OUT.write_text(render(), encoding="utf-8")
    STATIC_OUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(WEB_OUT, STATIC_OUT)
    print(json.dumps({"web": str(WEB_OUT), "static": str(STATIC_OUT)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
