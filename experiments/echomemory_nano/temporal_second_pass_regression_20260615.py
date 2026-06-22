#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import html
import json
from dataclasses import dataclass
from pathlib import Path

from echomem.index_engine.planner.query_planner import QueryPlan
from echomem.index_engine.search_service import SearchService
from echomem.utils.domain.context import ContextItem, RequestContext
from echomem.utils.domain.search import SearchIntentLabel


OUT_DIR = Path("/Users/chx/locomo-eval-web/web/static/generated-reports")
OUT_HTML = OUT_DIR / "echomemory_temporal_second_pass_regression_20260615.html"
OUT_JSON = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano/temporal_second_pass_regression_20260615.json")


def esc(value: object) -> str:
    return html.escape(str(value))


@dataclass
class CaseResult:
    case_id: str
    title: str
    expectation: str
    observed: str
    passed: bool
    notes: str = ""


class FakeFS:
    async def read_text(self, uri: str) -> str:
        return ""


class FakeAtomRetriever:
    async def retrieve(self, query: str, ctx: RequestContext, *, query_vec=None):
        return [
            ContextItem(
                content="Maya joined Acme on 2025-05-02.",
                source_uri="atom://story-time-event",
                memory_type="event",
                confidence=0.92,
                trace={
                    "atom_id": "story-time-event",
                    "node_type": "event",
                    "event_time": "2025-05-02",
                    "mention_time": "2025-05-02T22:00:00Z",
                },
            )
        ]


async def main() -> None:
    svc = SearchService(FakeFS(), atom_retriever=FakeAtomRetriever())
    ctx = RequestContext(
        account_id="acc",
        user_id="user",
        query_time_anchor="2025-05-03T09:00:00Z",
    )
    intent = SearchIntentLabel(
        query="What happened yesterday?",
        memory_types=("events",),
        strategy="experience_recall",
        confidence=1.0,
        rationale="test",
    )
    plan = QueryPlan(
        mode="temporal",
        target_layers=("event", "fact", "episode"),
        temporal_anchor_required=True,
        force_l2=True,
        prefer_event=True,
        note="test",
    )
    mention_only_item = ContextItem(
        content="Maya discussed the launch timeline yesterday.",
        source_uri="atom://mention-only-event",
        memory_type="event",
        confidence=0.9,
        trace={
            "atom_id": "mention-only-event",
            "node_type": "event",
            "event_time": "",
            "mention_time": "2025-05-02T20:00:00Z",
        },
    )
    initial_result = svc._build_result(
        [],
        [],
        [mention_only_item],
        [],
        intent,
        0.0,
        terminated_at="L2",
        termination_reason="complete",
        cumulative_tokens=10,
        query="What happened yesterday?",
        query_plan=plan,
        query_time_anchor=ctx.query_time_anchor,
    )
    before = initial_result.budget_consumed.get("self_check", {})
    refined_result, merged_l2, _ = await svc._apply_self_check_second_pass(
        query="What happened yesterday?",
        ctx=ctx,
        query_vec=None,
        query_plan=plan,
        expand_level="auto",
        intent=intent,
        l0_items=[],
        l1_items=[],
        l2_items=[mention_only_item],
        text_items=[],
        cumulative_tokens=10,
        start_time=0.0,
        terminated_at="L2",
        termination_reason="complete",
        initial_result=initial_result,
    )
    after = refined_result.budget_consumed.get("self_check", {})
    second_pass = refined_result.budget_consumed.get("second_pass", {})

    cases = [
        CaseResult(
            case_id="secondpass1",
            title="Self-check sees mention-only temporal evidence as insufficient story-time support",
            expectation="caution / prefer_story_time_evidence",
            observed=f"{before.get('status')} / {before.get('recommendation')}",
            passed=f"{before.get('status')} / {before.get('recommendation')}" == "caution / prefer_story_time_evidence",
            notes="Policy should explicitly call out the lack of story-time evidence before second pass.",
        ),
        CaseResult(
            case_id="secondpass2",
            title="Second pass is now willing to react to prefer_story_time_evidence",
            expectation="triggered=True, source includes atom",
            observed=f"triggered={second_pass.get('triggered')}, sources={second_pass.get('sources')}",
            passed=bool(second_pass.get("triggered")) and "atom" in (second_pass.get("sources") or []),
            notes="This is the new closed-loop behavior: temporal policy recommendation can now trigger supporting retrieval.",
        ),
        CaseResult(
            case_id="secondpass3",
            title="After supplementation, temporal policy can become answerable",
            expectation="ok / answerable",
            observed=f"{after.get('status')} / {after.get('recommendation')}",
            passed=f"{after.get('status')} / {after.get('recommendation')}" == "ok / answerable",
            notes="Once story-time evidence is added, policy should calm down.",
        ),
    ]

    payload = {
        "summary": {"passed": sum(1 for c in cases if c.passed), "total": len(cases)},
        "before_self_check": before,
        "after_self_check": after,
        "second_pass": second_pass,
        "merged_l2_items": [item.source_uri for item in merged_l2],
        "cases": [c.__dict__ for c in cases],
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = []
    for c in cases:
        badge = "通过" if c.passed else "失败"
        cls = "ok" if c.passed else "risk"
        rows.append(
            f"""
            <tr>
              <td>{esc(c.case_id)}</td>
              <td>{esc(c.title)}</td>
              <td>{esc(c.expectation)}</td>
              <td>{esc(c.observed)}</td>
              <td><span class="pill {cls}">{badge}</span></td>
              <td>{esc(c.notes)}</td>
            </tr>
            """
        )

    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Temporal Second-Pass Regression</title>
  <style>
    :root {{
      --bg:#f6f8fb; --panel:#fff; --line:#d9e3ef; --text:#172435; --muted:#5f6f84;
      --blue:#2563eb; --blue-soft:#eef4ff; --green:#0f8a5f; --green-soft:#eaf8f1;
      --red:#c43d3d; --red-soft:#fff2f2; --shadow:0 14px 34px rgba(15,23,42,.08);
    }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"SF Pro Text","PingFang SC","Segoe UI",sans-serif}}
    .wrap{{max-width:1180px;margin:0 auto;padding:26px 18px 72px}}
    .hero,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow)}}
    .hero{{padding:30px 32px}}
    .panel{{padding:20px 22px;margin-top:16px}}
    .tag,.pill{{display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:600;margin-right:6px;margin-bottom:6px}}
    .tag{{background:var(--blue-soft);color:var(--blue)}}
    .ok{{background:var(--green-soft);color:var(--green)}}
    .risk{{background:var(--red-soft);color:var(--red)}}
    .kpis{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:18px}}
    .kpi{{border:1px solid var(--line);border-radius:10px;padding:14px;background:#fbfcff}}
    .num{{font-size:24px;font-weight:700;line-height:1.1}}
    table{{width:100%;border-collapse:collapse}}
    th,td{{padding:10px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
    th{{background:#f7faff;color:#42556e;font-size:12px;text-transform:uppercase}}
    tr:last-child td{{border-bottom:none}}
    .quote{{border-left:4px solid #b8ccff;background:#f8fbff;padding:12px 14px;border-radius:10px;margin-top:14px}}
    code{{background:#f3f6fb;border:1px solid #e4ebf5;border-radius:6px;padding:1px 5px;font:12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="tag">closed loop</div>
      <div class="tag">self-check</div>
      <div class="tag">second pass</div>
      <div class="tag">temporal policy</div>
      <h1>EchoMemory Temporal Second-Pass Regression</h1>
      <p class="muted">
        这组回归验证的是更完整的一步：temporal policy 不只是“看出 mention-only evidence 有问题”，还要能推动 second pass 去补 story-time evidence，然后让 answerability 变好。
      </p>
      <div class="kpis">
        <div class="kpi"><div class="num">{payload["summary"]["passed"]}/{payload["summary"]["total"]}</div><div class="muted">cases passed</div></div>
        <div class="kpi"><div class="num">policy → retrieval</div><div class="muted">prefer_story_time_evidence can now trigger supplementation</div></div>
        <div class="kpi"><div class="num">closed loop</div><div class="muted">before caution → after answerable</div></div>
      </div>
      <div class="quote">
        <strong>意义：</strong>
        这说明主仓现在不只是“会诊断时间问题”，而是开始具备“时间问题驱动补检索”的闭环雏形。
      </div>
    </section>

    <section class="panel">
      <h2>Case Results</h2>
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Case</th>
            <th>Expectation</th>
            <th>Observed</th>
            <th>Status</th>
            <th>Why it matters</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
    </section>
  </div>
</body>
</html>
"""
    OUT_HTML.write_text(html_text, encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
