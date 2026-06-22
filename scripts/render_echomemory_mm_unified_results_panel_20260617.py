#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_mm_unified_results_panel_20260617.html"
)


def render() -> str:
    cards = [
        (
            "Time",
            "Three-clock temporal semantics",
            "`0/4 -> 4/4 -> 4/4`",
            "时间正确性先取决于 schema，再取决于 routing。只留一个 created_at 不够。",
        ),
        (
            "Topic",
            "Topic dossier + generic induction",
            "`1/5 -> 3/5 -> 4/5`; `5/5` no-hint",
            "纵向主题题需要中层对象；泛化不依赖手工 topic hint，但 induced label 仍更粗。",
        ),
        (
            "Backbone",
            "Temporal tree + graph dual route",
            "temporal `3/3`; relation `3/3`; dual `8/12`",
            "时间题和关系题不是一种检索任务，双 backbone 比单主干更稳。",
        ),
        (
            "Policy",
            "Readiness + self-check + answerability gate",
            "`1/5 -> 5/5`; `4/8 -> 8/8`; `2/6 -> 6/6`",
            "persisted 不是 answerable；retrieval 成功也不代表可以直接答。",
        ),
        (
            "Generalization",
            "Generic family routing + type-aware second pass",
            "`8/15 -> 15/15`; contract `1/5 -> 5/5`",
            "泛化应来自 family routing 和 typed evidence completion，而不是 benchmark 关键词表。",
        ),
    ]

    card_html = []
    for title, mech, result, note in cards:
        card_html.append(
            f"""
            <div class="card-mini">
              <div class="eyebrow">{title}</div>
              <h3>{mech}</h3>
              <div class="result">{result}</div>
              <p>{note}</p>
            </div>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory-MM Unified Results Panel</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --line:#dbe3ee; --text:#18212f; --muted:#617184; --blue:#2563eb;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif; }}
    .wrap {{ max-width:1240px; margin:0 auto; padding:28px 20px 56px; }}
    .hero,.panel {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:22px 24px; margin-bottom:16px; }}
    h1,h2,h3 {{ margin:0 0 12px; line-height:1.25; }}
    h1 {{ font-size:30px; }}
    h2 {{ font-size:21px; }}
    h3 {{ font-size:16px; }}
    .muted {{ color:var(--muted); }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }}
    .card-mini {{ border:1px solid var(--line); border-radius:10px; padding:14px; background:#fbfcfe; }}
    .eyebrow {{ font-size:12px; font-weight:700; color:var(--blue); margin-bottom:8px; text-transform:uppercase; }}
    .result {{ font:600 18px/1.3 ui-monospace,SFMono-Regular,Menlo,monospace; margin-bottom:10px; }}
    p {{ margin:0; }}
    @media (max-width: 900px) {{ .grid {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory-MM Unified Results Panel</h1>
      <p class="muted">
        这页把主文最重要的实验结论压成五张卡片。它不替代完整结果表，而是给 reviewer 一个先验阅读顺序：
        先看系统级主张，再决定要不要往下追具体 ablation。
      </p>
    </section>

    <section class="panel">
      <h2>Five takeaways</h2>
      <div class="grid">
        {''.join(card_html)}
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
