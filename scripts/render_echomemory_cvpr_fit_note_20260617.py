#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_cvpr_fit_note_20260617.html"
)


def render() -> str:
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory CVPR Fit Note</title>
  <style>
    :root{
      --bg:#f6f8fc; --panel:#fff; --line:#dbe3ee; --text:#18212f; --muted:#617184; --blue:#2563eb;
      --green:#0f766e; --amber:#b45309; --red:#b42318;
    }
    *{box-sizing:border-box}
    body{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}
    .wrap{max-width:1180px;margin:0 auto;padding:28px 20px 56px}
    .hero,.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:22px 24px;margin-bottom:16px}
    .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
    h1,h2,h3{margin:0 0 12px;line-height:1.25}
    h1{font-size:30px} h2{font-size:21px} h3{font-size:16px}
    p{margin:0 0 10px}
    ul{margin:8px 0 0 18px;padding:0}
    li{margin:6px 0}
    .muted{color:var(--muted)}
    .ok{color:var(--green)} .warn{color:var(--amber)} .bad{color:var(--red)}
    @media (max-width: 900px){.grid{grid-template-columns:1fr}}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory-MM 与 CVPR 的适配度说明</h1>
      <p class="muted">
        这页不是官方投稿建议，而是基于当前证据包做的研究判断：这项工作现在更像什么论文，
        如果真要往 CVPR 风格主线推进，还差哪几块证据。
      </p>
    </section>

    <section class="grid">
      <div class="card">
        <h2>当前最像什么</h2>
        <p><span class="ok"><b>更像一篇强机制导向的 memory / agent systems 论文</b></span></p>
        <ul>
          <li>优点在于：结构清晰、机制证据完整、real-code bridge 比较诚实。</li>
          <li>核心贡献是：three-clock、topic dossier、dual backbone、readiness、contract-aware policy。</li>
          <li>现在最强的证据仍然是时间、关系、长程主题、answerability，而不是大规模视觉 benchmark 主表。</li>
        </ul>
      </div>
      <div class="card">
        <h2>为什么它又有一定 CVPR 潜力</h2>
        <p><span class="warn"><b>因为它已经有 multimodal memory 的雏形，但主线还不够重</b></span></p>
        <ul>
          <li>当前方法里已经把 screenshot / OCR / image evidence 作为一等记忆对象。</li>
          <li>visual contract ablation 也说明“图像命中”和“图像证据充足”不是一回事。</li>
          <li>如果进一步把多模态作为主线，而不是附加分支，这篇就更接近 CVPR 可讲的故事。</li>
        </ul>
      </div>
    </section>

    <section class="card">
      <h2>离 CVPR 风格还差什么</h2>
      <ul>
        <li><span class="bad"><b>缺一个更强的视觉主任务。</b></span> 现在视觉证据更多是结构支持，不是全篇最核心 benchmark。</li>
        <li><span class="bad"><b>缺更大规模的 multimodal 主结果表。</b></span> 当前最强的是机制 ablation，不是大规模视觉/多模态 end-task benchmark。</li>
        <li><span class="warn"><b>缺更直接的视觉失败案例分析。</b></span> 现在图像证据讲得清楚，但还可以更像 CVPR 的 error analysis。</li>
        <li><span class="warn"><b>缺一个更强的视觉方法身份。</b></span> 现在主要身份仍是 memory architecture，而不是视觉-多模态方法本身。</li>
      </ul>
    </section>

    <section class="card">
      <h2>如果真要往 CVPR 送，最值得补的 4 件事</h2>
      <ol>
        <li>把 <b>image evidence / OCR / screenshot-grounded memory</b> 提升成全篇主线，而不是一条附加 evidence line。</li>
        <li>做一个更像 <b>multimodal memory benchmark</b> 的主结果集，至少让视觉题不只是 5-case contract ablation。</li>
        <li>增加 <b>visual failure taxonomy</b>：image-only hit、OCR-only hit、missing owner、missing linked event、wrong screenshot grounding。</li>
        <li>把主仓里的 visual route 再往前推，形成一个更完整的 <b>multimodal real-code bridge</b>。</li>
      </ol>
    </section>

    <section class="card">
      <h2>一句话判断</h2>
      <p>
        <span class="ok"><b>今天这套材料已经足够支撑一篇很像“长时多模态记忆系统”的强机制论文。</b></span>
        但如果目标非常具体地对齐到 <b>CVPR 风格主赛道</b>，还需要让“视觉 / 多模态主任务证据”从现在的辅助地位，升级成全篇最硬的主表之一。
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
