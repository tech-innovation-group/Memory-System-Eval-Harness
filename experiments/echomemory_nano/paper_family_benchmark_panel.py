#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_mm_family_benchmark_panel_20260616.html"
)


def esc(value: Any) -> str:
    return html.escape(str(value))


def load(name: str) -> dict[str, Any]:
    return json.loads((ROOT / name).read_text())


def pct(n: int, d: int) -> str:
    if not d:
        return "0.00%"
    return f"{(100.0 * n / d):.2f}%"


def section_row(title: str, setup: str, finding: str, evidence: str, claim: str) -> str:
    return (
        "<tr>"
        f"<td><b>{esc(title)}</b></td>"
        f"<td>{esc(setup)}</td>"
        f"<td>{esc(finding)}</td>"
        f"<td>{evidence}</td>"
        f"<td>{esc(claim)}</td>"
        "</tr>"
    )


def main() -> None:
    three_clock = load("nano_three_clock_temporal_ablation_results.json")["summary"]
    dual_benchmark = load("nano_dual_backbone_benchmark_results.json")["summary"]
    selfcheck = load("nano_dual_backbone_selfcheck_v2_results.json")["summary"]
    dossier_general = load("nano_topic_dossier_generalization_benchmark_results.json")["modes"]
    coverage = load("nano_coverage_aware_gating_ablation_results.json")
    second_pass = load("nano_graph_second_pass_contract_ablation_results.json")["summary"]
    type_aware = load("nano_type_aware_second_pass_ablation_results.json")["summary"]
    multimodal = load("nano_multimodal_contract_ablation_results.json")["summary"]
    readiness = load("nano_readiness_ablation_results.json")["summary"]
    graph_first = load("nano_graph_first_ablation_results.json")["summary"]
    relation_backbone = load("nano_relation_backbone_ablation_results.json")["summary"]
    temporal_contract = load("nano_temporal_event_time_contract_ablation_results.json")["policies"]
    relational_contract = load("nano_relational_path_grounding_contract_ablation_results.json")["policies"]
    visual_contract = load("nano_visual_image_evidence_contract_ablation_results.json")["policies"]

    rows = []
    rows.append(
        section_row(
            "Temporal semantics",
            "Compare write-time only vs event+mention split vs three-clock on 4 generic temporal cases.",
            f"Write-time only {three_clock['write_time_only_passed']}/{three_clock['cases']}; split {three_clock['event_mention_split_passed']}/{three_clock['cases']}; three-clock {three_clock['three_clock_passed']}/{three_clock['cases']}.",
            f"<code>{esc('nano_three_clock_temporal_ablation_results.json')}</code>",
            "Explicit story-time structure is necessary. A single created_at-like clock is not enough for date questions.",
        )
    )
    rows.append(
        section_row(
            "Temporal evidence contract",
            "Require temporal_tree + event, then strengthen to temporal_tree + event + event_time.",
            "Layer-only coverage can look complete while true story-time grounding is still absent.",
            (
                "<code>layer_only_temporal</code>: contract_ok=True but has_event_time=False"
                "<br><code>event_time_temporal</code>: contract_ok=True and has_event_time=True"
            ),
            "For temporal QA, event nodes are not sufficient unless at least one hit carries explicit event-time evidence.",
        )
    )
    rows.append(
        section_row(
            "Dual backbone",
            "Compare tree-only, graph-only, dual-backbone on a 12-case family benchmark.",
            (
                f"Tree-only {dual_benchmark['tree_only_passed']}/{dual_benchmark['cases']} "
                f"({pct(dual_benchmark['tree_only_passed'], dual_benchmark['cases'])}), "
                f"graph-only {dual_benchmark['graph_only_passed']}/{dual_benchmark['cases']} "
                f"({pct(dual_benchmark['graph_only_passed'], dual_benchmark['cases'])}), "
                f"dual {dual_benchmark['dual_passed']}/{dual_benchmark['cases']} "
                f"({pct(dual_benchmark['dual_passed'], dual_benchmark['cases'])})."
            ),
            f"<code>{esc('nano_dual_backbone_benchmark_results.json')}</code>",
            "Chronology-heavy and relation-heavy questions fail differently; tree+graph is a structural improvement, not a benchmark trick.",
        )
    )
    rows.append(
        section_row(
            "Relation routing",
            "Compare lexical baseline, graph-first, and graph-path grounding on 4 relation-heavy cases.",
            (
                f"Lexical {graph_first['lexical_correct']}/{graph_first['total_cases']}, "
                f"graph-first {graph_first['graph_first_correct']}/{graph_first['total_cases']}, "
                f"graph-path {graph_first['graph_path_correct']}/{graph_first['total_cases']}."
            ),
            f"<code>{esc('nano_graph_first_ablation_results.json')}</code>",
            "Relation queries should not stop at co-mentioned text; explicit path grounding improves reliability.",
        )
    )
    rows.append(
        section_row(
            "Relational path grounding contract",
            "Compare graph+fact vs graph+fact+path_grounding policies.",
            "Graph seed + fact support can still miss the actual relational path.",
            (
                "<code>graph_fact_only</code>: contract_ok=True but has_path_grounding=False"
                "<br><code>path_grounded_relational</code>: contract_ok=True and has_path_grounding=True"
            ),
            "Relation answers should prefer graph evidence with explicit path traces, not just nearby entity/fact co-occurrence.",
        )
    )
    rows.append(
        section_row(
            "Longitudinal middle layer",
            "Compare overview_only, atom_only, topic_dossier, contract_topic_dossier on 6 generic mixed-topic longitudinal cases.",
            (
                f"Overview {dossier_general['overview_only']['correct']}/{dossier_general['overview_only']['total']}, "
                f"atom {dossier_general['atom_only']['correct']}/{dossier_general['atom_only']['total']}, "
                f"dossier {dossier_general['topic_dossier']['correct']}/{dossier_general['topic_dossier']['total']}."
            ),
            f"<code>{esc('nano_topic_dossier_generalization_benchmark_results.json')}</code>",
            "A topic-centered middle layer improves cross-session progress questions without dataset-specific keyword maps.",
        )
    )
    rows.append(
        section_row(
            "Answer-time self-check",
            "Compare baseline retrieval vs dual-backbone retrieval with answer-time self-check on 8 cases.",
            (
                f"Baseline {selfcheck['baseline_correct']}/{selfcheck['cases']} "
                f"({pct(selfcheck['baseline_correct'], selfcheck['cases'])}) -> "
                f"self-check {selfcheck['selfcheck_correct']}/{selfcheck['cases']} "
                f"({pct(selfcheck['selfcheck_correct'], selfcheck['cases'])})."
            ),
            f"<code>{esc('nano_dual_backbone_selfcheck_v2_results.json')}</code>",
            "Retrieval should not end at the first plausible hit; answer-time inspection closes real evidence gaps.",
        )
    )
    rows.append(
        section_row(
            "Coverage-aware gating",
            "Compare confidence-only stopping vs coverage-aware stopping on 6 mixed-family cases.",
            (
                f"Keyword correctness stays {coverage['confidence_only_keyword_ok']}/{coverage['cases']} "
                f"for both, but contract completion improves "
                f"{coverage['confidence_only_contract_ok']}/{coverage['cases']} -> "
                f"{coverage['coverage_aware_contract_ok']}/{coverage['cases']}."
            ),
            f"<code>{esc('nano_coverage_aware_gating_ablation_results.json')}</code>",
            "Stopping based only on confidence is too weak; retrieval should look at whether the planned evidence contract is complete.",
        )
    )
    rows.append(
        section_row(
            "Ordered second pass",
            "Compare one-pass, graph-only second pass, and type-aware second pass on 5 cases.",
            (
                f"One-pass {type_aware['one_pass_contract_ok']}/{type_aware['cases']}, "
                f"graph-only {type_aware['graph_only_contract_ok']}/{type_aware['cases']}, "
                f"type-aware {type_aware['type_aware_contract_ok']}/{type_aware['cases']}."
            ),
            f"<code>{esc('nano_type_aware_second_pass_ablation_results.json')}</code>",
            "Second pass should be keyed by missing evidence types, not hard-coded to always add graph evidence.",
        )
    )
    rows.append(
        section_row(
            "Graph second pass on temporal tasks",
            "Compare one-pass vs graph second pass on 6 temporal/temporal-relational cases.",
            (
                f"Contract completion {second_pass['one_pass_contract_ok']}/{second_pass['cases']} -> "
                f"{second_pass['graph_second_pass_contract_ok']}/{second_pass['cases']}, "
                f"while keyword success stays {second_pass['graph_second_pass_keyword_ok']}/{second_pass['cases']}."
            ),
            f"<code>{esc('nano_graph_second_pass_contract_ablation_results.json')}</code>",
            "Many failures are hidden by keyword-level correctness; explicit supporting evidence materially changes answer trustworthiness.",
        )
    )
    rows.append(
        section_row(
            "Multimodal evidence contract",
            "Compare one-pass multimodal retrieval vs contract-aware multimodal retrieval on 5 visual cases.",
            (
                f"Contract completion {multimodal['one_pass_contract_ok']}/{multimodal['cases']} -> "
                f"{multimodal['contract_aware_contract_ok']}/{multimodal['cases']}."
            ),
            f"<code>{esc('nano_multimodal_contract_ablation_results.json')}</code>",
            "Image evidence should be a first-class memory object, then be grounded by event/entity/fact support when the query requires it.",
        )
    )
    rows.append(
        section_row(
            "Visual evidence typing",
            "Compare fact-only policy vs visual contract policy.",
            "Fact text can carry OCR strings, but still does not mean the system used first-class image evidence.",
            (
                "<code>generic_fact_only</code>: required=['fact']"
                "<br><code>visual_contract</code>: required=['image_evidence','fact']"
            ),
            "A visual answer should remain auditable as visual evidence, not collapse into plain text memory.",
        )
    )
    rows.append(
        section_row(
            "Readiness / lifecycle",
            "Compare baseline, temporal_graph, and full pipeline on 5 readiness-sensitive cases.",
            (
                f"Baseline {readiness['baseline']['correct']}/{readiness['baseline']['total']}, "
                f"temporal_graph {readiness['temporal_graph']['correct']}/{readiness['temporal_graph']['total']}, "
                f"full {readiness['full']['correct']}/{readiness['full']['total']}."
            ),
            f"<code>{esc('nano_readiness_ablation_results.json')}</code>",
            "Persisted does not imply answerable; readiness is a real control plane, not just a bookkeeping field.",
        )
    )

    per_family_rows = []
    for row in dual_benchmark["per_family"]:
        per_family_rows.append(
            "<tr>"
            f"<td>{esc(row['family'])}</td>"
            f"<td>{row['cases']}</td>"
            f"<td>{row['tree_only_passed']}</td>"
            f"<td>{row['graph_only_passed']}</td>"
            f"<td>{row['dual_passed']}</td>"
            "</tr>"
        )

    html_text = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory MM Family Benchmark Panel</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --line:#dbe3ee; --text:#172235; --muted:#607286;
      --blue:#245cff; --blue-soft:#eef4ff; --green:#0f8c60; --green-soft:#edf9f3;
      --amber:#9a6100; --amber-soft:#fff7e8; --shadow:0 12px 30px rgba(15,28,45,.08);
    }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
    .page{{max-width:1240px;margin:0 auto;padding:24px 18px 56px}}
    .hero,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}}
    .hero{{padding:26px;margin-bottom:16px;background:linear-gradient(135deg,#fff 0%,#eef4ff 100%)}}
    .panel{{padding:18px;margin-bottom:16px}}
    h1,h2,h3{{margin:0 0 10px;line-height:1.28}}
    h1{{font-size:30px}} h2{{font-size:21px;padding-bottom:8px;border-bottom:1px solid var(--line)}} h3{{font-size:16px}}
    p{{margin:8px 0}} ul{{margin:8px 0 0 18px;padding:0}} li{{margin:6px 0}}
    .muted{{color:var(--muted)}}
    .chips{{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}}
    .chip{{display:inline-block;padding:4px 10px;border-radius:999px;border:1px solid #cfdaee;background:#f8fbff;color:#2b4d7a;font-size:12px;font-weight:700}}
    .metric-row{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:14px}}
    .metric{{border:1px solid var(--line);border-radius:10px;padding:14px;background:#fff}}
    .metric .big{{margin-top:4px;font-size:24px;font-weight:700}}
    .callout{{margin-top:14px;padding:12px 14px;border-left:4px solid var(--blue);background:#f4f8ff;border-radius:8px}}
    .grid{{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:14px}}
    .span-6{{grid-column:span 6}} .span-12{{grid-column:span 12}}
    table{{width:100%;border-collapse:collapse;margin-top:10px}}
    th,td{{border:1px solid var(--line);padding:10px;text-align:left;vertical-align:top}}
    th{{background:#f4f7fd}}
    code{{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;background:#f4f7fb;border:1px solid #dde6f2;border-radius:4px;padding:1px 5px;font-size:12px;word-break:break-all}}
    @media (max-width:980px){{.span-6{{grid-column:span 12}} .metric-row{{grid-template-columns:1fr 1fr}}}}
    @media (max-width:680px){{.page{{padding:14px 12px 36px}} .metric-row{{grid-template-columns:1fr}} h1{{font-size:24px}}}}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>EchoMemory-MM Family Benchmark Panel</h1>
      <p class="muted">
        这页不是再造一个新 benchmark，而是把已经完成的泛化 nano 实验收成一张更像论文实验节的 panel。
        核心目标是回答：<b>当前 EchoMemory 这条路线到底验证了哪些能力族，哪些还只是方向性，哪些仍然是明显缺口。</b>
      </p>
      <div class="chips">
        <span class="chip">temporal</span>
        <span class="chip">relational</span>
        <span class="chip">longitudinal</span>
        <span class="chip">visual</span>
        <span class="chip">readiness</span>
      </div>
      <div class="metric-row">
        <div class="metric"><div class="muted">three-clock</div><div class="big">{three_clock['three_clock_passed']}/{three_clock['cases']}</div></div>
        <div class="metric"><div class="muted">dual-backbone</div><div class="big">{dual_benchmark['dual_passed']}/{dual_benchmark['cases']}</div></div>
        <div class="metric"><div class="muted">self-check</div><div class="big">{selfcheck['selfcheck_correct']}/{selfcheck['cases']}</div></div>
        <div class="metric"><div class="muted">multimodal contract</div><div class="big">{multimodal['contract_aware_contract_ok']}/{multimodal['cases']}</div></div>
      </div>
      <div class="callout">
        当前最可信的结论不是“我们已经赢了一个大 benchmark”，而是：
        <b>三时钟时间、双主干检索、topic dossier 中层、contract-aware second pass、以及 image evidence typing 这几条结构性改动，已经在多个泛化 family 上形成一致收益。</b>
      </div>
    </section>

    <section class="panel">
      <h2>1. 家族级实验总表</h2>
      <table>
        <thead>
          <tr>
            <th style="width:16%">能力族 / 机制</th>
            <th style="width:20%">实验设置</th>
            <th style="width:19%">主要发现</th>
            <th style="width:20%">证据</th>
            <th>当前最稳妥的论文说法</th>
          </tr>
        </thead>
        <tbody>
          {"".join(rows)}
        </tbody>
      </table>
    </section>

    <div class="grid">
      <section class="panel span-6">
        <h2>2. Dual-backbone 分 family 结果</h2>
        <table>
          <thead>
            <tr>
              <th>Family</th>
              <th>Cases</th>
              <th>Tree-only</th>
              <th>Graph-only</th>
              <th>Dual</th>
            </tr>
          </thead>
          <tbody>
            {"".join(per_family_rows)}
          </tbody>
        </table>
        <p class="muted">
          这里最重要的一点不是 dual 永远绝对最好，而是它更稳定地覆盖不同 family。
          时间题主要吃 tree，关系题主要吃 graph，mixed family 靠两者配合。
        </p>
      </section>

      <section class="panel span-6">
        <h2>3. 当前可以写进论文的创新点</h2>
        <ul>
          <li><b>Three-clock temporal semantics</b>：把 event / mention / write 明确分开，而不是只留一个 created_at。</li>
          <li><b>Dual-backbone memory retrieval</b>：temporal tree 和 relation graph 不是同一类结构，应该并列成为主干。</li>
          <li><b>Topic dossier middle layer</b>：overview 太粗、atom 太碎，中间需要 topic-centered dossier 来做 cross-session progress recall。</li>
          <li><b>Contract-aware answer policy</b>：coverage、self-check、second pass 不是调参细节，而是 correctness control plane。</li>
          <li><b>First-class image evidence</b>：视觉证据不应退化成 OCR 文本，需要在 memory graph 中保持类型身份。</li>
          <li><b>Readiness as a real lifecycle state</b>：persisted 不等于 answerable，QA gate 应受 readiness receipt 约束。</li>
        </ul>
      </section>
    </div>

    <section class="panel">
      <h2>4. 还不能过度声称的地方</h2>
      <ul>
        <li>这些证据已经足够支持 <b>机制级贡献</b> 和 <b>代码级可实现性</b>，但还不足以支撑“大 benchmark 全面领先”的强结论。</li>
        <li>topic dossier 目前更像一个很有希望的中层结构，还需要更强的主题归一和跨 session 合并策略。</li>
        <li>contract-aware second pass 方向已经成立，但 learned routing / learned write governance 还没正式做。</li>
        <li>多模态线现在更像 CVPR 方向的强雏形，下一步要补更完整的 visual-memory benchmark 和真实模型实验。</li>
      </ul>
      <div class="callout">
        如果现在要写论文，最合适的姿态是：<b>code-backed mechanism paper</b>。
        亮点是结构与机制，而不是把自己包装成一个已经在所有评测上封神的成品系统。
      </div>
    </section>

    <section class="panel">
      <h2>5. 源结果文件</h2>
      <ul>
        <li><code>/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_three_clock_temporal_ablation_results.json</code></li>
        <li><code>/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_dual_backbone_benchmark_results.json</code></li>
        <li><code>/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_dual_backbone_selfcheck_v2_results.json</code></li>
        <li><code>/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_topic_dossier_generalization_benchmark_results.json</code></li>
        <li><code>/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_coverage_aware_gating_ablation_results.json</code></li>
        <li><code>/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_graph_second_pass_contract_ablation_results.json</code></li>
        <li><code>/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_type_aware_second_pass_ablation_results.json</code></li>
        <li><code>/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_multimodal_contract_ablation_results.json</code></li>
        <li><code>/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_readiness_ablation_results.json</code></li>
        <li><code>/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_graph_first_ablation_results.json</code></li>
      </ul>
    </section>
  </div>
</body>
</html>
"""

    OUT_HTML.write_text(html_text)
    print(OUT_HTML)


if __name__ == "__main__":
    main()
