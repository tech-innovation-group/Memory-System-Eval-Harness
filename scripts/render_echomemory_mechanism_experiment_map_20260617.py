#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_mechanism_experiment_map_20260617.html"
)


def render() -> str:
    rows = [
        (
            "Three-clock time",
            "如果 memory 只留一个 created_at，系统很容易把事件发生时间、被提到时间、写入时间混成一个字段。",
            "three-clock temporal ablation",
            "`0/4 -> 4/4 -> 4/4`",
            "时间正确性首先是 schema 问题，然后才是 routing 问题。",
            "`nano_three_clock_temporal_ablation_results.json`",
            "`normalize_date()` / `_extract_atoms()` / `_build_temporal_tree()`",
        ),
        (
            "Topic-centered middle layer",
            "只有 global overview 太粗，只有 flat atoms 太碎，跨 session 主题演化题容易丢上下文连续性。",
            "topic-dossier ablation + generalization benchmark",
            "`1/5 -> 3/5 -> 4/5`；generalization `1/6 -> 3/6 -> 5/6`",
            "需要一个位于 overview 和 atom 之间的中层主题对象。",
            "`nano_topic_dossier_ablation_results.json`",
            "`_build_dossiers()` / `_induce_topic_groups()`",
        ),
        (
            "Generic topic induction",
            "如果 topic 中层依赖手工 topic hint，就不够泛化；但完全不加任何主题结构又会散。",
            "topic-induction benchmark",
            "`5/5` with hints；`5/5` without hints",
            "可以不用 benchmark-specific topic hints 保住任务行为，但 induced labels 仍更粗。",
            "`nano_reference_impl_v14_topic_induction_benchmark_results.json`",
            "`_induce_topic_groups()` / `_topic_signature()` / `_topic_tokens()`",
        ),
        (
            "Dual backbone retrieval",
            "时间题和关系题不是同一种检索问题，强行用统一主干会让一部分题型长期掉分。",
            "dual-backbone ablation + anchored temporal + relation-backbone",
            "dual `4/4`；anchored temporal `3/3`；relation `3/3`",
            "temporal tree 和 graph 各自覆盖不同失败模式，组合更稳。",
            "`nano_dual_backbone_ablation_results.json` / `nano_anchored_temporal_ablation_results.json` / `nano_relation_backbone_ablation_results.json`",
            "`plan()` / `_reader()` / `_reader_for_missing()`",
        ),
        (
            "Readiness plane",
            "durability 不等于 answerability；写入成功不代表所有下游 plane 都 ready。",
            "readiness ablation",
            "`1/5 -> 4/5 -> 5/5`",
            "readiness 不是工程细节，而是 correctness 约束。",
            "`nano_readiness_ablation_results.json`",
            "`Readiness` / `_invalidate_downstream()` / `build()`",
        ),
        (
            "Self-check after retrieval",
            "primary retrieval 看起来相关，不代表证据形状真的够回答。",
            "self-check v2",
            "`4/8 -> 8/8`",
            "retrieval 之后还需要 answer-time policy。",
            "`nano_dual_backbone_selfcheck_v2_results.json`",
            "`_present_layers()` + post-retrieval audit path",
        ),
        (
            "Coverage-aware gating",
            "高分 lexical hit 容易让系统过早停下，但 planned evidence family 仍没补齐。",
            "coverage-aware gating ablation",
            "contract-complete `1/6 -> 2/6`",
            "confidence 不等于 evidence sufficiency。",
            "`nano_coverage_aware_gating_ablation_results.json`",
            "main repo 对应 `evidence_contract.py` / `self_check.py` policy direction",
        ),
        (
            "Type-aware second pass",
            "second pass 如果固定只补 graph，会补错 reader；缺 event/fact/time 时需要补别的层。",
            "type-aware second-pass ablation",
            "contract-complete `1/5 -> 3/5 -> 5/5`",
            "不是“有没有 second pass”，而是“能不能补对 supporting reader”。",
            "`nano_type_aware_second_pass_ablation_results.json`",
            "`retrieve()` / `_reader_for_missing()`",
        ),
        (
            "Multimodal evidence contract",
            "visual 题经常命中 screenshot，但缺 linked fact / owner / event 时仍然答不对。",
            "multimodal contract ablation",
            "contract-complete `2/5 -> 5/5`",
            "image evidence 要成为一等记忆对象，不是 OCR 附件。",
            "`nano_multimodal_contract_ablation_results.json`",
            "`append_image()` / `_build_graph()` / visual branch in `plan()`",
        ),
        (
            "Answerability gate",
            "即使 contract 看起来 complete，candidate answer 也可能仍然不被支持。",
            "answerability benchmark",
            "`2/6 -> 6/6`",
            "`contract_ok` 不是 `answerable=true` 的同义词。",
            "`nano_reference_impl_v14_answerability_benchmark_results.json`",
            "`_answer()` / `_answerability_ok()`",
        ),
        (
            "Paraphrase robustness",
            "如果 family routing 依赖某几个 surface cue，就会在改写 query 时掉得很厉害。",
            "paraphrase benchmark",
            "answer-correct `8/15 -> 15/15`；family-correct `13/15 -> 15/15`",
            "泛化应该来自 generic family routing，而不是 benchmark 关键词表。",
            "`nano_reference_impl_v14_paraphrase_benchmark_results.json`",
            "`plan()` family rules + generic cues",
        ),
    ]

    table_rows = []
    for idx, row in enumerate(rows, start=1):
        table_rows.append(
            f"""
            <tr>
              <td>{idx}</td>
              <td><b>{row[0]}</b></td>
              <td>{row[1]}</td>
              <td>{row[2]}</td>
              <td>{row[3]}</td>
              <td>{row[4]}</td>
              <td><code>{row[5].replace('`','')}</code></td>
              <td><code>{row[6].replace('`','')}</code></td>
            </tr>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Mechanism-to-Experiment Map</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --line:#dbe3ee; --text:#18212f; --muted:#617184; --blue:#2563eb;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif; }}
    .wrap {{ max-width:1380px; margin:0 auto; padding:28px 20px 54px; }}
    .hero,.card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:22px 24px; margin-bottom:16px; }}
    h1,h2 {{ margin:0 0 12px; line-height:1.25; }}
    h1 {{ font-size:30px; }}
    h2 {{ font-size:21px; }}
    p {{ margin:0 0 12px; }}
    .muted {{ color:var(--muted); }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th,td {{ border-top:1px solid var(--line); text-align:left; vertical-align:top; padding:10px 8px; }}
    th {{ background:#fbfcfe; color:var(--muted); font-size:12px; }}
    code {{ background:#f3f6fb; border:1px solid #e5eaf2; border-radius:6px; padding:1px 4px; font:12px ui-monospace,SFMono-Regular,Menlo,monospace; }}
    .kpi {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-top:14px; }}
    .kpi>div {{ border:1px solid var(--line); border-radius:10px; padding:12px; background:#fbfcfe; }}
    .kpi strong {{ display:block; font-size:22px; margin-bottom:4px; }}
    @media (max-width: 980px) {{ .kpi {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory-MM 机制与实验对应图</h1>
      <p class="muted">
        这页解决一个常见 reviewer 问题：每个机制为什么存在，它到底被哪条实验线验证，结果说明了什么，
        又落在 nano 实现的哪个函数上。这样论文主张、实验结果、实现代码就不是三张皮。
      </p>
      <div class="kpi">
        <div><strong>11</strong><span class="muted">mechanism lines</span></div>
        <div><strong>11</strong><span class="muted">paired experiment lines</span></div>
        <div><strong>1</strong><span class="muted">shared nano reference</span></div>
        <div><strong>0</strong><span class="muted">dataset keyword hacks required</span></div>
      </div>
    </section>

    <section class="card">
      <h2>Mechanism → Experiment → Code</h2>
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>机制</th>
            <th>为什么要有</th>
            <th>对应实验</th>
            <th>结果</th>
            <th>说明了什么</th>
            <th>结果文件</th>
            <th>nano 代码锚点</th>
          </tr>
        </thead>
        <tbody>
          {''.join(table_rows)}
        </tbody>
      </table>
    </section>
  </div>
</body>
</html>
"""


def main() -> None:
    OUT_HTML.write_text(render(), encoding="utf-8")
    print(OUT_HTML)


if __name__ == "__main__":
    main()
