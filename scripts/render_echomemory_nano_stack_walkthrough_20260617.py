#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_stack_walkthrough_20260617.html"
)


def render() -> str:
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Nano Stack Walkthrough</title>
  <style>
    :root{
      --bg:#f6f8fc; --panel:#fff; --line:#dbe3ee; --text:#18212f; --muted:#617184; --blue:#2563eb; --blue-soft:#eef4ff;
    }
    *{box-sizing:border-box}
    body{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}
    .wrap{max-width:1240px;margin:0 auto;padding:28px 20px 56px}
    .hero,.card,.step{background:var(--panel);border:1px solid var(--line);border-radius:12px}
    .hero,.card{padding:22px 24px;margin-bottom:16px}
    .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
    .stack{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin-top:14px}
    .node{padding:14px 12px;border-radius:10px;background:#fbfcfe;border:1px solid var(--line);text-align:center}
    .node b{display:block;font-size:15px;margin-bottom:4px}
    .step{padding:16px}
    h1,h2,h3{margin:0 0 12px;line-height:1.25}
    h1{font-size:30px}
    h2{font-size:21px}
    h3{font-size:16px}
    p{margin:0 0 10px}
    ul{margin:8px 0 0 18px;padding:0}
    li{margin:6px 0}
    .muted{color:var(--muted)}
    code{background:#f3f6fb;border:1px solid #e5eaf2;border-radius:6px;padding:1px 4px;font:12px ui-monospace,SFMono-Regular,Menlo,monospace}
    .callout{margin-top:12px;padding:12px 14px;border-left:4px solid var(--blue);background:var(--blue-soft);border-radius:8px}
    @media (max-width: 1080px){.stack{grid-template-columns:repeat(3,minmax(0,1fr));}}
    @media (max-width: 920px){.grid,.stack{grid-template-columns:1fr;}}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory Nano Stack Walkthrough</h1>
      <p class="muted">
        这一页是给“想快速弄懂 EchoMemory 方法骨架”的人看的。不是讲所有实现细节，而是把 nano 参考实现拆成 6 个稳定层。
      </p>
      <div class="stack">
        <div class="node"><b>1. Stream</b><span>append-only observations</span></div>
        <div class="node"><b>2. Atom</b><span>three-clock atomic memory</span></div>
        <div class="node"><b>3. Dossier</b><span>topic-centered middle layer</span></div>
        <div class="node"><b>4. Tree</b><span>chronology retrieval surface</span></div>
        <div class="node"><b>5. Graph</b><span>relation + visual backbone</span></div>
        <div class="node"><b>6. Gate</b><span>readiness + answerability</span></div>
      </div>
      <div class="callout">
        最重要的一点：这个 nano 版本不是“为了某个数据集拼凑规则”，而是把长期记忆系统拆成几个泛化性的结构问题。
      </div>
    </section>

    <section class="grid">
      <div class="step">
        <h3>1. Stream layer</h3>
        <p>系统起点不是“已经整理好的记忆”，而是 observation stream。对应 nano 里的 <code>append_text()</code> 和 <code>append_image()</code>。</p>
        <ul>
          <li>这样写时与读时分离。</li>
          <li>后面能做 deferred consolidation。</li>
        </ul>
      </div>
      <div class="step">
        <h3>2. Atom layer</h3>
        <p>每条 observation 先被压成 atom，且显式保留 <code>story_time</code>、<code>mention_time</code>、<code>write_time</code>。</p>
        <ul>
          <li>这是时间题泛化的前提。</li>
          <li>对应真实仓库里的 <code>atom_first_pipeline.py</code>。</li>
        </ul>
      </div>
      <div class="step">
        <h3>3. Topic dossier layer</h3>
        <p><code>topic_dossier</code> 处在 overview 和 flat atoms 之间，专门扛 longitudinal / evolution / status 题。</p>
        <ul>
          <li>没有这层，系统只能在“太粗”和“太碎”之间摇摆。</li>
          <li>对应真实仓库里的 <code>organized_projector/projector.py</code>。</li>
        </ul>
      </div>
      <div class="step">
        <h3>4. Temporal tree layer</h3>
        <p>时间不是 metadata 附件，而是独立 retrieval surface。nano 里用 year / month / day block，真实仓库里对应 <code>temporal_tree</code>。</p>
        <ul>
          <li>适合 chronology-heavy 问题。</li>
          <li>也是 three-clock 设计的自然消费层。</li>
        </ul>
      </div>
      <div class="step">
        <h3>5. Graph layer</h3>
        <p>graph 负责 relation-heavy 和 visual-heavy 问题，不是 summary 检索失败后的补丁。</p>
        <ul>
          <li>event / fact / entity / image_evidence 都应该进图。</li>
          <li>未来重点是 path grounding 和 typed edges。</li>
        </ul>
      </div>
      <div class="step">
        <h3>6. Gate layer</h3>
        <p>最后不是“检索完就答”，而是过两道门：<code>readiness</code> 和 <code>answerability</code>。</p>
        <ul>
          <li>persisted 不等于 ready。</li>
          <li>contract complete 也不等于 answerable。</li>
        </ul>
      </div>
    </section>

    <section class="card">
      <h2>对应 nano 参考实现</h2>
      <ul>
        <li><code>/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_reference_impl_v14.py</code></li>
        <li><code>_extract_atoms()</code>：原子层</li>
        <li><code>_build_dossiers()</code>：主题中层</li>
        <li><code>_build_temporal_tree()</code>：时间树</li>
        <li><code>_build_graph()</code>：图层</li>
        <li><code>plan()</code>：按 query family 选 reader</li>
        <li><code>retrieve()</code>：按缺失证据类型做 second pass</li>
        <li><code>_answerability_ok()</code>：最终答前 gate</li>
      </ul>
    </section>

    <section class="card">
      <h2>一句话理解</h2>
      <p>
        EchoMemory 的 nano 主线可以压成一句：<b>先把流写成带三种时间的原子，再组织成主题中层、时间树和关系图，最后让 query family 和 evidence contract 决定怎么读、能不能答。</b>
      </p>
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
