#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_reviewer_faq_20260617.html"
)


def render() -> str:
    qa = [
        (
            "这是不是已经证明 benchmark-scale superiority 了？",
            "没有。当前包证明的是机制级和系统级方向成立，不能诚实地说已经在 LoCoMo 或 LongMemEval 上拿到了成熟主表优势。",
        ),
        (
            "这是不是只是在 toy nano 里成立？",
            "也不是。nano 负责把机制讲清楚，但当前主仓里已经能直接观察到 query_time_anchor、temporal_tree、topic_dossier route、coverage-aware gating、self-check、type-aware second pass 等真实信号。",
        ),
        (
            "你们有没有针对某个 benchmark 写关键词表？",
            "当前主张明确反对这种做法。paper package 里专门把 paraphrase robustness、generic topic induction 和 family routing 拿出来，就是为了说明泛化来自结构，而不是数据集 cue hack。",
        ),
        (
            "为什么要单独强调 answerability gate？",
            "因为 `contract_ok=true` 仍可能不 answerable。当前 nano benchmark 已经显示，unsupported relational queries 会吸附到看似相关但实际不支持答案的证据上。",
        ),
        (
            "为什么要加 middle layer，而不是只保留 overview + atoms？",
            "因为 longitudinal / cross-session topic evolution 既不是 global summary 问题，也不是单条 atom 问题。topic dossier 是介于两者之间的必要对象。",
        ),
        (
            "graph 和 temporal tree 到底是什么关系？",
            "它们不是谁替代谁。temporal tree 主要服务 chronology-heavy queries，graph 主要服务 relation-heavy / visual queries。论文主张是 dual-backbone，而不是单一万能 backbone。",
        ),
        (
            "真实代码里最强的 bridge 证据是什么？",
            "最强的是：QueryPlanner 的 typed route、SearchService 的 temporal_tree/topic_dossier readers、RetrievalGatingPolicy 的 coverage gap 检查、SelfCheckPolicy 的 explicit recommendation、以及 missing-type-driven second pass。",
        ),
        (
            "现在最薄弱的地方是什么？",
            "最薄弱的是 benchmark-scale evidence、生产级 multimodal evaluation，以及 main code 里最终强执行的 candidate-level answerability gate。",
        ),
    ]

    blocks = []
    for q, a in qa:
        blocks.append(
            f"""
            <div class="qa">
              <h3>{q}</h3>
              <p>{a}</p>
            </div>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Reviewer FAQ</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --line:#dbe3ee; --text:#18212f; --muted:#617184; --blue:#2563eb;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif; }}
    .wrap {{ max-width:1120px; margin:0 auto; padding:28px 20px 56px; }}
    .hero,.card,.qa {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; }}
    .hero,.card {{ padding:22px 24px; margin-bottom:16px; }}
    .qa {{ padding:16px 18px; margin-bottom:12px; background:#fbfcfe; }}
    h1,h2,h3 {{ margin:0 0 12px; line-height:1.25; }}
    h1 {{ font-size:30px; }}
    h2 {{ font-size:21px; }}
    h3 {{ font-size:16px; }}
    p {{ margin:0; }}
    .muted {{ color:var(--muted); }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory-MM Reviewer FAQ / Claim Boundary</h1>
      <p class="muted">
        这页不是新结果，而是为了把 claim boundary 说干净。它的目标是降低 reviewer 或合作方误读：
        哪些是当前已经能说的，哪些现在还不能说。
      </p>
    </section>

    <section class="card">
      <h2>FAQ</h2>
      {''.join(blocks)}
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
