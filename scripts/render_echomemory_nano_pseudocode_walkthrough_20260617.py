#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_pseudocode_walkthrough_20260617.html"
)


def render() -> str:
    pseudocode = """
Algorithm 1 EchoMemory-MM Nano Reference
Input: append-only observations O
Output: answerable memory state M and query-time answer function A

1:  append observations into stream O
2:  for each observation o in O do
3:      extract atomic memories a with
4:          atom_type, entities, story_time, mention_time, write_time
5:  end for
6:  group atoms into topic dossiers D
7:      first use topic hints if available
8:      otherwise induce generic topic groups from entities + lexical signatures
9:  build temporal tree T from atom story_time
10: build relation graph G from event/fact/entity/image nodes
11: compute readiness R =
12:     persisted ∧ atoms_ready ∧ dossier_ready ∧ tree_ready ∧ graph_ready
13:
14: function PLAN(query q):
15:     route q into one family:
16:         temporal / relational / longitudinal / visual / readiness / general
17:     choose one primary reader and supporting readers
18:     declare required evidence contract C
19:
20: function RETRIEVE(query q, query_time τ):
21:     p ← PLAN(q)
22:     H ← primary_reader(p)
23:     present ← evidence_layers(H)
24:     missing ← required_layers(p) - present
25:     while missing is not empty do
26:         choose supporting reader based on missing evidence type
27:         H ← H ∪ supporting_reader(missing)
28:         recompute present, missing
29:     end while
30:     candidate ← answer_from_hits(H, p)
31:     if readiness not sufficient then return unknown
32:     if answerability gate rejects candidate then return unknown
33:     return candidate
"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Nano Pseudocode Walkthrough</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --line:#dbe3ee; --text:#18212f; --muted:#617184; --blue:#2563eb;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif; }}
    .wrap {{ max-width:1180px; margin:0 auto; padding:28px 20px 54px; }}
    .hero,.card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:22px 24px; margin-bottom:16px; }}
    h1,h2,h3 {{ margin:0 0 12px; line-height:1.25; }}
    h1 {{ font-size:30px; }}
    h2 {{ font-size:21px; }}
    h3 {{ font-size:16px; }}
    p,li {{ font-size:14px; }}
    .muted {{ color:var(--muted); }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }}
    pre {{ margin:0; padding:16px; border:1px solid var(--line); border-radius:10px; background:#fbfcfe; overflow:auto; font:13px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace; }}
    code {{ background:#f3f6fb; border:1px solid #e5eaf2; border-radius:6px; padding:1px 4px; font:12px ui-monospace,SFMono-Regular,Menlo,monospace; }}
    ul {{ margin:8px 0 0 18px; padding:0; }}
    .step {{ border:1px solid var(--line); border-radius:10px; padding:14px; background:#fbfcfe; }}
    @media (max-width: 920px) {{ .grid {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory Nano 伪代码讲解</h1>
      <p class="muted">
        这一页把 <code>nano_reference_impl_v14.py</code> 从“实现源码”再压缩一层，变成更像 appendix algorithm 的读法。
        目标不是替代代码，而是让人先抓住结构，再回头看函数细节。
      </p>
    </section>

    <section class="card">
      <h2>Algorithm View</h2>
      <pre>{pseudocode.strip()}</pre>
    </section>

    <section class="grid">
      <div class="step">
        <h3>Step 1. Stream, not memory object</h3>
        <p>起点不是“已有记忆”，而是 append-only observation stream。这对应 <code>append_text()</code> / <code>append_image()</code>。</p>
      </div>
      <div class="step">
        <h3>Step 2. Atoms carry three clocks</h3>
        <p>每条 observation 先被压成 atom，并显式保留 <code>story_time</code>、<code>mention_time</code>、<code>write_time</code>，对应 <code>_extract_atoms()</code>。</p>
      </div>
      <div class="step">
        <h3>Step 3. Topic dossier is the middle layer</h3>
        <p>中层不是装饰，而是解决 longitudinal / cross-session topic evolution 的关键对象，对应 <code>_build_dossiers()</code>。</p>
      </div>
      <div class="step">
        <h3>Step 4. Temporal tree turns time into a retrieval surface</h3>
        <p>不是把时间塞进 metadata 就算完，而是把 chronology 真正做成可检索结构，对应 <code>_build_temporal_tree()</code>。</p>
      </div>
      <div class="step">
        <h3>Step 5. Graph is not a sidecar</h3>
        <p>graph 承担 relational / visual 主干，而不是文本检索旁边的补丁，对应 <code>_build_graph()</code>。</p>
      </div>
      <div class="step">
        <h3>Step 6. Plan before retrieve</h3>
        <p>query 先分 family，再决定 primary reader 和 required evidence family，对应 <code>plan()</code>。</p>
      </div>
      <div class="step">
        <h3>Step 7. Missing evidence drives second pass</h3>
        <p>second pass 不是固定 graph retry，而是由缺失证据类型决定 reader，对应 <code>retrieve()</code> 和 <code>_reader_for_missing()</code>。</p>
      </div>
      <div class="step">
        <h3>Step 8. Contract complete still may not be answerable</h3>
        <p>最后还要过 <code>_answerability_ok()</code>，因为 <code>contract_ok</code> 不是 <code>answerable=true</code> 的同义词。</p>
      </div>
    </section>

    <section class="card">
      <h2>Code Anchors</h2>
      <ul>
        <li><code>/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_reference_impl_v14.py</code></li>
        <li><code>_extract_atoms()</code> at line region around 261</li>
        <li><code>_build_dossiers()</code> at line region around 305</li>
        <li><code>_build_temporal_tree()</code> at line region around 460</li>
        <li><code>_build_graph()</code> at line region around 481</li>
        <li><code>plan()</code> at line region around 516</li>
        <li><code>retrieve()</code> at line region around 530</li>
        <li><code>_answerability_ok()</code> at line region around 779</li>
      </ul>
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
