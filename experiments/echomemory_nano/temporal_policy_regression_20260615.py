#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path

from echomem.index_engine.planner.query_planner import QueryPlan
from echomem.index_engine.policy.self_check import SelfCheckPolicy
from echomem.utils.domain.context import ContextItem
from echomem.utils.domain.search import SearchIntentLabel


OUT_DIR = Path("/Users/chx/locomo-eval-web/web/static/generated-reports")
OUT_HTML = OUT_DIR / "echomemory_temporal_policy_regression_20260615.html"
OUT_JSON = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano/temporal_policy_regression_20260615.json")


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


def make_item(
    *,
    atom_id: str,
    memory_type: str,
    confidence: float,
    event_time: str = "",
    mention_time: str = "",
) -> ContextItem:
    return ContextItem(
        content=f"Mock evidence for {atom_id}",
        source_uri=f"atom://{atom_id}",
        memory_type=memory_type,
        confidence=confidence,
        trace={
            "atom_id": atom_id,
            "node_type": memory_type,
            "event_time": event_time,
            "mention_time": mention_time,
        },
    )


def run_case(
    *,
    case_id: str,
    title: str,
    items: list[ContextItem],
    query_time_anchor: str,
    expected_recommendation: str,
    expected_status: str,
    notes: str,
) -> CaseResult:
    policy = SelfCheckPolicy()
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
    report = policy.evaluate(
        query="What happened yesterday?",
        intent=intent,
        query_plan=plan,
        items=items,
        terminated_at="L2",
        termination_reason="complete",
        query_time_anchor=query_time_anchor,
    )
    observed = f"{report['status']} / {report['recommendation']}"
    expected = f"{expected_status} / {expected_recommendation}"
    return CaseResult(
        case_id=case_id,
        title=title,
        expectation=expected,
        observed=observed,
        passed=observed == expected,
        notes=notes,
    )


def main() -> None:
    cases = [
        run_case(
            case_id="policy1",
            title="Temporal query without query-time anchor should not look answerable",
            items=[make_item(atom_id="a1", memory_type="event", confidence=0.9, event_time="2025-05-02")],
            query_time_anchor="",
            expected_recommendation="consider_unknown",
            expected_status="weak",
            notes="Policy should explicitly flag missing query-time anchor for relative-time questions.",
        ),
        run_case(
            case_id="policy2",
            title="Temporal query with anchor but mention-time-only evidence should ask for story-time evidence",
            items=[make_item(atom_id="a2", memory_type="event", confidence=0.9, mention_time="2025-05-02T20:00:00Z")],
            query_time_anchor="2025-05-03T09:00:00Z",
            expected_recommendation="prefer_story_time_evidence",
            expected_status="caution",
            notes="Policy should distinguish retrospective mention from actual event occurrence.",
        ),
        run_case(
            case_id="policy3",
            title="Temporal query with anchor and event-time evidence can be answerable",
            items=[make_item(atom_id="a3", memory_type="event", confidence=0.9, event_time="2025-05-02", mention_time="2025-05-02T21:00:00Z")],
            query_time_anchor="2025-05-03T09:00:00Z",
            expected_recommendation="answerable",
            expected_status="ok",
            notes="Once both the query-time anchor and story-time evidence are present, policy can stay calm.",
        ),
    ]

    payload = {
        "summary": {"passed": sum(1 for c in cases if c.passed), "total": len(cases)},
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
  <title>EchoMemory Temporal Policy Regression</title>
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
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="tag">policy layer</div>
      <div class="tag">self-check</div>
      <div class="tag">temporal queries</div>
      <h1>EchoMemory Temporal Policy Regression</h1>
      <p class="muted">
        这组回归不测存储和检索，而是专门验证 policy 层现在是不是开始真正理解 temporal query 的两个关键前提：<strong>query-time anchor</strong> 和 <strong>story-time evidence</strong>。
      </p>
      <div class="kpis">
        <div class="kpi"><div class="num">{payload["summary"]["passed"]}/{payload["summary"]["total"]}</div><div class="muted">cases passed</div></div>
        <div class="kpi"><div class="num">anchor-aware</div><div class="muted">no anchor → not confidently answerable</div></div>
        <div class="kpi"><div class="num">story-time-aware</div><div class="muted">mention-only → caution</div></div>
      </div>
      <div class="quote">
        <strong>要点：</strong>
        temporal policy 不应该只问“有没有时间相关 evidence”，而应该进一步问“这个 evidence 是 story-time 还是只是 mention-time”。
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
    main()
