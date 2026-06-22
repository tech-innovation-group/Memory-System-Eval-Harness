#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_submission_board_20260617.html"
)


def render() -> str:
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Submission Board</title>
  <style>
    :root{
      --bg:#f6f8fc; --panel:#fff; --line:#dbe3ee; --text:#18212f; --muted:#617184; --blue:#2563eb;
      --green:#0f766e; --amber:#b45309; --red:#b42318;
    }
    *{box-sizing:border-box}
    body{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}
    .wrap{max-width:1320px;margin:0 auto;padding:28px 20px 56px}
    .hero,.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:22px 24px;margin-bottom:16px}
    .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
    .kpi{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:14px}
    .kpi>div,.mini{border:1px solid var(--line);border-radius:10px;padding:12px;background:#fbfcfe}
    .kpi strong{display:block;font-size:22px;margin-bottom:4px}
    h1,h2,h3{margin:0 0 12px;line-height:1.25}
    h1{font-size:30px} h2{font-size:21px} h3{font-size:16px}
    p{margin:0 0 10px}
    ul{margin:8px 0 0 18px;padding:0}
    li{margin:6px 0}
    .muted{color:var(--muted)}
    .row{display:grid;grid-template-columns:1.2fr 1fr 1fr;gap:14px}
    .eyebrow{font-size:12px;font-weight:700;color:var(--blue);text-transform:uppercase;margin-bottom:8px}
    .ok{color:var(--green)} .warn{color:var(--amber)} .bad{color:var(--red)}
    code{background:#f3f6fb;border:1px solid #e5eaf2;border-radius:6px;padding:1px 4px;font:12px ui-monospace,SFMono-Regular,Menlo,monospace}
    @media (max-width: 980px){.grid,.kpi,.row{grid-template-columns:1fr}}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory-MM Submission Board</h1>
      <p class="muted">
        这是一页式投稿总览板，目标是把这篇工作的 thesis、方法、最强证据、主仓 bridge、以及 claim boundary 压到一个 reviewer 友好的入口里。
      </p>
      <div class="kpi">
        <div><strong>30</strong><span class="muted">recent papers mapped</span></div>
        <div><strong>11+</strong><span class="muted">mechanism evidence lines</span></div>
        <div><strong>21/21</strong><span class="muted">real-code family subset pass</span></div>
        <div><strong>2</strong><span class="muted">v17 unified nano + core ablation</span></div>
      </div>
    </section>

    <section class="row">
      <div class="mini">
        <div class="eyebrow">Thesis</div>
        <h3>What this paper claims</h3>
        <p>Long-horizon memory should be modeled as a <b>planner-routed, stream-to-structure system</b> with:</p>
        <ul>
          <li>three-clock time</li>
          <li>topic-centered middle layer</li>
          <li>dual backbone: temporal tree + relation graph</li>
          <li>readiness-aware answerability</li>
          <li>evidence contract + type-aware second pass</li>
        </ul>
      </div>
      <div class="mini">
        <div class="eyebrow">Method</div>
        <h3>Minimal architecture</h3>
        <ul>
          <li><code>stream → atom</code></li>
          <li><code>atom → dossier / tree / graph</code></li>
          <li><code>planner → contract</code></li>
          <li><code>self-check → second pass</code></li>
          <li><code>answerability gate</code></li>
        </ul>
      </div>
      <div class="mini">
        <div class="eyebrow">Boundary</div>
        <h3>What this does not claim</h3>
        <ul>
          <li>not benchmark-scale superiority</li>
          <li>not production multimodal QA proof</li>
          <li>not deployment-grade latency/cost study</li>
          <li>not fully enforced answerability gate in main code</li>
        </ul>
      </div>
    </section>

    <section class="panel">
      <h2>Strongest Evidence</h2>
      <div class="grid">
        <div class="mini">
          <div class="eyebrow">Time</div>
          <p><b>`0/4 -> 4/4 -> 4/4`</b></p>
          <p>Three-clock temporal ablation says time correctness is first a schema problem; the new interval temporal-arbitration ablation then shows that preserved clocks still need before/after/between reasoning.</p>
        </div>
        <div class="mini">
          <div class="eyebrow">Topic</div>
          <p><b>`1/5 -> 3/5 -> 4/5`</b></p>
          <p>Topic dossier ablation says longitudinal questions need a middle layer.</p>
        </div>
        <div class="mini">
          <div class="eyebrow">Backbone</div>
          <p><b>temporal `3/3`; relation `3/3`; dual `8/12`</b></p>
          <p>Temporal tree and graph cover different failure modes.</p>
        </div>
        <div class="mini">
          <div class="eyebrow">Policy</div>
          <p><b>`1/5 -> 5/5`; `4/8 -> 8/8`; `2/6 -> 6/6`</b></p>
          <p>Readiness, self-check, and answerability all materially change correctness.</p>
        </div>
        <div class="mini">
          <div class="eyebrow">Generalization</div>
          <p><b>`8/15 -> 15/15`; `5/5` no-hint</b></p>
          <p>Paraphrase robustness and topic induction support generic routing over dataset cue hacks.</p>
        </div>
        <div class="mini">
          <div class="eyebrow">Real Code</div>
          <p><b>`21/21` family expectation pass</b></p>
          <p>Main code already shows temporal tree, topic dossier, coverage-aware gating, self-check, and typed second pass signals.</p>
        </div>
      </div>
    </section>

    <section class="panel">
      <h2>What Reviewers Can Audit Immediately</h2>
      <div class="grid">
        <div class="mini">
          <h3>Paper-facing</h3>
          <ul>
            <li>main draft v18</li>
            <li>submission package home v18</li>
            <li>unified results panel</li>
            <li>reviewer FAQ / claim boundary</li>
          </ul>
        </div>
        <div class="mini">
          <h3>Method-facing</h3>
          <ul>
            <li>method figure</li>
            <li>mechanism ↔ experiment map</li>
            <li>real-code bridge</li>
            <li>self-check policy note</li>
            <li>answerability seam v14 vs v17</li>
          </ul>
        </div>
        <div class="mini">
          <h3>Implementation-facing</h3>
          <ul>
            <li>nano reference v17 source</li>
            <li>nano stack walkthrough</li>
            <li>unified structure ablation</li>
            <li>topic / answerability / paraphrase benchmarks</li>
          </ul>
        </div>
      </div>
    </section>

    <section class="panel">
      <h2>Current Readiness</h2>
      <p><span class="ok"><b>Already strong:</b></span> architecture analysis, mechanism evidence, nano explanation, real-code bridge, claim boundary discipline.</p>
      <p><span class="warn"><b>Still partial:</b></span> benchmark-scale tables, production multimodal evidence, end-to-end main-code answerability enforcement.</p>
      <p><span class="bad"><b>Not yet proven:</b></span> “this wins LoCoMo / LongMemEval / CVPR-style multimodal benchmarks at scale.”</p>
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
