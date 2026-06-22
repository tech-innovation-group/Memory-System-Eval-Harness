#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_mm_method_figure_20260617.html"
)


def render() -> str:
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory-MM Method Figure</title>
  <style>
    :root{
      --bg:#f5f7fb; --panel:#ffffff; --line:#dbe3ee; --text:#18212f; --muted:#627086;
      --blue:#2563eb; --green:#0f766e; --amber:#b45309; --purple:#7c3aed; --soft:#f8fbff;
    }
    *{box-sizing:border-box}
    body{margin:0;background:var(--bg);color:var(--text);font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}
    .wrap{max-width:1320px;margin:0 auto;padding:28px 20px 56px}
    .hero,.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:22px 24px;margin-bottom:16px}
    h1,h2,h3{margin:0 0 12px;line-height:1.25}
    h1{font-size:30px}
    h2{font-size:21px}
    h3{font-size:15px}
    p,li{font-size:14px}
    .muted{color:var(--muted)}
    .fig{overflow:auto;border:1px solid var(--line);border-radius:12px;background:#fcfdff;padding:18px}
    .grid{display:grid;grid-template-columns:260px 320px 320px 280px;gap:14px;align-items:start;min-width:1220px}
    .col{display:flex;flex-direction:column;gap:12px}
    .node{border:1px solid var(--line);border-radius:10px;padding:14px;background:#fff}
    .node.blue{border-color:#bfdbfe;background:#f8fbff}
    .node.green{border-color:#bbf7d0;background:#f4fdf8}
    .node.amber{border-color:#fed7aa;background:#fffaf4}
    .node.purple{border-color:#ddd6fe;background:#faf8ff}
    .label{display:inline-block;padding:3px 8px;border-radius:999px;font-size:12px;font-weight:600;margin-bottom:8px}
    .blue .label{background:#dbeafe;color:#1d4ed8}
    .green .label{background:#dcfce7;color:#047857}
    .amber .label{background:#ffedd5;color:#b45309}
    .purple .label{background:#ede9fe;color:#6d28d9}
    ul{margin:8px 0 0 18px;padding:0}
    .arrow{display:flex;justify-content:center;align-items:center;color:#4b5563;font-weight:700}
    .legend{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
    .legend>div{border:1px solid var(--line);border-radius:10px;padding:12px;background:#fbfcfe}
    code{background:#f3f6fb;border:1px solid #e5eaf2;border-radius:6px;padding:1px 4px;font:12px ui-monospace,SFMono-Regular,Menlo,monospace}
    @media (max-width: 980px){.legend{grid-template-columns:1fr}}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory-MM 方法架构图</h1>
      <p class="muted">
        这张图把当前论文主张压成一个 reviewer-friendly 方法图：从 append-only stream 出发，经过 atom、middle layer、temporal tree、graph、readiness，
        最后由 planner、evidence contract、self-check 和 answerability gate 决定如何回答。
      </p>
    </section>

    <section class="card">
      <h2>Method Figure</h2>
      <div class="fig">
        <div class="grid">
          <div class="col">
            <div class="node blue">
              <span class="label">Input Stream</span>
              <h3>Append-Only Session Stream</h3>
              <ul>
                <li>user / assistant turns</li>
                <li>text, screenshot, OCR</li>
                <li>write-time anchored</li>
              </ul>
            </div>
            <div class="arrow">↓</div>
            <div class="node blue">
              <span class="label">Ingest</span>
              <h3>Atomic Extraction</h3>
              <ul>
                <li>fact / event / relation</li>
                <li>image evidence</li>
                <li>story_time / mention_time / write_time</li>
              </ul>
              <p><code>workers/atom_first_pipeline.py</code></p>
            </div>
          </div>

          <div class="col">
            <div class="node green">
              <span class="label">Middle Layer</span>
              <h3>Topic Dossier Plane</h3>
              <ul>
                <li>cross-session topic continuity</li>
                <li>progress / status / evolution</li>
                <li>coarse-to-fine entry point</li>
              </ul>
            </div>
            <div class="node green">
              <span class="label">Chronology</span>
              <h3>Temporal Tree</h3>
              <ul>
                <li>story-time oriented blocks</li>
                <li>year / month / day abstraction</li>
                <li>relative-time anchor support</li>
              </ul>
            </div>
            <div class="node green">
              <span class="label">Profile</span>
              <h3>Profile / Overview</h3>
              <ul>
                <li>stable facts and preferences</li>
                <li>global summaries</li>
              </ul>
            </div>
            <p><code>workers/organized_projector/projector.py</code></p>
          </div>

          <div class="col">
            <div class="node amber">
              <span class="label">Structure</span>
              <h3>Relation Graph</h3>
              <ul>
                <li>atom / fact / event / entity nodes</li>
                <li>typed edges: has_fact / about / involves</li>
                <li>path-grounded relational retrieval</li>
              </ul>
            </div>
            <div class="node amber">
              <span class="label">Lifecycle</span>
              <h3>Readiness Plane</h3>
              <ul>
                <li>persisted</li>
                <li>atoms ready</li>
                <li>organized ready</li>
                <li>tree ready</li>
                <li>graph ready</li>
                <li>QA ready</li>
              </ul>
            </div>
            <p><code>index_engine/graph/sync.py</code><br /><code>index_engine/session_service.py</code></p>
          </div>

          <div class="col">
            <div class="node purple">
              <span class="label">Planner</span>
              <h3>Query Family Routing</h3>
              <ul>
                <li>temporal</li>
                <li>relational</li>
                <li>longitudinal</li>
                <li>visual</li>
                <li>readiness</li>
              </ul>
              <p><code>planner/query_planner.py</code></p>
            </div>
            <div class="node purple">
              <span class="label">Policy</span>
              <h3>Evidence Contract</h3>
              <ul>
                <li>required evidence families</li>
                <li>coverage / missing types</li>
                <li>type-aware second pass</li>
              </ul>
              <p><code>policy/evidence_contract.py</code></p>
            </div>
            <div class="node purple">
              <span class="label">Answer-Time</span>
              <h3>Self-Check + Answerability Gate</h3>
              <ul>
                <li>expand supporting evidence</li>
                <li>prefer story-time over mention-time</li>
                <li>abstain when unsupported</li>
              </ul>
              <p><code>policy/self_check.py</code></p>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="card">
      <h2>How To Read This Figure</h2>
      <div class="legend">
        <div>
          <h3>1. Why this is not flat RAG</h3>
          <p>输入流不会直接扔进一个统一的检索池，而是先被结构化成多种 plane。不同问题走不同 plane，这是论文最核心的论点。</p>
        </div>
        <div>
          <h3>2. Why topic dossier matters</h3>
          <p>它位于 overview 和 atom 之间，专门服务于“某个主题后来怎么样了”这类跨 session 问题。</p>
        </div>
        <div>
          <h3>3. Why graph and tree coexist</h3>
          <p>tree 负责 chronology-heavy，graph 负责 relation-heavy / visual。二者不是替代关系，而是双 backbone。</p>
        </div>
        <div>
          <h3>4. Why answer-time policy matters</h3>
          <p>即使 retrieval 看起来够了，也仍可能不 answerable，所以还需要 self-check 和 final gate。</p>
        </div>
      </div>
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
