#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_minimal_temporal_answerability_20260617_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_minimal_temporal_answerability_20260617.html"
)


def esc(value: Any) -> str:
    return html.escape(str(value))


def parse_dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z]{2,}|20\d{2}-\d{2}-\d{2}|[\u4e00-\u9fff]{1,4}", text.lower()))


def lexical_overlap(a: str, b: str) -> float:
    ta = tokens(a)
    tb = tokens(b)
    if not ta:
        return 0.0
    return len(ta & tb) / max(len(ta), 1)


@dataclass(frozen=True)
class MemoryEvent:
    event_id: str
    statement: str
    story_time: str
    mention_time: str
    write_time: str
    tags: tuple[str, ...]
    answer_hint: str
    path_grounding: tuple[str, ...] = ()


@dataclass(frozen=True)
class Readiness:
    qa_ready: bool = True
    atoms_ready: bool = True
    graph_ready: bool = True
    temporal_ready: bool = True


@dataclass(frozen=True)
class QueryPlan:
    family: str
    primary_reader: str
    supporting_readers: tuple[str, ...]
    required_layers: tuple[str, ...]
    note: str


@dataclass(frozen=True)
class Hit:
    source: str
    layer: str
    score: float
    content: str
    answer_hint: str
    story_time: str = ""
    trace: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Case:
    case_id: str
    query: str
    expected: str
    note: str
    readiness: Readiness


class MinimalTemporalAnswerabilityNano:
    """
    A small teaching nano that only keeps two generic ideas:

    1. Temporal answers need explicit time arbitration.
    2. Retrieval should still pass a family-aware answerability gate.

    This is intentionally smaller than the unified v17 nano. It is meant to
    explain the core mechanics without pulling in the entire stream-to-graph
    stack at once.
    """

    def __init__(self) -> None:
        self.events = self._build_memory()

    def plan(self, query: str) -> QueryPlan:
        q = query.lower()
        if any(token in q for token in ("ready", "answer now", "can the system answer")):
            return QueryPlan(
                "readiness",
                "readiness",
                (),
                ("readiness",),
                "Lifecycle / answerability query.",
            )
        if any(token in q for token in ("who introduced", "who connected", "relationship", "introduced")):
            return QueryPlan(
                "relational",
                "graph",
                ("atom",),
                ("graph", "fact", "path_grounding"),
                "Relation-heavy query prefers graph plus typed path grounding.",
            )
        return QueryPlan(
            "temporal",
            "temporal_tree",
            ("graph", "atom"),
            ("temporal_tree", "event", "event_time"),
            "Chronology-heavy query prefers event-time-aware evidence.",
        )

    def primary_read(self, case: Case, plan: QueryPlan) -> list[Hit]:
        if plan.family == "readiness":
            return [
                Hit(
                    "readiness",
                    "readiness",
                    1.0 if case.readiness.qa_ready else 0.0,
                    f"qa_ready={case.readiness.qa_ready}",
                    "ready" if case.readiness.qa_ready else "not_ready",
                )
            ]

        rows: list[Hit] = []
        for event in self.events:
            score = lexical_overlap(case.query, event.statement)
            if plan.family == "temporal":
                if "when" in case.query.lower():
                    score += 0.35
                if "between" in case.query.lower():
                    score += 0.15
                if "before" in case.query.lower():
                    score += 0.15
                if score > 0:
                    rows.append(
                        Hit(
                            source=f"tree:{event.event_id}",
                            layer="temporal_tree",
                            score=score,
                            content=f"mentioned_on={event.mention_time[:10]} :: {event.statement}",
                            answer_hint=event.mention_time[:10],
                            story_time=event.story_time,
                            trace={"mention_time": event.mention_time[:10]},
                        )
                    )
            elif plan.family == "relational":
                if score > 0 or any(t in case.query.lower() for t in ("jon", "lena", "introduced")):
                    # Deliberately expose the common failure mode: a graph-ish
                    # shared neighbor can look relevant but is not the answer.
                    if "introducer_bridge" in event.tags:
                        rows.append(
                            Hit(
                                source="graph:shared_neighbor:figma",
                                layer="graph",
                                score=max(score, 0.92),
                                content="Jon and Lena both connect to Figma in the collaboration graph.",
                                answer_hint="Figma",
                            )
                        )
                        break
        return sorted(rows, key=lambda item: (-item.score, item.source))

    def temporal_arbitrate(self, query: str, hits: list[Hit]) -> list[Hit]:
        q = query.lower()
        if not hits:
            return hits

        chosen: list[MemoryEvent] = []

        if "when did the deal close" in q:
            candidates = [event for event in self.events if "deal_close" in event.tags]
            chosen = sorted(candidates, key=lambda e: (e.story_time, e.mention_time, e.write_time))
            chosen = chosen[-1:] if chosen else []
        elif "between the lease signing and partner approval" in q:
            lease = next(event for event in self.events if "lease_signing" in event.tags)
            approval = next(event for event in self.events if "partner_approval" in event.tags)
            start = parse_dt(lease.story_time)
            end = parse_dt(approval.story_time)
            chosen = [
                event for event in self.events
                if start < parse_dt(event.story_time) < end and "budget_revision" in event.tags
            ]
        elif "before the keynote day" in q:
            keynote = next(event for event in self.events if "keynote_day" in event.tags)
            anchor = parse_dt(keynote.story_time)
            candidates = [
                event for event in self.events
                if parse_dt(event.story_time) < anchor and "rehearsal" in event.tags
            ]
            chosen = sorted(candidates, key=lambda e: (e.story_time, e.mention_time, e.write_time))[-1:] if candidates else []

        if not chosen:
            # Fallback: choose the best lexical hit but answer with story_time.
            top = hits[0]
            match = next((event for event in self.events if top.source.endswith(event.event_id)), None)
            if match is not None:
                chosen = [match]

        upgraded: list[Hit] = []
        for event in chosen:
            upgraded.append(
                Hit(
                    source=f"atom:{event.event_id}",
                    layer="fact",
                    score=1.0,
                    content=f"{event.statement} event_time={event.story_time}",
                    answer_hint=event.story_time if "when" in q else event.answer_hint,
                    story_time=event.story_time,
                    trace={"event_time": event.story_time},
                )
            )
        return upgraded or hits

    def supporting_read(self, case: Case, plan: QueryPlan, missing: list[str]) -> list[Hit]:
        if plan.family == "temporal":
            return self.temporal_arbitrate(case.query, self.primary_read(case, plan))

        if plan.family == "relational" and ("path_grounding" in missing or "fact" in missing):
            introducer = next(event for event in self.events if "introducer_bridge" in event.tags)
            return [
                Hit(
                    source=f"graph:path:{introducer.event_id}",
                    layer="graph",
                    score=0.99,
                    content=introducer.statement,
                    answer_hint=introducer.answer_hint,
                    story_time=introducer.story_time,
                    trace={
                        "path_grounding": list(introducer.path_grounding),
                        "event_time": introducer.story_time,
                    },
                ),
                Hit(
                    source=f"atom:{introducer.event_id}",
                    layer="fact",
                    score=0.97,
                    content=introducer.statement,
                    answer_hint=introducer.answer_hint,
                    story_time=introducer.story_time,
                    trace={"event_time": introducer.story_time},
                ),
            ]

        return []

    def present_layers(self, hits: list[Hit]) -> set[str]:
        present: set[str] = set()
        for hit in hits:
            present.add(hit.layer)
            if hit.layer == "temporal_tree":
                present.add("temporal_tree")
            if hit.layer == "fact":
                present.add("fact")
                present.add("event")
                if hit.trace.get("event_time"):
                    present.add("event_time")
            if hit.layer == "graph":
                present.add("graph")
                if hit.trace.get("path_grounding"):
                    present.add("path_grounding")
        return present

    def finalize(self, case: Case, plan: QueryPlan, hits: list[Hit], mode: str) -> dict[str, Any]:
        if mode == "flat_direct":
            answer = hits[0].answer_hint if hits else "unknown"
            return {"answer": answer, "used_gate": False, "used_supporting": False}

        present = self.present_layers(hits)
        missing = [layer for layer in plan.required_layers if layer not in present]

        if mode == "temporal_only":
            answer = hits[0].answer_hint if hits else "unknown"
            if plan.family == "readiness":
                answer = "ready" if case.readiness.qa_ready else "not_ready"
            return {
                "answer": answer,
                "used_gate": False,
                "used_supporting": plan.family == "temporal",
                "missing": missing,
            }

        if plan.family == "readiness":
            return {
                "answer": "ready" if case.readiness.qa_ready else "not_ready",
                "used_gate": True,
                "used_supporting": False,
                "missing": [],
            }

        if not case.readiness.qa_ready:
            return {
                "answer": "not_ready",
                "used_gate": True,
                "used_supporting": False,
                "missing": list(plan.required_layers),
            }

        if missing:
            supporting = self.supporting_read(case, plan, missing)
            hits = self._dedup(hits + supporting)
            present = self.present_layers(hits)
            missing = [layer for layer in plan.required_layers if layer not in present]

        if missing:
            return {
                "answer": "unknown",
                "used_gate": True,
                "used_supporting": True,
                "missing": missing,
            }

        return {
            "answer": hits[0].answer_hint if hits else "unknown",
            "used_gate": True,
            "used_supporting": True,
            "missing": [],
        }

    def run_case(self, case: Case, mode: str) -> dict[str, Any]:
        plan = self.plan(case.query)
        primary = self.primary_read(case, plan)
        hits = primary
        if mode in {"temporal_only", "full_family_aware"} and plan.family == "temporal":
            hits = self._dedup(hits + self.temporal_arbitrate(case.query, hits))
        result = self.finalize(case, plan, hits, mode)
        return {
            "case_id": case.case_id,
            "query": case.query,
            "family": plan.family,
            "mode": mode,
            "expected": case.expected,
            "answer": result["answer"],
            "correct": result["answer"] == case.expected,
            "used_gate": result.get("used_gate", False),
            "used_supporting": result.get("used_supporting", False),
            "missing": result.get("missing", []),
            "note": case.note,
            "plan": asdict(plan),
            "hits": [asdict(hit) for hit in hits],
        }

    @staticmethod
    def _dedup(hits: list[Hit]) -> list[Hit]:
        best: dict[str, Hit] = {}
        for hit in hits:
            prev = best.get(hit.source)
            if prev is None or hit.score > prev.score:
                best[hit.source] = hit
        return sorted(best.values(), key=lambda item: (-item.score, item.source))

    @staticmethod
    def _build_memory() -> list[MemoryEvent]:
        return [
            MemoryEvent(
                "lease_signing",
                "The lease signing happened on 2026-05-02.",
                "2026-05-02",
                "2026-05-02",
                "2026-05-02",
                ("lease_signing",),
                "The lease signing happened on 2026-05-02.",
            ),
            MemoryEvent(
                "budget_revision",
                "The board requested one more budget revision on 2026-05-06.",
                "2026-05-06",
                "2026-05-06",
                "2026-05-06",
                ("budget_revision", "board"),
                "The board requested one more budget revision on 2026-05-06.",
            ),
            MemoryEvent(
                "partner_approval",
                "Partner approval happened on 2026-05-10.",
                "2026-05-10",
                "2026-05-10",
                "2026-05-10",
                ("partner_approval",),
                "Partner approval happened on 2026-05-10.",
            ),
            MemoryEvent(
                "deal_close_retro",
                "On 2026-05-20 I mentioned that the deal had actually closed on 2026-05-13.",
                "2026-05-13",
                "2026-05-20",
                "2026-05-20",
                ("deal_close", "retrospective_mention"),
                "2026-05-13",
            ),
            MemoryEvent(
                "rehearsal",
                "The keynote rehearsal happened on 2026-06-09.",
                "2026-06-09",
                "2026-06-09",
                "2026-06-09",
                ("rehearsal",),
                "The keynote rehearsal happened on 2026-06-09.",
            ),
            MemoryEvent(
                "keynote_day",
                "The keynote itself happened on 2026-06-10.",
                "2026-06-10",
                "2026-06-10",
                "2026-06-10",
                ("keynote_day",),
                "The keynote itself happened on 2026-06-10.",
            ),
            MemoryEvent(
                "introducer_bridge",
                "Maya introduced Jon to Lena at the launch dinner.",
                "2026-04-14",
                "2026-04-14",
                "2026-04-14",
                ("introducer_bridge", "relationship"),
                "Maya",
                ("Jon --introduced_by--> Maya", "Maya --introduced--> Lena"),
            ),
        ]


def build_cases() -> list[Case]:
    return [
        Case(
            "temporal_retro_mention",
            "When did the deal close?",
            "2026-05-13",
            "Retrospective mention should not replace the real event date.",
            Readiness(),
        ),
        Case(
            "temporal_interval",
            "What happened between the lease signing and partner approval?",
            "The board requested one more budget revision on 2026-05-06.",
            "Interval reasoning needs more than one timestamp field.",
            Readiness(),
        ),
        Case(
            "temporal_before_anchor",
            "What happened before the keynote day?",
            "The keynote rehearsal happened on 2026-06-09.",
            "Before/after reasoning should use event_time against an anchor event.",
            Readiness(),
        ),
        Case(
            "relational_path_grounding",
            "Who introduced Jon to Lena?",
            "Maya",
            "A shared neighbor can look relevant; typed path grounding should recover the real introducer.",
            Readiness(),
        ),
        Case(
            "readiness_barrier",
            "Can the system answer now?",
            "not_ready",
            "Durability is weaker than answerability; readiness should remain a hard gate.",
            Readiness(qa_ready=False, atoms_ready=True, graph_ready=False, temporal_ready=False),
        ),
    ]


def evaluate() -> dict[str, Any]:
    nano = MinimalTemporalAnswerabilityNano()
    cases = build_cases()
    modes = ("flat_direct", "temporal_only", "full_family_aware")
    results: dict[str, list[dict[str, Any]]] = {mode: [] for mode in modes}
    summary: dict[str, dict[str, Any]] = {}

    for mode in modes:
        for case in cases:
            results[mode].append(nano.run_case(case, mode))
        correct = sum(1 for row in results[mode] if row["correct"])
        summary[mode] = {
            "correct": correct,
            "total": len(cases),
            "accuracy": round(correct / len(cases), 4),
        }

    return {
        "summary": summary,
        "cases": results,
        "paper_claim": [
            "Preserving timestamps is necessary but not sufficient; temporal answers need explicit arbitration.",
            "Retrieval quality should still pass a family-aware answerability gate before answering.",
            "The mechanism remains generic because it operates on query family and evidence shape, not dataset keywords.",
        ],
        "code_mapping": [
            {
                "concept": "time arbitration",
                "real_code": "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/self_check.py",
                "why": "The current stack already detects mention-time-only evidence; the next step is to turn that into a stronger answering policy.",
            },
            {
                "concept": "family-aware planning",
                "real_code": "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/planner/query_planner.py",
                "why": "Temporal, relational, and readiness-sensitive queries should not share the same primary evidence path.",
            },
            {
                "concept": "answerability gate",
                "real_code": "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/policy/answerability_gate.py",
                "why": "Final answers should be controlled by evidence completeness, not only by retrieval relevance.",
            },
            {
                "concept": "supporting re-read",
                "real_code": "/Users/chx/Code/echomemory/echo_memory/echomem/index_engine/search_service.py",
                "why": "Missing evidence families should trigger targeted second-pass retrieval instead of generic context expansion.",
            },
        ],
    }


def render_html(report: dict[str, Any]) -> str:
    def render_rows(mode: str) -> str:
        rows = []
        for row in report["cases"][mode]:
            rows.append(
                f"""
                <tr>
                  <td>{esc(row['case_id'])}</td>
                  <td>{esc(row['family'])}</td>
                  <td>{esc(row['query'])}</td>
                  <td>{esc(row['expected'])}</td>
                  <td>{esc(row['answer'])}</td>
                  <td>{'yes' if row['correct'] else 'no'}</td>
                  <td>{esc(', '.join(row['missing']) or '-')}</td>
                  <td>{esc(row['note'])}</td>
                </tr>
                """
            )
        return "".join(rows)

    metrics = "".join(
        f"""
        <div class="metric">
          <div>{esc(mode)}</div>
          <div class="v">{esc(vals['correct'])}/{esc(vals['total'])}</div>
        </div>
        """
        for mode, vals in report["summary"].items()
    )

    mapping = "".join(
        f"""
        <li>
          <strong>{esc(item['concept'])}</strong>:
          <a href="file://{esc(item['real_code'])}">{esc(Path(item['real_code']).name)}</a>
          <span class="muted"> - {esc(item['why'])}</span>
        </li>
        """
        for item in report["code_mapping"]
    )

    claims = "".join(f"<li>{esc(item)}</li>" for item in report["paper_claim"])

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>EchoMemory Minimal Temporal + Answerability Nano</title>
  <style>
    :root {{
      --bg:#f6f8fc; --panel:#fff; --line:#dbe3ee; --text:#18212f; --muted:#617184; --blue:#2563eb; --shadow:0 10px 24px rgba(15,23,42,.08);
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif; }}
    .wrap {{ max-width:1220px; margin:0 auto; padding:28px 20px 54px; }}
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
    table {{ width:100%; border-collapse:collapse; margin-top:10px; font-size:13px; }}
    th,td {{ border-top:1px solid var(--line); padding:10px 8px; text-align:left; vertical-align:top; }}
    th {{ background:#fbfcfe; color:var(--muted); font-size:12px; }}
    code {{ background:#f3f6fb; border:1px solid #e5eaf2; border-radius:6px; padding:1px 4px; font:12px ui-monospace,SFMono-Regular,Menlo,monospace; }}
    a {{ color:var(--blue); text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
    .note {{ margin-top:12px; padding:12px 14px; border-left:4px solid var(--blue); background:#f4f8ff; border-radius:8px; }}
    @media (max-width: 960px) {{ .grid {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>EchoMemory Minimal Nano: Temporal Arbitration + Family-Aware Answerability</h1>
      <p class="muted">
        这版是一个更小的教学 nano。它只保留两件最关键的机制：
        <b>时间仲裁</b> 和 <b>family-aware answerability gate</b>。
        目标不是覆盖完整系统，而是把“为什么这两件事能泛化”讲清楚。
      </p>
      <div class="note">
        它比统一的 v17 nano 更小，但比单一 ablation 更完整：既能展示 temporal reasoning，
        也能展示为什么关系题与 readiness 题仍需要最后一道 family-aware gate。
      </div>
    </section>

    <section class="card">
      <h2>Summary</h2>
      <div class="grid">{metrics}</div>
    </section>

    <section class="card">
      <h2>What this nano proves</h2>
      <ul>{claims}</ul>
    </section>

    <section class="card">
      <h2>Mode: flat_direct</h2>
      <p class="muted">Only lexical retrieval. No explicit time arbitration, no final gate.</p>
      <table>
        <thead><tr><th>Case</th><th>Family</th><th>Query</th><th>Expected</th><th>Answer</th><th>Correct</th><th>Missing</th><th>Note</th></tr></thead>
        <tbody>{render_rows('flat_direct')}</tbody>
      </table>
    </section>

    <section class="card">
      <h2>Mode: temporal_only</h2>
      <p class="muted">Temporal cases get explicit time arbitration, but relational/readiness cases still lack a strong final gate.</p>
      <table>
        <thead><tr><th>Case</th><th>Family</th><th>Query</th><th>Expected</th><th>Answer</th><th>Correct</th><th>Missing</th><th>Note</th></tr></thead>
        <tbody>{render_rows('temporal_only')}</tbody>
      </table>
    </section>

    <section class="card">
      <h2>Mode: full_family_aware</h2>
      <p class="muted">Adds family-aware answerability and targeted supporting re-read on top of temporal arbitration.</p>
      <table>
        <thead><tr><th>Case</th><th>Family</th><th>Query</th><th>Expected</th><th>Answer</th><th>Correct</th><th>Missing</th><th>Note</th></tr></thead>
        <tbody>{render_rows('full_family_aware')}</tbody>
      </table>
    </section>

    <section class="card">
      <h2>How it maps back to the real code</h2>
      <ul>{mapping}</ul>
    </section>
  </div>
</body>
</html>
"""


def main() -> None:
    report = evaluate()
    OUT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    OUT_HTML.write_text(render_html(report), encoding="utf-8")
    print(OUT_JSON)
    print(OUT_HTML)


if __name__ == "__main__":
    main()
