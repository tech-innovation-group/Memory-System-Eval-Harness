#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web")
OUT_HTML = ROOT / "web" / "static" / "generated-reports" / "echomemory_selfcheck_policy_note_20260617.html"


def load(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def esc(v: Any) -> str:
    return html.escape(str(v))


def main() -> None:
    answerability = load(str(ROOT / "experiments" / "echomemory_nano" / "nano_reference_impl_v14_answerability_benchmark_results.json"))
    selfcheck_v2 = load(str(ROOT / "experiments" / "echomemory_nano" / "nano_dual_backbone_selfcheck_v2_results.json"))
    type_aware = load(str(ROOT / "experiments" / "echomemory_nano" / "nano_type_aware_second_pass_ablation_results.json"))

    a = answerability["summary"]
    s = selfcheck_v2["summary"]
    t = type_aware["summary"]

    unsupported = next(row for row in answerability["rows"] if row["case_id"] == "unsupported")

    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Self-Check Policy Note</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --line:#dbe3ee; --text:#18212f; --muted:#617184;
      --blue:#2563eb; --green:#0f766e; --amber:#b45309;
    }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
    .wrap{{max-width:1220px;margin:0 auto;padding:28px 20px 48px}}
    .hero,.card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:22px 24px;margin-bottom:16px}}
    h1,h2,h3{{margin:0 0 12px;line-height:1.25}}
    .grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}}
    .stats{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:14px}}
    .stat{{border:1px solid var(--line);border-radius:10px;padding:12px;background:#fbfcfe}}
    .label{{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}}
    .value{{font-size:22px;font-weight:700}}
    .note{{border-left:4px solid var(--blue);background:#f7fbff;padding:12px 14px;border-radius:8px}}
    table{{width:100%;border-collapse:collapse}}
    th,td{{border-top:1px solid var(--line);padding:10px 8px;text-align:left;vertical-align:top}}
    th{{font-size:12px;color:var(--muted);background:#fafbfc}}
    code{{background:#f2f4f8;padding:1px 4px;border-radius:4px;font-size:12px}}
    .muted{{color:var(--muted)}}
    ul{{margin:8px 0 0 18px;padding:0}}
    @media (max-width: 960px){{.grid,.stats{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>Why Self-Check Must Become a Controller, Not a Comment</h1>
      <p class="muted">
        这页把三条 nano 证据线串起来：dual-backbone self-check、type-aware second pass、answerability gate。
        共同结论很直接：<b>planner 和 retrieval backbone 还不够，answer-time policy 必须能强执行。</b>
      </p>
      <div class="stats">
        <div class="stat"><span class="label">dual-backbone baseline → self-check v2</span><span class="value">{s['baseline_correct']}/{s['cases']} → {s['selfcheck_correct']}/{s['cases']}</span></div>
        <div class="stat"><span class="label">one-pass → type-aware second pass</span><span class="value">{t['one_pass_contract_ok']}/{t['cases']} → {t['type_aware_contract_ok']}/{t['cases']}</span></div>
        <div class="stat"><span class="label">legacy answerability → enforced gate</span><span class="value">{a['legacy_correct']}/{a['cases']} → {a['enforced_correct']}/{a['cases']}</span></div>
      </div>
    </section>

    <section class="card">
      <h2>三条证据线分别证明什么</h2>
      <div class="grid">
        <div>
          <h3>1. Self-check v2</h3>
          <p>不是只看相似度，而是检查 family 对应的证据形状对不对；不对就补 supporting backbone。</p>
          <p class="muted">结果：{s['baseline_correct']}/{s['cases']} → {s['selfcheck_correct']}/{s['cases']}</p>
        </div>
        <div>
          <h3>2. Type-aware second pass</h3>
          <p>不是固定 graph-only 补检索，而是根据 <code>missing_layers</code> 选 tree / atom / graph。</p>
          <p class="muted">结果：{t['one_pass_contract_ok']}/{t['cases']} → {t['type_aware_contract_ok']}/{t['cases']}</p>
        </div>
        <div>
          <h3>3. Answerability gate</h3>
          <p>即使表面上 <code>contract_ok=true</code>，也不代表问题真的可回答；还要检查 query 中的人物、关系、答案类型是否被支持。</p>
          <p class="muted">结果：{a['legacy_correct']}/{a['cases']} → {a['enforced_correct']}/{a['cases']}</p>
        </div>
      </div>
    </section>

    <section class="card">
      <h2>最关键的 failure case</h2>
      <div class="note">
        unsupported 题：<code>{esc(unsupported['query'])}</code><br />
        old-style answer: <b>{esc(unsupported['legacy'])}</b><br />
        enforced answerability: <b>{esc(unsupported['enforced'])}</b>
      </div>
      <p style="margin-top:12px">
        这题特别重要，因为它说明了一个容易被忽略的问题：
        <b>即使 planner family 正确、表面 contract 也完整，系统仍然可能给出不相关答案。</b>
        所以真正的 self-check 不能停在“报告 coverage”，必须能在答案阶段拦截 unsupported response。
      </p>
    </section>

    <section class="card">
      <h2>对应到主仓代码</h2>
      <table>
        <thead>
          <tr>
            <th>Role</th>
            <th>Code Anchor</th>
            <th>Why it matters</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>planner</td>
            <td><code>/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/planner/query_planner.py</code></td>
            <td>决定 family、primary reader、supporting readers、required evidence。</td>
          </tr>
          <tr>
            <td>contract</td>
            <td><code>/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/evidence_contract.py</code></td>
            <td>定义 evidence family 是否齐全。</td>
          </tr>
          <tr>
            <td>self-check</td>
            <td><code>/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/self_check.py</code></td>
            <td>当前已经能给 recommendation，但还偏 advisory。</td>
          </tr>
          <tr>
            <td>retrieval loop</td>
            <td><code>/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/search_service.py</code></td>
            <td>这里已经有 second pass 闭环，是最适合继续收紧成强控制器的地方。</td>
          </tr>
          <tr>
            <td>nano reference</td>
            <td><code>/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_reference_impl_v14.py</code></td>
            <td>这轮已经把 answerability gate 加进参考实现，便于教学和论文解释。</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="card">
      <h2>对论文主线的意义</h2>
      <ul>
        <li>可以把这条线写成一个很清楚的 claim：<b>contract completeness is necessary but not sufficient for answerability</b>。</li>
        <li>它和近两年的记忆论文是对齐的，但不是数据集 patch，而是更强的 answer-time policy。</li>
        <li>如果后面投 CVPR，这条线特别适合跟 visual grounding / multimodal evidence 一起讲，因为“看到了图”也不等于“答得对图”。</li>
      </ul>
    </section>

    <section class="card">
      <h2>相关产物</h2>
      <ul>
        <li><code>/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_dual_backbone_selfcheck_v2_20260614.html</code></li>
        <li><code>/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_type_aware_second_pass_ablation_20260615.html</code></li>
        <li><code>/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_reference_v14_answerability_benchmark_20260617.html</code></li>
      </ul>
    </section>
  </div>
</body>
</html>"""

    OUT_HTML.write_text(html_text, encoding="utf-8")
    print(str(OUT_HTML))


if __name__ == "__main__":
    main()
