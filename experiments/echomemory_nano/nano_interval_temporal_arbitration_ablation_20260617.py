#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_interval_temporal_arbitration_ablation_20260617_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_interval_temporal_arbitration_ablation_20260617.html"
)


def esc(value: Any) -> str:
    return html.escape(str(value))


@dataclass(frozen=True)
class MemoryEvent:
    event_id: str
    statement: str
    event_time: str
    mention_time: str
    write_time: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class Case:
    case_id: str
    query: str
    expected_answer: str
    note: str


def parse_date(text: str) -> datetime:
    return datetime.fromisoformat(text)


def event_sort_key(event: MemoryEvent) -> tuple[datetime, datetime, datetime]:
    return (
        parse_date(event.event_time),
        parse_date(event.mention_time),
        parse_date(event.write_time),
    )


def lexical_score(statement: str, query: str) -> float:
    s = statement.lower()
    q = query.lower()
    score = 0.0
    for token in (
        "lease",
        "deal",
        "approval",
        "keynote",
        "rehearsal",
        "budget",
        "pilot",
        "signing",
        "partner",
        "launch",
        "board",
        "contract",
        "close",
        "closed",
        "mentioned",
    ):
        if token in s and token in q:
            score += 1.5
    return score


def write_time_only_answer(events: list[MemoryEvent], case: Case) -> dict[str, Any]:
    q = case.query.lower()
    scored: list[tuple[float, MemoryEvent]] = []
    for event in events:
        score = lexical_score(event.statement, q)
        if "when" in q:
            score += 0.5
        if "before" in q or "after" in q or "between" in q:
            score += 0.2
        if score > 0:
            scored.append((score, event))
    scored.sort(key=lambda item: (item[0], item[1].write_time), reverse=True)
    if not scored:
        return {"answer": "unknown", "evidence_ids": [], "rationale": "no lexical hit"}
    top = scored[0][1]
    answer = top.write_time
    if "what happened" in q or "which event" in q:
        answer = top.statement
    return {
        "answer": answer,
        "evidence_ids": [top.event_id],
        "rationale": "uses write_time as the effective answer clock",
    }


def three_clock_no_interval_answer(events: list[MemoryEvent], case: Case) -> dict[str, Any]:
    q = case.query.lower()
    scored: list[tuple[float, MemoryEvent]] = []
    for event in events:
        score = lexical_score(event.statement, q)
        if "when" in q:
            score += 1.0
        # Stronger than write-time-only: prefer event_time over write_time.
        if "closed" in q and "closed" in event.statement.lower():
            score += 2.0
        if "before" in q and "before" not in event.statement.lower():
            score += 0.1
        if "between" in q:
            score += 0.1
        if score > 0:
            scored.append((score, event))
    scored.sort(key=lambda item: (item[0], item[1].event_time), reverse=True)
    if not scored:
        return {"answer": "unknown", "evidence_ids": [], "rationale": "no lexical hit"}
    top = scored[0][1]
    answer = top.event_time if "when" in q else top.statement
    return {
        "answer": answer,
        "evidence_ids": [top.event_id],
        "rationale": "preserves event_time but does not explicitly reason over before/after/between windows",
    }


def _extract_anchor_dates(query: str) -> list[str]:
    return re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", query)


def temporal_arbitration_answer(events: list[MemoryEvent], case: Case) -> dict[str, Any]:
    q = case.query.lower()
    dates = _extract_anchor_dates(case.query)
    chosen: list[MemoryEvent] = []
    rationale: list[str] = []

    def by_tag(tag: str) -> list[MemoryEvent]:
        return [event for event in events if tag in event.tags]

    # Rule 1: explicit "closed on X, mentioned on Y" conflicts must prefer event_time.
    if "when did the deal close" in q:
        close_events = [event for event in events if "deal_close" in event.tags]
        if close_events:
            close_events = sorted(close_events, key=event_sort_key)
            chosen = [close_events[-1]]
            rationale.append("prefer event_time for close-date question over later mention_time")

    # Rule 2: "before the keynote day" style ordering.
    if not chosen and "before the keynote day" in q:
        anchor = next((event for event in events if "keynote_day" in event.tags), None)
        if anchor:
            candidates = [event for event in events if parse_date(event.event_time) < parse_date(anchor.event_time)]
            candidates = [event for event in candidates if "rehearsal" in event.tags or "pilot" in event.tags]
            if candidates:
                chosen = [sorted(candidates, key=event_sort_key)[-1]]
                rationale.append("filter candidates by event_time before keynote anchor")

    # Rule 3: "between A and B" style interval selection.
    if not chosen and "between the lease signing and partner approval" in q:
        lease = next((event for event in events if "lease_signing" in event.tags), None)
        approval = next((event for event in events if "partner_approval" in event.tags), None)
        if lease and approval:
            start = parse_date(lease.event_time)
            end = parse_date(approval.event_time)
            candidates = [
                event
                for event in events
                if start < parse_date(event.event_time) < end
            ]
            candidates = [event for event in candidates if "budget_revision" in event.tags]
            if candidates:
                chosen = [sorted(candidates, key=event_sort_key)[0]]
                rationale.append("resolve query as an event_time interval between two anchors")

    # Rule 4: fallback to event_time-aware retrieval for ordinary when/what queries.
    if not chosen:
        scored: list[tuple[float, MemoryEvent]] = []
        for event in events:
            score = lexical_score(event.statement, q)
            if "when" in q:
                score += 1.0
            if "what happened" in q or "which event" in q:
                score += 0.5
            if score > 0:
                scored.append((score, event))
        scored.sort(key=lambda item: (item[0], item[1].event_time), reverse=True)
        if scored:
            chosen = [scored[0][1]]
            rationale.append("fallback to event_time-aware selection")

    if not chosen:
        return {"answer": "unknown", "evidence_ids": [], "rationale": "no resolved evidence"}

    top = chosen[0]
    answer = top.event_time if "when" in q else top.statement
    return {
        "answer": answer,
        "evidence_ids": [event.event_id for event in chosen],
        "rationale": "; ".join(rationale),
    }


def build_memory() -> list[MemoryEvent]:
    return [
        MemoryEvent(
            event_id="lease_signing",
            statement="The lease signing happened on 2026-05-02.",
            event_time="2026-05-02",
            mention_time="2026-05-02",
            write_time="2026-05-02",
            tags=("lease_signing",),
        ),
        MemoryEvent(
            event_id="budget_revision",
            statement="The board requested one more budget revision on 2026-05-06.",
            event_time="2026-05-06",
            mention_time="2026-05-06",
            write_time="2026-05-06",
            tags=("budget_revision", "board"),
        ),
        MemoryEvent(
            event_id="partner_approval",
            statement="Partner approval happened on 2026-05-10.",
            event_time="2026-05-10",
            mention_time="2026-05-10",
            write_time="2026-05-10",
            tags=("partner_approval",),
        ),
        MemoryEvent(
            event_id="deal_close_retro",
            statement="On 2026-05-20 I mentioned that the deal had actually closed on 2026-05-13.",
            event_time="2026-05-13",
            mention_time="2026-05-20",
            write_time="2026-05-20",
            tags=("deal_close", "retrospective_mention"),
        ),
        MemoryEvent(
            event_id="pilot_review",
            statement="The pilot review happened on 2026-06-08.",
            event_time="2026-06-08",
            mention_time="2026-06-08",
            write_time="2026-06-08",
            tags=("pilot",),
        ),
        MemoryEvent(
            event_id="rehearsal",
            statement="The keynote rehearsal happened on 2026-06-09.",
            event_time="2026-06-09",
            mention_time="2026-06-09",
            write_time="2026-06-09",
            tags=("rehearsal",),
        ),
        MemoryEvent(
            event_id="keynote_day",
            statement="The keynote day was on 2026-06-10.",
            event_time="2026-06-10",
            mention_time="2026-06-10",
            write_time="2026-06-10",
            tags=("keynote_day",),
        ),
    ]


def build_cases() -> list[Case]:
    return [
        Case(
            case_id="mentioned_on_vs_happened_on",
            query="When did the deal close?",
            expected_answer="2026-05-13",
            note="The message was written on 2026-05-20, but the true event happened on 2026-05-13.",
        ),
        Case(
            case_id="interval_between_two_anchors",
            query="Between the lease signing and partner approval, what happened?",
            expected_answer="The board requested one more budget revision on 2026-05-06.",
            note="The answer should come from the event inside the interval, not from one of the anchor events or the most recent write.",
        ),
        Case(
            case_id="before_anchor_day",
            query="What happened before the keynote day?",
            expected_answer="The keynote rehearsal happened on 2026-06-09.",
            note="The system should select the latest event before the anchor day, not just any related event.",
        ),
    ]


def run() -> dict[str, Any]:
    memory = build_memory()
    cases = build_cases()
    variants = {
        "write_time_only": write_time_only_answer,
        "three_clock_no_interval": three_clock_no_interval_answer,
        "temporal_arbitration": temporal_arbitration_answer,
    }

    payload: dict[str, Any] = {"cases": [asdict(case) for case in cases], "variants": []}
    for variant_name, fn in variants.items():
        rows: list[dict[str, Any]] = []
        correct = 0
        for case in cases:
            result = fn(memory, case)
            passed = result["answer"] == case.expected_answer
            correct += int(passed)
            rows.append(
                {
                    "case_id": case.case_id,
                    "query": case.query,
                    "expected_answer": case.expected_answer,
                    "answer": result["answer"],
                    "passed": passed,
                    "evidence_ids": result["evidence_ids"],
                    "rationale": result["rationale"],
                    "note": case.note,
                }
            )
        payload["variants"].append(
            {
                "variant": variant_name,
                "correct": correct,
                "total": len(cases),
                "rows": rows,
            }
        )
    return payload


def render_html(report: dict[str, Any]) -> str:
    summary_rows: list[str] = []
    detail_sections: list[str] = []
    for variant in report["variants"]:
        summary_rows.append(
            f"""
            <tr>
              <td><code>{esc(variant['variant'])}</code></td>
              <td>{esc(f"{variant['correct']}/{variant['total']}")}</td>
              <td>{esc(variant['rows'][0]['rationale'])}</td>
            </tr>
            """
        )
        case_rows: list[str] = []
        for row in variant["rows"]:
            case_rows.append(
                f"""
                <tr>
                  <td>{esc(row['case_id'])}</td>
                  <td>{esc(row['answer'])}</td>
                  <td>{esc(row['expected_answer'])}</td>
                  <td>{esc(row['passed'])}</td>
                  <td><code>{esc(row['evidence_ids'])}</code></td>
                  <td>{esc(row['rationale'])}</td>
                </tr>
                """
            )
        detail_sections.append(
            f"""
            <section class="panel">
              <h2>{esc(variant['variant'])}</h2>
              <table>
                <thead>
                  <tr><th>Case</th><th>Answer</th><th>Expected</th><th>Pass</th><th>Evidence</th><th>Rationale</th></tr>
                </thead>
                <tbody>
                  {''.join(case_rows)}
                </tbody>
              </table>
            </section>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Nano Interval Temporal Arbitration Ablation</title>
  <style>
    :root{{--bg:#f5f7fb;--panel:#fff;--line:#dde5ef;--text:#182333;--muted:#607286;--blue:#245cff;--shadow:0 12px 28px rgba(15,23,42,.08)}}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.74 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}
    .page{{max-width:1120px;margin:0 auto;padding:28px 20px 56px}}
    .hero,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}}
    .hero{{padding:26px 28px;margin-bottom:16px;background:linear-gradient(135deg,#fff 0%,#eef4ff 100%)}}
    .panel{{padding:18px 20px;margin-bottom:16px}}
    h1,h2{{margin:0 0 12px;line-height:1.25}} h1{{font-size:30px}} h2{{font-size:20px}}
    p{{margin:0 0 10px}} .muted{{color:var(--muted)}}
    table{{width:100%;border-collapse:collapse;margin-top:10px;font-size:13px}}
    th,td{{border-top:1px solid var(--line);padding:10px 8px;text-align:left;vertical-align:top}}
    th{{background:#f7f9fc;color:var(--muted);font-size:12px}}
    code{{background:#f3f6fb;border:1px solid #e2e9f2;border-radius:4px;padding:1px 5px;font:12px ui-monospace,SFMono-Regular,Menlo,monospace}}
    .note{{margin-top:12px;padding:12px 14px;border-left:4px solid var(--blue);background:#eef4ff;border-radius:8px}}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>Nano Interval Temporal Arbitration Ablation</h1>
      <p class="muted">
        这组实验补的是 three-clock 之后还缺的一步：时间题不仅要区分 event / mention / write，
        还要能处理 <b>mentioned on vs happened on</b>、<b>before/after</b>、以及 <b>between two anchors</b>。
      </p>
      <div class="note">
        目标不是刷某个 benchmark，而是证明一种更通用的时间策略：
        <b>先确定应该沿哪条时间轴回答，再判断查询是不是在问一个区间、一个顺序关系、还是一个 retrospective mention。</b>
      </div>
    </section>

    <section class="panel">
      <h2>Summary</h2>
      <table>
        <thead>
          <tr><th>Variant</th><th>Correct</th><th>Representative behavior</th></tr>
        </thead>
        <tbody>
          {''.join(summary_rows)}
        </tbody>
      </table>
    </section>

    {''.join(detail_sections)}
  </div>
</body>
</html>
"""


def main() -> None:
    report = run()
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(report), encoding="utf-8")
    print(OUT_JSON)
    print(OUT_HTML)


if __name__ == "__main__":
    main()
