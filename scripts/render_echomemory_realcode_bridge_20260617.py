#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_realcode_bridge_20260617.html"
)


def render() -> str:
    rows = [
        (
            "Relative-time anchoring",
            "already in main code",
            "SearchService can auto-fill `query_time_anchor` from current session messages or meta instead of silently falling back to wall clock.",
            "`_with_query_time_anchor()` in `search_service.py`",
            "Supports the paper claim that temporal QA should resolve relative expressions against a stable query-time anchor.",
        ),
        (
            "Temporal tree retrieval",
            "already in main code",
            "Temporal queries can read dedicated `temporal_tree` blocks and attach explicit `event_time`, `event_time_start`, `mention_time_start`, and related traces.",
            "`_temporal_tree_candidate_keys()` + `_search_temporal_tree()` in `search_service.py`",
            "Shows that chronology is already becoming a retrieval surface, not just passive metadata.",
        ),
        (
            "Topic-dossier route",
            "already in main code",
            "Longitudinal queries can be routed to `topic_dossier` as a first-class primary reader with typed supporting readers.",
            "`QueryPlanner.build()` + `_search_topic_dossier()` in `query_planner.py` / `search_service.py`",
            "Supports the new middle-layer claim beyond nano-only evidence.",
        ),
        (
            "Shared evidence contract",
            "already in main code",
            "Required evidence families are explicitly computed and checked instead of being left implicit in one fused score.",
            "`compute_coverage()` in `evidence_contract.py`",
            "This is the code-level backbone for contract-driven retrieval and answer-time review.",
        ),
        (
            "Coverage-aware gating",
            "already in main code",
            "L2 is not skipped purely because L1 looks strong; the gating policy checks whether required evidence families are still missing.",
            "`RetrievalGatingPolicy.should_skip_l2()` in `retrieval_gating.py`",
            "Directly supports the paper claim that confidence should not substitute for evidence sufficiency.",
        ),
        (
            "Self-check after retrieval",
            "already in main code",
            "Retrieved evidence can be diagnosed as weak/caution/ok, with recommendations such as `expand_supporting_evidence`, `prefer_story_time_evidence`, or `prefer_topic_dossier_evidence`.",
            "`SelfCheckPolicy` in `self_check.py`",
            "Shows that answer-time policy is already visible in the real retrieval loop, not only in toy code.",
        ),
        (
            "Type-aware second pass",
            "already in main code",
            "When evidence is missing, the system can choose supporting readers based on missing evidence families rather than always doing a graph-only retry.",
            "`_collect_second_pass_support()` + `_planned_second_pass_readers()` in `search_service.py`",
            "This is one of the strongest real-code bridges from the paper’s method claim to actual implementation.",
        ),
        (
            "Graph-backed relational support",
            "already in main code",
            "Graph retrieval is seeded and diffused explicitly, then reused in both primary and second-pass support paths.",
            "`GraphSeedPlanner` + `_search_graph()` in `search_service.py`",
            "Supports the dual-backbone claim that relation-heavy questions should not rely on summary-only evidence.",
        ),
        (
            "Temporal-vs-mention-time diagnosis",
            "already in main code",
            "Self-check can explicitly warn when temporal questions rely on mention-time evidence without explicit story-time evidence.",
            "`SelfCheckPolicy` temporal branch in `self_check.py`",
            "This is a concrete real-code reflection of the three-clock paper claim.",
        ),
        (
            "Lifecycle / QA-ready inspection",
            "partially integrated",
            "SearchService can inspect `qa_ready` from current session metadata, but lifecycle state is still thinner than the full six-plane story in the paper.",
            "`_current_session_qa_ready()` in `search_service.py`",
            "The readiness plane is present, but still lighter-weight in main code than in the paper narrative.",
        ),
        (
            "Answerability gate",
            "mostly nano evidence",
            "Main code has self-check diagnostics, but the final strong candidate-level answerability gate remains much more explicit in nano than in current real code.",
            "nano reference: `_answerability_ok()` in `nano_reference_impl_v14.py`",
            "Important claim boundary: contract review is already in code, but final answerability enforcement is not yet equally explicit end-to-end.",
        ),
    ]

    tr = []
    for i, row in enumerate(rows, start=1):
        tr.append(
            f"""
            <tr>
              <td>{i}</td>
              <td><b>{row[0]}</b></td>
              <td>{row[1]}</td>
              <td>{row[2]}</td>
              <td><code>{row[3].replace('`','')}</code></td>
              <td>{row[4]}</td>
            </tr>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Real-Code Bridge</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --line:#dbe3ee; --text:#18212f; --muted:#617184; --blue:#2563eb;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif; }}
    .wrap {{ max-width:1360px; margin:0 auto; padding:28px 20px 56px; }}
    .hero,.card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:22px 24px; margin-bottom:16px; }}
    h1,h2 {{ margin:0 0 12px; line-height:1.25; }}
    h1 {{ font-size:30px; }}
    h2 {{ font-size:21px; }}
    p {{ margin:0 0 12px; }}
    .muted {{ color:var(--muted); }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th,td {{ border-top:1px solid var(--line); text-align:left; vertical-align:top; padding:10px 8px; }}
    th {{ background:#fbfcfe; color:var(--muted); font-size:12px; }}
    code {{ background:#f3f6fb; border:1px solid #e5eaf2; border-radius:6px; padding:1px 4px; font:12px ui-monospace,SFMono-Regular,Menlo,monospace; }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory-MM Real-Code Bridge</h1>
      <p class="muted">
        这页只回答一个问题：论文里讲的那些机制，哪些已经能在当前主仓里直接观察到？
        我刻意把每一项都分成 `already in main code`、`partially integrated`、`mostly nano evidence`，
        避免把 paper narrative 和当前实现状态混在一起。
      </p>
    </section>

    <section class="card">
      <h2>Observable policy signals in current code</h2>
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Signal</th>
            <th>Status</th>
            <th>What is already observable</th>
            <th>Main-code anchor</th>
            <th>Why it matters for the paper</th>
          </tr>
        </thead>
        <tbody>
          {''.join(tr)}
        </tbody>
      </table>
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
