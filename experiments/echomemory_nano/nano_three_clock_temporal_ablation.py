#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


OUT_JSON = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano/nano_three_clock_temporal_ablation_results.json")
OUT_HTML = Path("/Users/chx/locomo-eval-web/web/static/generated-reports/echomemory_nano_three_clock_temporal_ablation_20260615.html")


@dataclass
class Turn:
    turn_id: str
    text: str
    write_time: str


@dataclass
class MemoryRecord:
    record_id: str
    statement: str
    event_time: str
    mention_time: str
    write_time: str


@dataclass
class Case:
    case_id: str
    query: str
    query_time: str
    expected_event_time: str
    expected_keyword: str
    note: str


def extract_records(turns: list[Turn]) -> list[MemoryRecord]:
    records: list[MemoryRecord] = []
    for turn in turns:
        text = turn.text.strip()
        event_time = infer_event_time(text, turn.write_time)
        mention_time = turn.write_time
        records.append(
            MemoryRecord(
                record_id=f"rec-{len(records):03d}",
                statement=text,
                event_time=event_time,
                mention_time=mention_time,
                write_time=turn.write_time,
            )
        )
    return records


def infer_event_time(text: str, write_time: str) -> str:
    direct = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if direct:
        return direct.group(1)

    write_date = write_time[:10]
    if "yesterday" in text.lower():
        return shift_day(write_date, -1)
    if "last week" in text.lower():
        return shift_day(write_date, -7)
    if "two days ago" in text.lower():
        return shift_day(write_date, -2)
    if "last month" in text.lower():
        year, month, day = write_date.split("-")
        y = int(year)
        m = int(month) - 1
        if m == 0:
            y -= 1
            m = 12
        return f"{y:04d}-{m:02d}-{day}"
    return write_date


def shift_day(ymd: str, delta: int) -> str:
    from datetime import datetime, timedelta

    dt = datetime.fromisoformat(ymd)
    return (dt + timedelta(days=delta)).strftime("%Y-%m-%d")


def search_write_time_only(records: list[MemoryRecord], case: Case) -> list[dict]:
    q = case.query.lower()
    hits: list[dict] = []
    for rec in records:
        score = lexical_score(rec.statement, q)
        if "yesterday" in q and rec.write_time[:10] == shift_day(case.query_time[:10], -1):
            score += 5
        if "last week" in q and rec.write_time[:10] >= shift_day(case.query_time[:10], -7):
            score += 2
        if score > 0:
            hits.append(
                {
                    "record_id": rec.record_id,
                    "mode": "write_time_only",
                    "score": score,
                    "event_time": rec.write_time[:10],
                    "evidence_clock": "write_time",
                    "statement": rec.statement,
                }
            )
    return sorted(hits, key=lambda x: (x["score"], x["event_time"]), reverse=True)[:5]


def search_event_mention(records: list[MemoryRecord], case: Case) -> list[dict]:
    q = case.query.lower()
    hits: list[dict] = []
    for rec in records:
        score = lexical_score(rec.statement, q)
        if "yesterday" in q and rec.event_time == shift_day(case.query_time[:10], -1):
            score += 5
        if "last week" in q and shift_day(case.query_time[:10], -7) <= rec.event_time <= case.query_time[:10]:
            score += 4
        if "when" in q and rec.event_time:
            score += 1
        if score > 0:
            hits.append(
                {
                    "record_id": rec.record_id,
                    "mode": "event_mention_split",
                    "score": score,
                    "event_time": rec.event_time,
                    "mention_time": rec.mention_time[:10],
                    "evidence_clock": "event_time",
                    "statement": rec.statement,
                }
            )
    return sorted(hits, key=lambda x: (x["score"], x["event_time"]), reverse=True)[:5]


def search_three_clock(records: list[MemoryRecord], case: Case) -> list[dict]:
    q = case.query.lower()
    hits: list[dict] = []
    for rec in records:
        score = lexical_score(rec.statement, q)
        rationale: list[str] = []
        if "yesterday" in q:
            target = shift_day(case.query_time[:10], -1)
            if rec.event_time == target:
                score += 6
                rationale.append("event_time matches relative day")
            if rec.mention_time[:10] > rec.event_time:
                score += 1
                rationale.append("retrospective mention preserved")
        if "last week" in q:
            start = shift_day(case.query_time[:10], -7)
            if start <= rec.event_time <= case.query_time[:10]:
                score += 5
                rationale.append("event_time in relative week window")
            if rec.mention_time[:10] != rec.event_time:
                score += 1
                rationale.append("mention/event split available")
        if "when" in q and rec.event_time:
            score += 2
            rationale.append("query asks for event date")
        if "what had happened before" in q and rec.event_time < case.query_time[:10]:
            score += 3
            rationale.append("event is before query anchor")
        if score > 0:
            hits.append(
                {
                    "record_id": rec.record_id,
                    "mode": "three_clock",
                    "score": score,
                    "event_time": rec.event_time,
                    "mention_time": rec.mention_time[:10],
                    "write_time": rec.write_time[:10],
                    "evidence_clock": "event_time+mention_time+write_time",
                    "rationale": rationale,
                    "statement": rec.statement,
                }
            )
    return sorted(hits, key=lambda x: (x["score"], x["event_time"]), reverse=True)[:5]


def lexical_score(statement: str, query: str) -> float:
    s = statement.lower()
    score = 0.0
    for keyword in [
        "signed",
        "contract",
        "rehearsal",
        "proposal",
        "rome",
        "investment",
        "design review",
        "board meeting",
        "partner",
        "launch",
    ]:
        if keyword in s and keyword in query:
            score += 2.0
    if "when" in query and re.search(r"\b20\d{2}-\d{2}-\d{2}\b", s):
        score += 1.0
    if "what" in query and len(s) > 0:
        score += 0.5
    return score


def judge_hits(hits: list[dict], case: Case) -> dict:
    if not hits:
        return {"ok": False, "top_event_time": "", "contains_keyword": False}
    top = hits[0]
    blob = "\n".join(hit["statement"] for hit in hits[:3]).lower()
    return {
        "ok": top.get("event_time", "") == case.expected_event_time and case.expected_keyword.lower() in blob,
        "top_event_time": top.get("event_time", ""),
        "contains_keyword": case.expected_keyword.lower() in blob,
    }


def build_cases() -> tuple[list[Turn], list[Case]]:
    turns = [
        Turn("t1", "I signed the venue contract on 2026-03-03, but I'm only telling you now after travel.", "2026-03-10T09:00:00Z"),
        Turn("t2", "Yesterday I finished the budget proposal for the studio launch.", "2026-04-09T18:00:00Z"),
        Turn("t3", "Last week we had the partner board meeting about the Rome launch plan.", "2026-05-18T10:00:00Z"),
        Turn("t4", "Two days ago I finalized the keynote rehearsal.", "2026-05-21T08:30:00Z"),
    ]
    cases = [
        Case(
            case_id="retrospective_contract",
            query="When did I sign the contract?",
            query_time="2026-03-10T10:00:00Z",
            expected_event_time="2026-03-03",
            expected_keyword="contract",
            note="Retrospective mention: write time is later than true event time.",
        ),
        Case(
            case_id="yesterday_budget",
            query="What happened yesterday about the proposal?",
            query_time="2026-04-10T09:00:00Z",
            expected_event_time="2026-04-08",
            expected_keyword="proposal",
            note="Relative day query should resolve against query anchor, not just write time.",
        ),
        Case(
            case_id="last_week_board",
            query="What happened last week about the Rome launch?",
            query_time="2026-05-18T20:00:00Z",
            expected_event_time="2026-05-11",
            expected_keyword="rome launch",
            note="Week-scale recall needs event windowing instead of write-time proximity.",
        ),
        Case(
            case_id="before_anchor_rehearsal",
            query="What had happened before the keynote day?",
            query_time="2026-05-22T09:00:00Z",
            expected_event_time="2026-05-19",
            expected_keyword="rehearsal",
            note="Before/after style questions need event ordering, mention time alone is not enough.",
        ),
    ]
    return turns, cases


def evaluate() -> dict:
    turns, cases = build_cases()
    records = extract_records(turns)
    rows = []
    summary = {
        "cases": len(cases),
        "write_time_only_passed": 0,
        "event_mention_split_passed": 0,
        "three_clock_passed": 0,
    }
    for case in cases:
        write_hits = search_write_time_only(records, case)
        split_hits = search_event_mention(records, case)
        tri_hits = search_three_clock(records, case)
        write_judge = judge_hits(write_hits, case)
        split_judge = judge_hits(split_hits, case)
        tri_judge = judge_hits(tri_hits, case)
        summary["write_time_only_passed"] += int(write_judge["ok"])
        summary["event_mention_split_passed"] += int(split_judge["ok"])
        summary["three_clock_passed"] += int(tri_judge["ok"])
        rows.append(
            {
                "case_id": case.case_id,
                "query": case.query,
                "query_time": case.query_time,
                "expected_event_time": case.expected_event_time,
                "expected_keyword": case.expected_keyword,
                "note": case.note,
                "write_time_only": {"hits": write_hits, "judge": write_judge},
                "event_mention_split": {"hits": split_hits, "judge": split_judge},
                "three_clock": {"hits": tri_hits, "judge": tri_judge},
            }
        )
    return {"turns": [asdict(t) for t in turns], "records": [asdict(r) for r in records], "summary": summary, "cases": rows}


def render_html(payload: dict) -> str:
    rows_html = []
    for case in payload["cases"]:
        rows_html.append(
            f"""
            <tr>
              <td>{html.escape(case['case_id'])}</td>
              <td>{html.escape(case['query'])}<br><span class="muted">{html.escape(case['query_time'])}</span></td>
              <td>{html.escape(case['expected_event_time'])}</td>
              <td>{html.escape(case['write_time_only']['judge']['top_event_time'])} / {'yes' if case['write_time_only']['judge']['ok'] else 'no'}</td>
              <td>{html.escape(case['event_mention_split']['judge']['top_event_time'])} / {'yes' if case['event_mention_split']['judge']['ok'] else 'no'}</td>
              <td>{html.escape(case['three_clock']['judge']['top_event_time'])} / {'yes' if case['three_clock']['judge']['ok'] else 'no'}</td>
              <td>{html.escape(case['note'])}</td>
            </tr>
            """
        )
    first = payload["cases"][0]
    summary = payload["summary"]
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Nano Three-Clock Temporal Ablation</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --text:#18212f; --muted:#5f6f82; --line:#dbe3ee;
      --blue:#2563eb; --green:#14804a; --amber:#b7791f; --shadow:0 10px 28px rgba(15,23,42,.08);
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.7 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",sans-serif; }}
    .wrap {{ max-width:1160px; margin:0 auto; padding:28px 20px 48px; }}
    .hero,.section {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:20px 22px; margin-bottom:16px; box-shadow:var(--shadow); }}
    h1,h2,h3 {{ margin:0 0 10px; }}
    .kpis {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-top:14px; }}
    .kpi {{ border:1px solid var(--line); border-radius:10px; padding:12px 14px; background:#fbfcff; }}
    .label {{ display:block; font-size:12px; color:var(--muted); margin-bottom:4px; }}
    .value {{ font-size:22px; font-weight:700; }}
    .muted {{ color:var(--muted); font-size:12px; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th,td {{ border-top:1px solid var(--line); text-align:left; vertical-align:top; padding:10px 8px; }}
    th {{ background:#fbfcfe; color:var(--muted); font-size:12px; text-transform:uppercase; }}
    pre {{ background:#f8fafc; border:1px solid var(--line); border-radius:8px; padding:12px; overflow:auto; font-size:12px; }}
    ul {{ margin:8px 0 0 18px; }}
    .badge {{ display:inline-block; border-radius:999px; padding:2px 8px; font-size:12px; font-weight:600; margin-right:8px; }}
    .blue {{ background:#eef4ff; color:var(--blue); }}
    .green {{ background:#edf9f1; color:var(--green); }}
    .amber {{ background:#fff7e8; color:var(--amber); }}
    @media (max-width:980px) {{ .kpis {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>EchoMemory Nano: Three-Clock Temporal Ablation</h1>
      <p>
        这个 nano 只回答一个机制问题：<b>如果把事件发生时间、提到时间、写入时间混成一个字段，时间题会坏到什么程度？</b>
        我们对比三种 memory 设计：
      </p>
      <ul>
        <li><span class="badge amber">write-time only</span> 只保留写入时间，最接近很多简化记忆系统的默认行为。</li>
        <li><span class="badge blue">event + mention split</span> 至少把事件时间和提到时间分开。</li>
        <li><span class="badge green">three-clock</span> 同时保留 <code>event_time / mention_time / write_time</code>，并在 query-time 显式使用。</li>
      </ul>
      <div class="kpis">
        <div class="kpi"><span class="label">Cases</span><span class="value">{summary['cases']}</span></div>
        <div class="kpi"><span class="label">Write-time only</span><span class="value">{summary['write_time_only_passed']}</span></div>
        <div class="kpi"><span class="label">Event + Mention</span><span class="value">{summary['event_mention_split_passed']}</span></div>
        <div class="kpi"><span class="label">Three-clock</span><span class="value">{summary['three_clock_passed']}</span></div>
      </div>
    </div>

    <div class="section">
      <h2>为什么这个实验重要</h2>
      <p>
        LoCoMo、LongMemEval、TiMem 这类工作反复暴露一个问题：很多 long-memory 失败不是“完全没记住”，而是
        <b>时间语义被压扁了</b>。当前主仓里也有这个风险，比如 organized event 在缺失 story time 时会回退到 created time。
      </p>
      <p>
        这个 nano 的目标不是做 benchmark 提分，而是把这个 failure mode 单独拎出来说明白。
      </p>
    </div>

    <div class="section">
      <h2>结果总表</h2>
      <table>
        <thead>
          <tr>
            <th>Case</th>
            <th>Query / Query Time</th>
            <th>Expected Event Time</th>
            <th>Write-time only</th>
            <th>Event + Mention</th>
            <th>Three-clock</th>
            <th>Note</th>
          </tr>
        </thead>
        <tbody>{''.join(rows_html)}</tbody>
      </table>
    </div>

    <div class="section">
      <h2>结论</h2>
      <ul>
        <li><b>只保留 write time</b> 时，retrospective mention 和 relative-time query 最容易答错。</li>
        <li><b>event + mention split</b> 已经能修掉一大半问题，但仍看不到“为什么这条证据在当前查询里可靠”。</li>
        <li><b>three-clock</b> 的关键不是多一个字段，而是让 retrieval / self-check / reader 都知道它们分别代表什么。</li>
      </ul>
    </div>

    <div class="section">
      <h2>Example Payload</h2>
      <pre>{html.escape(json.dumps(first, ensure_ascii=False, indent=2))}</pre>
    </div>
  </div>
</body>
</html>"""


def main() -> None:
    payload = evaluate()
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render_html(payload), encoding="utf-8")
    print(json.dumps({"ok": True, "json": str(OUT_JSON), "html": str(OUT_HTML)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
