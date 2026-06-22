#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


OUT = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_mm_submission_package_home_v18_20260617.html"
)


def main() -> None:
    html = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory-MM Submission Package v18</title>
  <style>
    :root{
      --bg:#f6f8fc;--panel:#fff;--line:#dbe3ee;--text:#18212f;--muted:#617184;--blue:#2563eb;
      --blue-soft:#eef4ff;--green:#0f766e;--amber:#b45309;--shadow:0 12px 28px rgba(15,23,42,.08);
    }
    *{box-sizing:border-box}
    body{margin:0;background:var(--bg);color:var(--text);font:14px/1.74 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}
    .wrap{max-width:1240px;margin:0 auto;padding:28px 20px 54px}
    .hero,.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}
    .hero{padding:26px 28px;margin-bottom:16px;background:linear-gradient(135deg,#fff 0%,#eef4ff 100%)}
    .card{padding:18px 20px;margin-bottom:16px}
    .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
    .grid3{display:grid;grid-template-columns:1.1fr 1fr 1fr;gap:16px}
    .kpi{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:14px}
    .kpi>div,.mini{border:1px solid var(--line);border-radius:10px;padding:12px;background:#fbfcfe}
    .kpi strong{display:block;font-size:22px;margin-bottom:4px}
    h1,h2,h3{margin:0 0 12px;line-height:1.25}
    h1{font-size:31px} h2{font-size:21px} h3{font-size:16px}
    p{margin:0 0 10px}
    ul{margin:8px 0 0 18px;padding:0}
    li{margin:6px 0}
    a{color:var(--blue);text-decoration:none}
    a:hover{text-decoration:underline}
    .muted{color:var(--muted)}
    .tag{display:inline-block;padding:4px 10px;border-radius:999px;background:var(--blue-soft);color:var(--blue);font-size:12px;font-weight:700;margin-right:8px}
    .callout{margin-top:12px;padding:12px 14px;border-left:4px solid var(--blue);background:#f4f8ff;border-radius:8px}
    .good{color:var(--green)}
    .warn{color:var(--amber)}
    code{background:#f3f6fb;border:1px solid #e5eaf2;border-radius:6px;padding:1px 4px;font:12px ui-monospace,SFMono-Regular,Menlo,monospace}
    @media (max-width: 980px){.grid,.grid3,.kpi{grid-template-columns:1fr}}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div>
        <span class="tag">submission package</span>
        <span class="tag">v18</span>
        <span class="tag">code-grounded</span>
      </div>
      <h1>EchoMemory-MM Submission Package v18</h1>
      <p class="muted">
        这页是当前最推荐的入口。它把主论文、30 篇论文映射、真实代码结构、关键 nano 实验、
        以及下一步实验路线压到一个地方，方便继续写稿、补实验、或者交给别人一起推进。
      </p>
      <div class="kpi">
        <div><strong>30</strong><span class="muted">recent papers mapped</span></div>
        <div><strong>6/6</strong><span class="muted">typed gate unified nano</span></div>
        <div><strong>21/21</strong><span class="muted">real-code family subset</span></div>
        <div><strong>0</strong><span class="muted">dataset keyword hacks recommended</span></div>
      </div>
      <div class="callout">
        当前最重要的新结论是两句：第一，<b>three-clock time</b> 和 <b>topic dossier</b> 已经能在统一消融里解释大部分收益；
        第二，answerability 真正有用的前提不是“更严格”，而是 <b>family-aware + typed + executable</b>。
      </div>
    </section>

    <section class="grid3">
      <div class="mini">
        <h3>从哪里开始看</h3>
        <ul>
          <li><a href="/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_mm_cvpr_main_submission_draft_v18_20260617.html">主论文 v18 HTML</a></li>
          <li><a href="/Users/chx/locomo-eval-web/docs/echomemory_mm_cvpr_main_submission_draft_v18_20260617.md">主论文 v18 Markdown</a></li>
          <li><a href="/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_submission_board_20260617.html">一页式 submission board</a></li>
        </ul>
      </div>
      <div class="mini">
        <h3>这版新增什么</h3>
        <ul>
          <li>统一结构消融并入主稿</li>
          <li>v14 / v17 answerability seam 说明</li>
          <li>更贴真实代码的 top10 论文分析</li>
        </ul>
      </div>
      <div class="mini">
        <h3>建议阅读顺序</h3>
        <ul>
          <li>先看主稿和 submission board</li>
          <li>再看结构 top10 与 related work</li>
          <li>最后看 nano 与 unified ablation</li>
        </ul>
      </div>
    </section>

    <section class="grid">
      <div class="card">
        <h2>Main Paper</h2>
        <ul>
          <li><a href="/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_mm_cvpr_main_submission_draft_v18_20260617.html">main submission draft v18</a></li>
          <li><a href="/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_mm_unified_results_panel_20260617.html">unified results panel</a></li>
          <li><a href="/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_mm_method_figure_20260617.html">method figure</a></li>
          <li><a href="/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_reviewer_faq_20260617.html">reviewer FAQ</a></li>
        </ul>
      </div>
      <div class="card">
        <h2>Structure + Papers</h2>
        <ul>
          <li><a href="/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_structure_30paper_strict_20260617.html">30-paper strict roadmap</a></li>
          <li><a href="/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_cvpr_related_work_table_20260617.html">CVPR related work table</a></li>
          <li><a href="/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_structure_top10_codegrounded_20260617c.html">top10 code-grounded analysis</a></li>
          <li><a href="/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_recent30_related_work_matrix_20260617.html">recent30 related-work matrix</a></li>
        </ul>
      </div>
    </section>

    <section class="grid">
      <div class="card">
        <h2>Nano + Ablations</h2>
        <ul>
          <li><a href="/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_reference_impl_v17.py">nano reference v17 source</a></li>
          <li><a href="/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_reference_impl_v17_20260617.html">nano reference v17 HTML</a></li>
          <li><a href="/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_stack_walkthrough_20260617.html">nano stack walkthrough</a></li>
          <li><a href="/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_unified_structure_ablation_20260617.html">unified structure ablation</a></li>
          <li><a href="/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_interval_temporal_arbitration_ablation_20260617.html">interval temporal arbitration ablation</a></li>
          <li><a href="/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_selfcheck_executor_ablation_20260617.html">self-check executor ablation</a></li>
          <li><a href="/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_visual_ingest_bridge_20260617.html">visual-ingest bridge</a></li>
        </ul>
      </div>
      <div class="card">
        <h2>Code Bridges</h2>
        <ul>
          <li><a href="/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_realcode_bridge_20260617.html">real-code bridge</a></li>
          <li><a href="/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_realcode_bridge_summary_20260617.html">real-code bridge summary</a></li>
          <li><a href="/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_answerability_seam_v14_vs_v17_20260617.html">answerability seam: v14 vs v17</a></li>
          <li><a href="/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_selfcheck_policy_note_20260617.html">self-check policy note</a></li>
          <li><a href="/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_maincode_family_aware_readiness_bridge_20260617.html">family-aware readiness bridge</a></li>
        </ul>
      </div>
    </section>

    <section class="card">
      <h2>Next Experiments</h2>
      <p class="muted">
        下面这页是接下来最值得做的一轮实验路线，不依赖 benchmark 关键词 patch，而是继续强化通用结构。
      </p>
      <ul>
        <li><a href="/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_cvpr_experiment_roadmap_v19_20260617.html">CVPR experiment roadmap v19</a></li>
      </ul>
      <p><span class="good"><b>已经比较强：</b></span> 结构分析、统一消融、real-code bridge、nano 解释。</p>
      <p><span class="warn"><b>还要继续补：</b></span> 多模态主线、执行型 gate 的主代码桥接、以及更完整的 benchmark-scale 证据。</p>
    </section>
  </div>
</body>
</html>
"""
    OUT.write_text(html, encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
