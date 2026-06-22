#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


OUT = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_cvpr_experiment_roadmap_v19_20260617.html"
)


def main() -> None:
    html = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory CVPR Experiment Roadmap v19</title>
  <style>
    :root{--bg:#f6f8fc;--panel:#fff;--line:#dbe3ee;--text:#18212f;--muted:#617184;--blue:#2563eb;--shadow:0 10px 24px rgba(15,23,42,.08)}
    *{box-sizing:border-box}
    body{margin:0;background:var(--bg);color:var(--text);font:14px/1.74 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}
    .wrap{max-width:1180px;margin:0 auto;padding:28px 20px 54px}
    .hero,.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}
    .hero{padding:26px 28px;margin-bottom:16px;background:linear-gradient(135deg,#fff 0%,#eef4ff 100%)}
    .card{padding:18px 20px;margin-bottom:16px}
    h1,h2,h3{margin:0 0 12px;line-height:1.25}
    h1{font-size:31px} h2{font-size:21px} h3{font-size:16px}
    p{margin:0 0 10px}
    ul{margin:8px 0 0 18px;padding:0}
    li{margin:6px 0}
    table{width:100%;border-collapse:collapse;margin-top:10px;font-size:13px}
    th,td{border-top:1px solid var(--line);padding:10px 8px;text-align:left;vertical-align:top}
    th{background:#fbfcfe;color:var(--muted);font-size:12px}
    code{background:#f3f6fb;border:1px solid #e5eaf2;border-radius:6px;padding:1px 4px;font:12px ui-monospace,SFMono-Regular,Menlo,monospace}
    .muted{color:var(--muted)}
    .note{margin-top:12px;padding:12px 14px;border-left:4px solid var(--blue);background:#f4f8ff;border-radius:8px}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory CVPR Experiment Roadmap v19</h1>
      <p class="muted">
        这页只回答一个问题：接下来最值得补什么实验，既能提升论文说服力，又不靠 LoCoMo 之类 benchmark 的关键词特化。
      </p>
      <div class="note">
        总原则：继续做 <b>结构型、机制型、可泛化</b> 的实验。优先证明 memory schema、planner、contract、gate 的价值，
        而不是为了某个数据集把 query surface 做 hardcode。
      </div>
    </section>

    <section class="card">
      <h2>优先级总表</h2>
      <table>
        <thead>
          <tr>
            <th>Priority</th>
            <th>Experiment</th>
            <th>What to vary</th>
            <th>Main hypothesis</th>
            <th>Closest code/nano target</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>P0</td>
            <td>Executive answerability gate</td>
            <td>advisory / expand-only / abstain+expand / full executive</td>
            <td>self-check 只有诊断不够，必须能执行 expand / abstain / defer。</td>
            <td><code>self_check.py</code>, <code>answerability_gate.py</code>, <code>nano_selfcheck_executor_ablation.py</code></td>
          </tr>
          <tr>
            <td>P0</td>
            <td>Three-clock + interval time</td>
            <td>write-only / story+mention / three-clock / three-clock+interval</td>
            <td>时间题的核心收益来自时间语义，而不是 query rewrite 小补丁。</td>
            <td><code>temporal/query_resolver.py</code>, <code>topic_dossier</code>, temporal nano</td>
          </tr>
          <tr>
            <td>P0</td>
            <td>Topic dossier as semantic tree</td>
            <td>flat atoms / dossier / dossier+canonicalization / dossier+state-delta</td>
            <td>cross-session / latest-state / process-evolution 题，需要稳定中层而不是 overview 拼大段。</td>
            <td><code>organized_projector/projector.py</code>, topic-dossier nano line</td>
          </tr>
          <tr>
            <td>P1</td>
            <td>Path-grounded graph backbone</td>
            <td>vector-first / graph-second-pass / graph-first / graph-first+typed path</td>
            <td>关系题和多跳题最好走图主干，不只是拿图补证据。</td>
            <td><code>graph_seed_planner.py</code>, graph/path nano line</td>
          </tr>
          <tr>
            <td>P1</td>
            <td>Fragment-then-compose</td>
            <td>summary-first / fragment-only / fragment+slot-compose</td>
            <td>细节题和时间题掉分常来自 summary 抹平；slot-aware compose 会更稳。</td>
            <td><code>evidence_composer.py</code>, search/fusion bridge</td>
          </tr>
          <tr>
            <td>P1</td>
            <td>Visual ingest main line</td>
            <td>no visual ingest / OCR only / image_evidence / image_evidence+owner/event/fact link</td>
            <td>如果冲 CVPR，多模态主线必须证明写入侧结构化带来的收益。</td>
            <td><code>resource_service.py</code>, <code>graph/sync.py</code>, <code>nano_visual_ingest_bridge.py</code></td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="card">
      <h2>最值得先补的两条主线</h2>
      <h3>主线 A：time + topic + graph + gate</h3>
      <ul>
        <li>这是当前最完整、最可信的 systems 论文主线。</li>
        <li>已经有：统一 nano、结构消融、real-code bridge、主稿叙事。</li>
        <li>还缺：更强的主代码 bridge，证明 executive gate 和更强 time/topic schema 不只是 nano 里成立。</li>
      </ul>
      <h3>主线 B：visual ingest -&gt; image_evidence -&gt; grounded answerability</h3>
      <ul>
        <li>这是更像 CVPR 的主线。</li>
        <li>已经有：visual-ingest nano bridge 和 graph-backed image_evidence 叙事。</li>
        <li>还缺：更真实的 write-side multimodal structuring 证据，最好能在主代码或更强 prototype 里落地。</li>
      </ul>
    </section>

    <section class="card">
      <h2>实验设计原则</h2>
      <ul>
        <li>不要用 benchmark-specific keyword tables 决定 planner family。</li>
        <li>优先比较 memory plane、time schema、path grounding、answerability decision，而不是 prompt wording 小差异。</li>
        <li>每个实验都尽量回答“这个结构到底值不值”这个问题。</li>
        <li>能在 nano 里先证明机制，再去主代码做 bridge，是目前最稳的路线。</li>
      </ul>
    </section>
  </div>
</body>
</html>
"""
    OUT.write_text(html, encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
