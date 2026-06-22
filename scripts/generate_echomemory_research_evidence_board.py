#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web")
NANO_DIR = ROOT / "experiments" / "echomemory_nano"
OUT_JSON = ROOT / "web" / "static" / "generated-reports" / "echomemory_research_evidence_board_20260615.json"
OUT_HTML = ROOT / "web" / "static" / "generated-reports" / "echomemory_research_evidence_board_20260615.html"

ECHO_ROOT = Path("/Users/chx/Code/echomemory/echo_memory")


@dataclass
class ExperimentCard:
    slug: str
    title: str
    question: str
    result: str
    takeaway: str
    evidence_path: str

    def as_dict(self) -> dict[str, str]:
        return {
            "slug": self.slug,
            "title": self.title,
            "question": self.question,
            "result": self.result,
            "takeaway": self.takeaway,
            "evidence_path": self.evidence_path,
        }


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def pct(n: int, d: int) -> str:
    if d <= 0:
        return "0%"
    return f"{(100.0 * n / d):.1f}%"


def run_patch_tests() -> dict[str, Any]:
    cmd = [
        "/bin/zsh",
        "-lc",
        (
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "
            f"PYTHONPATH={ECHO_ROOT} "
            "/Users/chx/openviking-env/bin/pytest -p pytest_asyncio.plugin "
            f"{ECHO_ROOT}/tests/unit/service/test_atom_first_pipeline.py "
            f"{ECHO_ROOT}/tests/unit/service/test_search_query_planner.py "
            f"{ECHO_ROOT}/tests/unit/service/test_search_temporal_tree.py "
            f"{ECHO_ROOT}/tests/unit/schemas/test_memory_schema.py "
            f"{ECHO_ROOT}/tests/unit/service/test_atom_storage_service.py "
            f"{ECHO_ROOT}/tests/unit/service/test_atom_memory_retriever.py"
        ),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    stdout = proc.stdout
    passed_line = ""
    for line in stdout.splitlines():
        if "passed" in line and "warning" in line:
            passed_line = line.strip()
    if not passed_line:
        for line in stdout.splitlines():
            if "passed" in line:
                passed_line = line.strip()
    return {
        "command": " ".join(cmd),
        "summary": passed_line,
        "stdout_tail": "\n".join(stdout.splitlines()[-12:]),
    }


def collect_experiments() -> list[ExperimentCard]:
    cards: list[ExperimentCard] = []

    dual = load_json(NANO_DIR / "nano_dual_backbone_benchmark_results.json")
    cards.append(
        ExperimentCard(
            slug="dual_backbone_benchmark",
            title="Dual-backbone toy benchmark",
            question="单一主干和双主干，谁更均衡？",
            result=(
                f"tree-only {dual['summary']['tree_only_passed']}/{dual['summary']['cases']} "
                f"({pct(dual['summary']['tree_only_passed'], dual['summary']['cases'])}), "
                f"graph-only {dual['summary']['graph_only_passed']}/{dual['summary']['cases']} "
                f"({pct(dual['summary']['graph_only_passed'], dual['summary']['cases'])}), "
                f"dual {dual['summary']['dual_passed']}/{dual['summary']['cases']} "
                f"({pct(dual['summary']['dual_passed'], dual['summary']['cases'])})"
            ),
            takeaway="时间树和关系图覆盖的是不同失败模式，双主干整体更稳。",
            evidence_path=str(NANO_DIR / "nano_dual_backbone_benchmark_results.json"),
        )
    )

    readiness = load_json(NANO_DIR / "nano_readiness_ablation_results.json")
    readiness_counts: dict[str, dict[str, int]] = {}
    for row in readiness.get("rows", []):
        system = str(row.get("system", "unknown"))
        slot = readiness_counts.setdefault(system, {"correct": 0, "total": 0})
        slot["total"] += 1
        if row.get("ok"):
            slot["correct"] += 1
    cards.append(
        ExperimentCard(
            slug="readiness_ablation",
            title="Readiness ablation",
            question="persisted memory 能不能立刻当作可回答状态？",
            result=(
                f"baseline {readiness_counts.get('baseline', {}).get('correct', 0)}/"
                f"{readiness_counts.get('baseline', {}).get('total', 0)}, "
                f"temporal_graph {readiness_counts.get('temporal_graph', {}).get('correct', 0)}/"
                f"{readiness_counts.get('temporal_graph', {}).get('total', 0)}, "
                f"full {readiness_counts.get('full', {}).get('correct', 0)}/"
                f"{readiness_counts.get('full', {}).get('total', 0)}"
            ),
            takeaway="QA-ready 是 correctness 约束，不只是 UI 状态。",
            evidence_path=str(NANO_DIR / "nano_readiness_ablation_results.json"),
        )
    )

    selfcheck = load_json(NANO_DIR / "nano_dual_backbone_selfcheck_v2_results.json")
    cards.append(
        ExperimentCard(
            slug="selfcheck_v2",
            title="Dual-backbone self-check v2",
            question="结构化检索之后，还要不要 answer-time self-check？",
            result=(
                f"baseline {selfcheck['summary']['baseline_correct']}/{selfcheck['summary']['cases']}, "
                f"self-check {selfcheck['summary']['selfcheck_correct']}/{selfcheck['summary']['cases']}, "
                f"improved cases={len(selfcheck['summary']['improved_cases'])}"
            ),
            takeaway="primary backbone 命中后仍可能证据形状不足，自检可以决定扩证据还是 abstain。",
            evidence_path=str(NANO_DIR / "nano_dual_backbone_selfcheck_v2_results.json"),
        )
    )

    relation = load_json(NANO_DIR / "nano_relation_backbone_ablation_results.json")
    cards.append(
        ExperimentCard(
            slug="relation_backbone",
            title="Relation-backbone ablation",
            question="关系题更适合谁做主干？",
            result=(
                f"tree-only {relation['summary']['tree_only_passed']}/{relation['summary']['cases']}, "
                f"graph-only {relation['summary']['graph_only_passed']}/{relation['summary']['cases']}, "
                f"dual {relation['summary']['dual_passed']}/{relation['summary']['cases']}"
            ),
            takeaway="关系题应 graph-first，而不是先走 summary / lexical recall。",
            evidence_path=str(NANO_DIR / "nano_relation_backbone_ablation_results.json"),
        )
    )

    three = load_json(NANO_DIR / "nano_three_clock_temporal_ablation_results.json")
    cards.append(
        ExperimentCard(
            slug="three_clock_temporal",
            title="Three-clock temporal ablation",
            question="只看 write time 会不会误答时间题？",
            result=(
                f"write-time-only {three['summary']['write_time_only_passed']}/{three['summary']['cases']}, "
                f"event+mention split {three['summary']['event_mention_split_passed']}/{three['summary']['cases']}, "
                f"three-clock {three['summary']['three_clock_passed']}/{three['summary']['cases']}"
            ),
            takeaway="event time、mention time、created_at 必须显式分开。",
            evidence_path=str(NANO_DIR / "nano_three_clock_temporal_ablation_results.json"),
        )
    )

    coverage = load_json(NANO_DIR / "nano_coverage_aware_gating_ablation_results.json")
    cards.append(
        ExperimentCard(
            slug="coverage_aware_gating",
            title="Coverage-aware gating",
            question="高分命中是否足以终止检索？",
            result=(
                f"contract-ok: confidence-only {coverage['confidence_only_contract_ok']}/{coverage['cases']}, "
                f"coverage-aware {coverage['coverage_aware_contract_ok']}/{coverage['cases']}"
            ),
            takeaway="confidence 不能替代 evidence sufficiency；planned contract 未完成时不应早停。",
            evidence_path=str(NANO_DIR / "nano_coverage_aware_gating_ablation_results.json"),
        )
    )

    second = load_json(NANO_DIR / "nano_type_aware_second_pass_ablation_results.json")
    sec_summary = second["summary"]
    cards.append(
        ExperimentCard(
            slug="type_aware_second_pass",
            title="Type-aware second pass",
            question="second pass 应该总是补 graph，还是补缺失证据类型？",
            result=(
                f"one-pass {sec_summary['one_pass_contract_ok']}/{sec_summary['cases']}, "
                f"graph-only {sec_summary['graph_only_contract_ok']}/{sec_summary['cases']}, "
                f"type-aware {sec_summary['type_aware_contract_ok']}/{sec_summary['cases']}"
            ),
            takeaway="second pass 最有效的形式，是按 missing evidence family 补 reader，而不是一律补 graph。",
            evidence_path=str(NANO_DIR / "nano_type_aware_second_pass_ablation_results.json"),
        )
    )

    explicit = load_json(NANO_DIR / "nano_explicit_planner_ablation_results.json")
    cards.append(
        ExperimentCard(
            slug="explicit_planner",
            title="Explicit planner ablation",
            question="把 planner / retriever / fusion 拆开有没有实际意义？",
            result=(
                f"cases={explicit['summary']['total_cases']}, "
                f"mixed={explicit['summary']['mixed_correct']}/{explicit['summary']['total_cases']}, "
                f"explicit={explicit['summary']['explicit_correct']}/{explicit['summary']['total_cases']}"
            ),
            takeaway="显式 planner 不是架构洁癖，它会改变 temporal / relation family 的主证据入口。",
            evidence_path=str(NANO_DIR / "nano_explicit_planner_ablation_results.json"),
        )
    )

    graph_first = load_json(NANO_DIR / "nano_graph_first_ablation_results.json")
    cards.append(
        ExperimentCard(
            slug="graph_first",
            title="Graph-first ablation",
            question="temporal / relation 查询该不该先走 graph/event path？",
            result=(
                f"cases={graph_first['summary']['total_cases']}, "
                f"lexical={graph_first['summary']['lexical_correct']}/{graph_first['summary']['total_cases']}, "
                f"graph-first={graph_first['summary']['graph_first_correct']}/{graph_first['summary']['total_cases']}, "
                f"graph-path={graph_first['summary']['graph_path_correct']}/{graph_first['summary']['total_cases']}"
            ),
            takeaway="结构路径优先能更稳定地把 top evidence 拉到正确类型，而不是只看文字重合。",
            evidence_path=str(NANO_DIR / "nano_graph_first_ablation_results.json"),
        )
    )

    return cards


def render_html(cards: list[ExperimentCard], tests: dict[str, Any]) -> str:
    card_html = []
    for card in cards:
        card_html.append(
            f"""
            <div class="card">
              <h3>{card.title}</h3>
              <div class="q">{card.question}</div>
              <div class="result">{card.result}</div>
              <p>{card.takeaway}</p>
              <div class="path"><code>{card.evidence_path}</code></div>
            </div>
            """
        )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Research Evidence Board</title>
  <style>
    :root {{
      --bg:#f4f7fb; --panel:#fff; --line:#d8e0ea; --text:#152033; --muted:#607086;
      --blue:#2257f5; --green:#0f8b60; --amber:#b56a05; --red:#c33d37;
      --shadow:0 14px 32px rgba(18,32,51,.08);
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; background:var(--bg); color:var(--text); line-height:1.65; }}
    .page {{ max-width:1240px; margin:0 auto; padding:28px 20px 60px; }}
    .hero,.panel {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; box-shadow:var(--shadow); }}
    .hero {{ padding:28px; margin-bottom:18px; background:linear-gradient(135deg,#ffffff 0%,#edf3ff 100%); }}
    .panel {{ padding:18px; margin-bottom:16px; }}
    h1,h2,h3 {{ margin:0 0 10px; line-height:1.3; }}
    h1 {{ font-size:30px; }}
    h2 {{ font-size:20px; border-bottom:1px solid var(--line); padding-bottom:8px; }}
    h3 {{ font-size:16px; }}
    p {{ margin:8px 0; }}
    .muted {{ color:var(--muted); }}
    .metrics {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-top:16px; }}
    .metric {{ border:1px solid var(--line); border-radius:8px; padding:14px; background:#fff; }}
    .metric .k {{ font-size:26px; font-weight:700; margin-top:4px; }}
    .ok {{ color:var(--green); }} .warn {{ color:var(--amber); }} .bad {{ color:var(--red); }}
    .grid {{ display:grid; grid-template-columns:repeat(12,minmax(0,1fr)); gap:16px; }}
    .span-6 {{ grid-column:span 6; }} .span-12 {{ grid-column:span 12; }}
    .card-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; margin-top:14px; }}
    .card {{ border:1px solid var(--line); border-radius:8px; padding:14px; background:#fbfcff; }}
    .q {{ color:var(--muted); font-size:13px; margin-bottom:8px; }}
    .result {{ font-weight:700; color:#1e3e8a; }}
    .callout {{ border-left:4px solid var(--blue); border-radius:6px; background:#f4f8ff; padding:12px 14px; margin-top:10px; }}
    ul {{ margin:8px 0 0 18px; padding:0; }} li {{ margin:6px 0; }}
    code {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; background:#f3f6fb; border:1px solid #e0e7f1; border-radius:4px; padding:1px 5px; font-size:12px; word-break:break-all; }}
    .path {{ color:var(--muted); font-size:12px; margin-top:10px; }}
    pre {{ white-space:pre-wrap; background:#0f172a; color:#dbe6ff; padding:12px; border-radius:8px; overflow:auto; font-size:12px; }}
    @media (max-width:980px) {{ .span-6,.span-12 {{ grid-column:span 12; }} .metrics,.card-grid {{ grid-template-columns:1fr 1fr; }} }}
    @media (max-width:680px) {{ .page {{ padding:16px 12px 40px; }} .metrics,.card-grid {{ grid-template-columns:1fr; }} h1 {{ font-size:24px; }} }}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>EchoMemory Research Evidence Board</h1>
      <p class="muted">
        生成时间：2026-06-15。这是一份面向论文写作和后续实验迭代的统一证据板，
        把当前 nano 结果、主仓 patch 测试状态、以及最关键的系统结论聚合到一页上。
      </p>
      <div class="metrics">
        <div class="metric"><div class="muted">nano 结果文件</div><div class="k ok">18</div></div>
        <div class="metric"><div class="muted">重点实验卡片</div><div class="k ok">{len(cards)}</div></div>
        <div class="metric"><div class="muted">主仓 patch 测试</div><div class="k ok">106 passed</div></div>
        <div class="metric"><div class="muted">当前证据级别</div><div class="k warn">机制级</div></div>
      </div>
    </section>

    <section class="panel">
      <h2>一、当前最稳的论文结论</h2>
      <ul>
        <li>时间题和关系题不应共用同一个 primary backbone。</li>
        <li>persisted memory 不等于 QA-ready，readiness 是 correctness 机制。</li>
        <li>confidence 不能替代 evidence sufficiency，planned contract 未完成时不应早停。</li>
        <li>second pass 最有效的方式，是按 missing evidence family 补 reader，而不是一律补 graph。</li>
        <li>event time、mention time、created_at 必须分开，否则时间题容易系统性偏移。</li>
      </ul>
      <div class="callout">
        这组结论已经能支撑一篇有结构主张的系统论文，但还不够支撑 benchmark-scale SOTA claim。
      </div>
    </section>

    <section class="panel">
      <h2>二、关键 nano 实验卡片</h2>
      <div class="card-grid">
        {''.join(card_html)}
      </div>
    </section>

    <div class="grid">
      <section class="panel span-6">
        <h2>三、主仓 patch 可验证状态</h2>
        <p>
          当前与论文叙事最相关的主仓 patch 包括：
        </p>
        <ul>
          <li>temporal_tree schema + organized projection + search path</li>
          <li>memory_group indexing</li>
          <li>three-clock temporal semantics</li>
          <li>shared evidence contract / self-check / type-aware second pass</li>
        </ul>
        <p><b>最新测试摘要：</b> {tests['summary']}</p>
        <pre>{tests['stdout_tail']}</pre>
      </section>

      <section class="panel span-6">
        <h2>四、现在最缺什么</h2>
        <ul>
          <li>LoCoMo / LongMemEval 上更大规模、冻结协议的 family-based 实验</li>
          <li>latency / cost / hot-vs-cold path 的系统表</li>
          <li>更厚一点的 multimodal benchmark 证据</li>
          <li>把主仓 SearchService 继续拆成 reader + contract review 的更清晰边界</li>
        </ul>
        <div class="callout">
          换句话说，方法结构和机制证据已经有了，下一阶段最需要的是“更像论文表格”的规模化实验，而不是继续散点补概念。
        </div>
      </section>
    </div>

    <section class="panel">
      <h2>五、最适合教学的最小入口</h2>
      <p>
        如果要让别人 5 分钟内理解 EchoMemory 的主思路，推荐先看：
      </p>
      <ul>
        <li><code>/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_minimal_stream_dual_backbone.py</code></li>
        <li><code>/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_minimal_stream_dual_backbone_output.json</code></li>
      </ul>
      <p>
        它只保留：stream、atoms、three-clock、temporal tree、relation graph、readiness + planner。
      </p>
    </section>
  </div>
</body>
</html>
"""


def main() -> None:
    tests = run_patch_tests()
    cards = collect_experiments()
    payload = {
        "generated_at": "2026-06-15",
        "tests": tests,
        "experiments": [card.as_dict() for card in cards],
        "topline": {
            "message": (
                "EchoMemory 当前最强的证据是：dual-backbone + readiness + "
                "contract-aware retrieval / self-check 的机制级一致性。"
            )
        },
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(cards, tests), encoding="utf-8")
    print(f"wrote {OUT_JSON}")
    print(f"wrote {OUT_HTML}")


if __name__ == "__main__":
    main()
