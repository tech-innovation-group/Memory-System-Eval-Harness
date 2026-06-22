#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


OUT = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_answerability_seam_v14_vs_v17_20260617.html"
)


def main() -> None:
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Answerability Seam: v14 vs v17</title>
  <style>
    body { margin:0; background:#f6f8fc; color:#182333; font:14px/1.75 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif; }
    .wrap { max-width:1040px; margin:0 auto; padding:28px 18px 52px; }
    .hero,.card { background:#fff; border:1px solid #dbe3ee; border-radius:12px; box-shadow:0 12px 28px rgba(15,23,42,.08); }
    .hero { padding:26px 28px; margin-bottom:16px; }
    .card { padding:18px 20px; margin-bottom:16px; }
    h1,h2 { margin:0 0 12px; line-height:1.25; }
    h1 { font-size:30px; }
    h2 { font-size:20px; }
    p,li { margin:0 0 10px; }
    ul { margin:10px 0 0 20px; padding:0; }
    code { background:#f3f6fb; border:1px solid #e2e9f2; border-radius:4px; padding:1px 5px; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; }
    .muted { color:#627286; }
    .grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }
    .note { border-left:4px solid #2563eb; background:#eef4ff; padding:12px 14px; border-radius:8px; }
    @media (max-width: 860px) { .grid { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>Answerability Seam: v14 vs v17</h1>
      <p class="muted">这页只解释一件事：为什么后面做 typed answerability gate 时，不能只说“关掉一个开关”，而要把 gate 重新做成可执行接口。</p>
      <div class="note">
        v14 把 answerability 放在 <code>_answerability_ok(...)</code> 里，v17 的整体实现更统一，但 answer-family 行为更靠近 <code>_answer(...)</code>，所以 reusable seam 变弱了。
      </div>
    </section>

    <section class="grid">
      <div class="card">
        <h2>v14 的特点</h2>
        <ul>
          <li>有单独的 <code>_answerability_ok(query, plan, hits, candidate)</code>。</li>
          <li>更容易把“候选答案生成”和“是否允许回答”分开。</li>
          <li>适合做 ablation：只改 gate，不动其它答题逻辑。</li>
        </ul>
      </div>
      <div class="card">
        <h2>v17 的特点</h2>
        <ul>
          <li>整条主路径更统一：stream -> atoms -> dossier -> graph -> readiness -> answerability。</li>
          <li>但部分 answer-family 行为更靠近最终 answer 函数。</li>
          <li>因此后续 typed gate 更像“补一个执行接口”，而不是只切换一个布尔项。</li>
        </ul>
      </div>
    </section>

    <section class="card">
      <h2>对主代码的启示</h2>
      <ul>
        <li>answerability 应保留独立入口。</li>
        <li>gate 应支持 family-aware / typed / readiness-aware 决策。</li>
        <li>如果没有稳定 seam，后续实验很难判断准确率提升到底来自哪里。</li>
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
