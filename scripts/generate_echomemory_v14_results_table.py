#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("/Users/chx/locomo-eval-web")
NANO = ROOT / "experiments" / "echomemory_nano"
OUT = ROOT / "web" / "static" / "generated-reports" / "echomemory_mm_v14_results_table_20260616.html"


def load(name: str) -> dict:
    return json.loads((NANO / name).read_text(encoding="utf-8"))


def pct(n: int, d: int) -> str:
    if not d:
        return "0.00%"
    return f"{(100.0 * n / d):.2f}%"


def main() -> None:
    three = load("nano_three_clock_temporal_ablation_results.json")["summary"]
    dual = load("nano_dual_backbone_benchmark_results.json")["summary"]
    selfcheck = load("nano_dual_backbone_selfcheck_v2_results.json")["summary"]
    dossier = load("nano_topic_dossier_generalization_benchmark_results.json")["modes"]
    coverage = load("nano_coverage_aware_gating_ablation_results.json")
    typed = load("nano_type_aware_second_pass_ablation_results.json")["summary"]
    multi = load("nano_multimodal_contract_ablation_results.json")["summary"]
    ready = load("nano_readiness_ablation_results.json")["summary"]
    graph = load("nano_graph_first_ablation_results.json")["summary"]

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory-MM v14 Results Table</title>
  <style>
    :root{{
      --bg:#f6f8fc; --panel:#fff; --text:#18212f; --muted:#617184; --line:#dbe3ee;
      --blue:#2563eb; --green:#0f9f6e; --amber:#b7791f; --red:#b42318; --shadow:0 10px 28px rgba(15,23,42,.08);
      --soft-blue:#eef4ff; --soft-green:#eaf9f3; --soft-amber:#fff7e8; --soft-red:#fff1f1;
    }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Segoe UI",sans-serif}}
    .wrap{{max-width:1200px;margin:0 auto;padding:28px 20px 48px}}
    .hero,.section{{background:var(--panel);border:1px solid var(--line);border-radius:10px;box-shadow:var(--shadow)}}
    .hero{{padding:24px 26px;margin-bottom:16px}}
    .section{{padding:20px 22px;margin-bottom:16px}}
    h1,h2,h3{{margin:0 0 10px;line-height:1.25}}
    h1{{font-size:30px}} h2{{font-size:20px}} h3{{font-size:16px}}
    p{{margin:0 0 10px}}
    .tag{{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12px;margin-right:8px;background:var(--soft-blue);color:var(--blue)}}
    .quote{{border-left:3px solid var(--blue);padding-left:12px;color:var(--muted);margin-top:10px}}
    table{{width:100%;border-collapse:collapse;font-size:13px}}
    th,td{{border-top:1px solid var(--line);text-align:left;vertical-align:top;padding:10px 8px}}
    th{{background:#fbfcfe;color:var(--muted);font-size:12px;text-transform:uppercase}}
    .ok{{color:var(--green);font-weight:700}}
    .warn{{color:var(--amber);font-weight:700}}
    .bad{{color:var(--red);font-weight:700}}
    .muted{{color:var(--muted)}}
    code{{background:#f3f6fb;border-radius:4px;padding:1px 5px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}}
    ul{{margin:8px 0 0 18px;padding:0}} li{{margin:6px 0}}
    .grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}}
    .card{{border:1px solid var(--line);border-radius:10px;padding:14px;background:#fff}}
    @media (max-width:980px){{.grid{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>EchoMemory-MM v14 Results Table</h1>
      <p>这页比统一结果页更短，目标是直接给论文实验节或 rebuttal 使用：只保留最关键的机制级结果、最清楚的 claim、以及最严格的 claim boundary。</p>
      <div style="margin-top:12px;">
        <span class="tag">v14</span>
        <span class="tag">paper-facing</span>
        <span class="tag">concise table</span>
        <span class="tag">source-verified</span>
      </div>
      <div class="quote">
        当前最可信的说法不是“我们已经赢了一个大 benchmark”，而是：
        <b>时间语义、双主干检索、中层 topic dossier、coverage/type-aware policy、以及视觉证据类型化这五条结构性改动，已经在多个 generic family 上形成一致收益。</b>
      </div>
    </div>

    <div class="section">
      <h2>1. Main Results</h2>
      <table>
        <thead>
          <tr>
            <th>Component</th>
            <th>Settings</th>
            <th>Result</th>
            <th>Supports</th>
            <th>Strength</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><b>Three-clock temporal semantics</b></td>
            <td>write-time only vs event+mention split vs three-clock</td>
            <td><code>{three['write_time_only_passed']}/{three['cases']} → {three['event_mention_split_passed']}/{three['cases']} → {three['three_clock_passed']}/{three['cases']}</code></td>
            <td>时间字段不能只留一个 created_at；story time 必须显式保留。</td>
            <td class="ok">Strong</td>
          </tr>
          <tr>
            <td><b>Dual-backbone benchmark</b></td>
            <td>tree-only vs graph-only vs dual</td>
            <td><code>{dual['tree_only_passed']}/{dual['cases']} → {dual['graph_only_passed']}/{dual['cases']} → {dual['dual_passed']}/{dual['cases']}</code></td>
            <td>时间题和关系题不该共用同一 primary route。</td>
            <td class="ok">Strong</td>
          </tr>
          <tr>
            <td><b>Graph-first relational routing</b></td>
            <td>lexical vs graph-first vs graph-path</td>
            <td><code>{graph['lexical_correct']}/{graph['total_cases']} → {graph['graph_first_correct']}/{graph['total_cases']} → {graph['graph_path_correct']}/{graph['total_cases']}</code></td>
            <td>关系题要 path grounding，不是只要共现文本。</td>
            <td class="ok">Strong</td>
          </tr>
          <tr>
            <td><b>Topic dossier middle layer</b></td>
            <td>overview vs atom vs topic dossier</td>
            <td><code>{dossier['overview_only']['correct']}/{dossier['overview_only']['total']} → {dossier['atom_only']['correct']}/{dossier['atom_only']['total']} → {dossier['topic_dossier']['correct']}/{dossier['topic_dossier']['total']}</code></td>
            <td>longitudinal/status questions need a middle layer between overview and flat atoms.</td>
            <td class="ok">Strong</td>
          </tr>
          <tr>
            <td><b>Answer-time self-check</b></td>
            <td>dual-backbone baseline vs self-check v2</td>
            <td><code>{selfcheck['baseline_correct']}/{selfcheck['cases']} → {selfcheck['selfcheck_correct']}/{selfcheck['cases']}</code></td>
            <td>retrieval 之后还需要显式 policy，不然会 answer-too-early。</td>
            <td class="ok">Strong</td>
          </tr>
          <tr>
            <td><b>Coverage-aware gating</b></td>
            <td>confidence-only vs coverage-aware</td>
            <td>keyword ok <code>{coverage['confidence_only_keyword_ok']}/{coverage['cases']} = {coverage['coverage_aware_keyword_ok']}/{coverage['cases']}</code><br>contract ok <code>{coverage['confidence_only_contract_ok']}/{coverage['cases']} → {coverage['coverage_aware_contract_ok']}/{coverage['cases']}</code></td>
            <td>confidence 不等于 evidence sufficiency。</td>
            <td class="warn">Partial but consistent</td>
          </tr>
          <tr>
            <td><b>Type-aware second pass</b></td>
            <td>one-pass vs graph-only retry vs type-aware retry</td>
            <td><code>{typed['one_pass_contract_ok']}/{typed['cases']} → {typed['graph_only_contract_ok']}/{typed['cases']} → {typed['type_aware_contract_ok']}/{typed['cases']}</code></td>
            <td>second pass should follow missing evidence type, not always add graph.</td>
            <td class="ok">Strong</td>
          </tr>
          <tr>
            <td><b>Multimodal contract</b></td>
            <td>one-pass vs contract-aware</td>
            <td><code>{multi['one_pass_contract_ok']}/{multi['cases']} → {multi['contract_aware_contract_ok']}/{multi['cases']}</code></td>
            <td>image evidence should remain typed and structurally grounded.</td>
            <td class="ok">Strong</td>
          </tr>
          <tr>
            <td><b>Readiness</b></td>
            <td>baseline vs temporal_graph vs full</td>
            <td><code>{ready['baseline']['correct']}/{ready['baseline']['total']} → {ready['temporal_graph']['correct']}/{ready['temporal_graph']['total']} → {ready['full']['correct']}/{ready['full']['total']}</code></td>
            <td>persisted is weaker than answerable.</td>
            <td class="ok">Strong</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="section">
      <h2>2. CVPR-facing Reading</h2>
      <div class="grid">
        <div class="card">
          <h3>What is novel enough</h3>
          <ul>
            <li>three-clock time as a memory design constraint</li>
            <li>dual-backbone retrieval with a topic-centered middle layer</li>
            <li>shared evidence contract spanning gating, self-check, and second pass</li>
            <li>first-class image evidence typing</li>
          </ul>
        </div>
        <div class="card">
          <h3>What is already backed</h3>
          <ul>
            <li>generic nano evidence across multiple query families</li>
            <li>real-code bridge evidence on the current SearchService</li>
            <li>code-grounded 30-paper mapping</li>
            <li>family-level experiment panel</li>
          </ul>
        </div>
        <div class="card">
          <h3>What is still weak</h3>
          <ul>
            <li>benchmark-scale tables on LoCoMo / LongMemEval</li>
            <li>large multimodal evaluation line</li>
            <li>deployment-grade latency / cost study</li>
            <li>learned routing or learned write governance</li>
          </ul>
        </div>
      </div>
    </div>

    <div class="section">
      <h2>3. Claim Boundary</h2>
      <ul>
        <li><b>Can claim:</b> a code-backed mechanism paper with multiple converging evidence lines.</li>
        <li><b>Cannot claim yet:</b> benchmark-scale superiority or a finalized CVPR empirical package.</li>
        <li><b>Best current label:</b> <code>paper-shaped, code-backed, benchmark-incomplete</code>.</li>
      </ul>
    </div>
  </div>
</body>
</html>
"""
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
