#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
RESULTS = ROOT / "nano_minimal_temporal_answerability_20260617_results.json"
OUT = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_minimal_temporal_answerability_walkthrough_20260617.html"
)


def esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def load() -> dict:
    return json.loads(RESULTS.read_text(encoding="utf-8"))


def main() -> None:
    report = load()
    summary = report["summary"]
    code_mapping = report["code_mapping"]
    claims = report["paper_claim"]

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Minimal Temporal + Answerability Nano Walkthrough</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --line:#dbe3ee; --text:#18212f; --muted:#617184; --blue:#2563eb; --shadow:0 10px 24px rgba(15,23,42,.08);
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif; }}
    .wrap {{ max-width:1180px; margin:0 auto; padding:28px 20px 54px; }}
    .hero,.card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; box-shadow:var(--shadow); }}
    .hero {{ padding:26px 28px; margin-bottom:16px; background:linear-gradient(135deg,#fff 0%,#eef4ff 100%); }}
    .card {{ padding:18px 20px; margin-bottom:16px; }}
    h1,h2,h3 {{ margin:0 0 12px; line-height:1.25; }}
    h1 {{ font-size:30px; }} h2 {{ font-size:21px; }} h3 {{ font-size:16px; }}
    p {{ margin:0 0 10px; }}
    ul {{ margin:8px 0 0 18px; padding:0; }}
    li {{ margin:6px 0; }}
    .muted {{ color:var(--muted); }}
    .grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }}
    .metric {{ border:1px solid var(--line); border-radius:10px; background:#fbfcff; padding:12px; }}
    .metric .v {{ font:600 24px/1.3 ui-monospace,SFMono-Regular,Menlo,monospace; margin-top:4px; }}
    code {{ background:#f3f6fb; border:1px solid #e5eaf2; border-radius:6px; padding:1px 4px; font:12px ui-monospace,SFMono-Regular,Menlo,monospace; }}
    .note {{ margin-top:12px; padding:12px 14px; border-left:4px solid var(--blue); background:#f4f8ff; border-radius:8px; }}
    .box {{ border:1px solid var(--line); border-radius:10px; background:#fbfcff; padding:14px; }}
    a {{ color:var(--blue); text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
    @media (max-width:960px) {{ .grid {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>Minimal Nano Walkthrough</h1>
      <p class="muted">
        这页只讲一件事：为什么“时间仲裁 + family-aware answerability”是最小但足够泛化的记忆机制。
      </p>
      <div class="note">
        它不是在记某个数据集的特殊词，而是在处理三类通用结构：<b>mention-time vs event-time</b>、
        <b>relation path grounding</b>、<b>readiness gating</b>。
      </div>
    </section>

    <section class="card">
      <h2>Results</h2>
      <div class="grid">
        <div class="metric"><div>flat_direct</div><div class="v">{summary['flat_direct']['correct']}/{summary['flat_direct']['total']}</div><p class="muted">Only lexical retrieval.</p></div>
        <div class="metric"><div>temporal_only</div><div class="v">{summary['temporal_only']['correct']}/{summary['temporal_only']['total']}</div><p class="muted">Adds explicit temporal arbitration.</p></div>
        <div class="metric"><div>full_family_aware</div><div class="v">{summary['full_family_aware']['correct']}/{summary['full_family_aware']['total']}</div><p class="muted">Adds family-aware answerability gate.</p></div>
      </div>
    </section>

    <section class="card">
      <h2>Mechanism sketch</h2>
      <div class="box">
        <p><code>query</code> -&gt; <code>plan family</code> -&gt; <code>primary read</code> -&gt; <code>temporal arbitration</code> / <code>supporting reread</code> -&gt; <code>answerability gate</code> -&gt; answer</p>
      </div>
    </section>

    <section class="card">
      <h2>Why this generalizes</h2>
      <ul>{''.join(f'<li>{esc(item)}</li>' for item in claims)}</ul>
    </section>

    <section class="card">
      <h2>Code mapping</h2>
      <ul>
        {''.join(f'<li><strong>{esc(item["concept"])}</strong> — <a href="file://{esc(item["real_code"])}">{esc(Path(item["real_code"]).name)}</a> — {esc(item["why"])}</li>' for item in code_mapping)}
      </ul>
    </section>
  </div>
</body>
</html>"""

    OUT.write_text(html, encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
