#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path("/Users/chx/locomo-eval-web/experiments/echomemory_nano")
OUT_JSON = ROOT / "nano_topic_dossier_state_delta_ablation_20260617_results.json"
OUT_HTML = Path(
    "/Users/chx/locomo-eval-web/web/static/generated-reports/"
    "echomemory_nano_topic_dossier_state_delta_ablation_20260617.html"
)


def esc(value: Any) -> str:
    return html.escape(str(value))


def extract_date(text: str) -> str:
    m = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    return m.group(1) if m else ""


@dataclass
class Obs:
    obs_id: str
    topic: str
    statement: str
    event_time: str
    write_time: str
    subject: str
    predicate: str
    obj: str


@dataclass
class StateVersion:
    version_id: str
    subject: str
    predicate: str
    obj: str
    event_time: str
    write_time: str
    valid_from: str
    valid_until: str = ""
    status: str = "active"
    superseded_by: str = ""


@dataclass
class TimelineDossier:
    topic: str
    lines: list[str]
    by_write_time: list[Obs]


@dataclass
class StateDeltaDossier:
    topic: str
    lines: list[str]
    versions: list[StateVersion]
    latest_state: dict[str, str]
    state_history: dict[str, list[str]]


@dataclass
class Case:
    qid: str
    question: str
    query_time: str
    expected_keywords: list[str]
    family: str
    why: str


class TimelineOnlyMemory:
    """Weak middle layer: dossier is just a write-time timeline."""

    def __init__(self, observations: list[Obs]) -> None:
        self.observations = sorted(observations, key=lambda x: (x.write_time, x.obs_id))
        self.dossiers = self._build_dossiers()

    def _build_dossiers(self) -> dict[str, TimelineDossier]:
        grouped: dict[str, list[Obs]] = {}
        for obs in self.observations:
            grouped.setdefault(obs.topic, []).append(obs)
        dossiers: dict[str, TimelineDossier] = {}
        for topic, rows in grouped.items():
            ordered = sorted(rows, key=lambda x: (x.write_time, x.obs_id))
            dossiers[topic] = TimelineDossier(
                topic=topic,
                lines=[f"{row.event_time} (written {row.write_time[:10]}): {row.statement}" for row in ordered],
                by_write_time=ordered,
            )
        return dossiers

    def answer(self, case: Case) -> tuple[str, str]:
        topic = self._pick_topic(case.question)
        dossier = self.dossiers[topic]
        q = case.question.lower()
        if any(token in q for token in ("current", "latest", "now", "现在", "最新")):
            row = dossier.by_write_time[-1]
            return row.obj, topic
        if "what did" in q or "as of" in q or "on 2026-" in q:
            row = dossier.by_write_time[-1]
            return row.obj, topic
        if any(token in q for token in ("how did", "evolve", "change over time", "演变", "变化")):
            return "\n".join(dossier.lines[:4]), topic
        return dossier.lines[-1], topic

    def _pick_topic(self, question: str) -> str:
        q = question.lower()
        if "living" in q or "city" in q or "apartment" in q:
            return "housing"
        if "prefer" in q or "drink" in q:
            return "preferences"
        return "project_status"


class StateDeltaMemory:
    """Stronger middle layer: dossier carries current slots and version history."""

    def __init__(self, observations: list[Obs]) -> None:
        self.observations = sorted(observations, key=lambda x: (x.event_time, x.write_time, x.obs_id))
        self.dossiers = self._build_dossiers()

    def _build_dossiers(self) -> dict[str, StateDeltaDossier]:
        grouped: dict[str, list[Obs]] = {}
        for obs in self.observations:
            grouped.setdefault(obs.topic, []).append(obs)

        dossiers: dict[str, StateDeltaDossier] = {}
        for topic, rows in grouped.items():
            ordered = sorted(rows, key=lambda x: (x.event_time, x.write_time, x.obs_id))
            versions: list[StateVersion] = []
            slot_versions: dict[tuple[str, str], list[StateVersion]] = {}

            for row in ordered:
                version = StateVersion(
                    version_id=f"{topic}-v{len(versions):03d}",
                    subject=row.subject,
                    predicate=row.predicate,
                    obj=row.obj,
                    event_time=row.event_time,
                    write_time=row.write_time,
                    valid_from=row.event_time,
                )
                key = (row.subject, row.predicate)
                prev_chain = slot_versions.setdefault(key, [])
                if prev_chain:
                    prev = prev_chain[-1]
                    prev.status = "superseded"
                    prev.valid_until = row.event_time
                    prev.superseded_by = version.version_id
                prev_chain.append(version)
                versions.append(version)

            latest_state: dict[str, str] = {}
            state_history: dict[str, list[str]] = {}
            for (subject, predicate), chain in slot_versions.items():
                key = f"{subject}.{predicate}"
                latest = chain[-1]
                latest_state[key] = latest.obj
                state_history[key] = [
                    f"{v.event_time} -> {v.obj} [{v.status if v.status != 'active' else 'active'}]"
                    for v in chain
                ]

            dossiers[topic] = StateDeltaDossier(
                topic=topic,
                lines=[f"{row.event_time}: {row.statement}" for row in ordered],
                versions=versions,
                latest_state=latest_state,
                state_history=state_history,
            )
        return dossiers

    def answer(self, case: Case) -> tuple[str, str]:
        topic = self._pick_topic(case.question)
        dossier = self.dossiers[topic]
        q = case.question.lower()

        if any(token in q for token in ("current", "latest", "now", "现在", "最新")):
            key = self._slot_key(topic)
            return dossier.latest_state.get(key, "unknown"), topic

        if "what did" in q or "as of" in q or "on 2026-" in q:
            key = self._slot_key(topic)
            value = self._value_as_of(dossier, key, case.query_time)
            return value or "unknown", topic

        if any(token in q for token in ("how did", "evolve", "change over time", "演变", "变化")):
            key = self._slot_key(topic)
            history = dossier.state_history.get(key)
            if history:
                return "\n".join(history), topic
            return "\n".join(dossier.lines[:4]), topic

        return dossier.lines[-1], topic

    def _value_as_of(self, dossier: StateDeltaDossier, key: str, query_time: str) -> str:
        for version in dossier.versions:
            slot = f"{version.subject}.{version.predicate}"
            if slot != key:
                continue
            end = version.valid_until or "9999-12-31"
            if version.valid_from <= query_time[:10] < end[:10]:
                return version.obj
        return ""

    def _pick_topic(self, question: str) -> str:
        q = question.lower()
        if "living" in q or "city" in q or "apartment" in q:
            return "housing"
        if "prefer" in q or "drink" in q:
            return "preferences"
        return "project_status"

    def _slot_key(self, topic: str) -> str:
        return {
            "housing": "Maya.lives_in",
            "preferences": "Maya.prefers",
            "project_status": "Alpha.status",
        }[topic]


def build_observations() -> list[Obs]:
    return [
        Obs(
            obs_id="obs-000",
            topic="housing",
            statement="Maya moved to Boston on 2026-02-01.",
            event_time="2026-02-01",
            write_time="2026-02-01T09:00:00",
            subject="Maya",
            predicate="lives_in",
            obj="Boston",
        ),
        Obs(
            obs_id="obs-001",
            topic="housing",
            statement="Maya moved to Seattle on 2026-03-15.",
            event_time="2026-03-15",
            write_time="2026-03-15T09:00:00",
            subject="Maya",
            predicate="lives_in",
            obj="Seattle",
        ),
        Obs(
            obs_id="obs-002",
            topic="housing",
            statement="Before moving, Maya had lived in Boston for five years on 2026-02-01.",
            event_time="2026-02-01",
            write_time="2026-04-01T09:00:00",
            subject="Maya",
            predicate="lives_in",
            obj="Boston",
        ),
        Obs(
            obs_id="obs-003",
            topic="preferences",
            statement="Maya preferred tea on 2026-03-01.",
            event_time="2026-03-01",
            write_time="2026-03-01T08:00:00",
            subject="Maya",
            predicate="prefers",
            obj="tea",
        ),
        Obs(
            obs_id="obs-004",
            topic="preferences",
            statement="Maya preferred coffee on 2026-04-10.",
            event_time="2026-04-10",
            write_time="2026-04-10T08:00:00",
            subject="Maya",
            predicate="prefers",
            obj="coffee",
        ),
        Obs(
            obs_id="obs-005",
            topic="project_status",
            statement="Project Alpha was planned for pilot on 2026-05-01.",
            event_time="2026-05-01",
            write_time="2026-04-20T10:00:00",
            subject="Alpha",
            predicate="status",
            obj="pilot_planned",
        ),
        Obs(
            obs_id="obs-006",
            topic="project_status",
            statement="Project Alpha was delayed on 2026-05-08 because vendor testing slipped.",
            event_time="2026-05-08",
            write_time="2026-05-08T10:00:00",
            subject="Alpha",
            predicate="status",
            obj="delayed",
        ),
        Obs(
            obs_id="obs-007",
            topic="project_status",
            statement="Project Alpha recovered and launched on 2026-05-18.",
            event_time="2026-05-18",
            write_time="2026-05-18T10:00:00",
            subject="Alpha",
            predicate="status",
            obj="launched",
        ),
    ]


def build_cases() -> list[Case]:
    return [
        Case(
            qid="sd1",
            question="Which city does Maya currently live in now?",
            query_time="2026-04-15",
            expected_keywords=["Seattle"],
            family="latest_state",
            why="Current-state questions should not be hijacked by a later retrospective mention of an older state.",
        ),
        Case(
            qid="sd2",
            question="What did Maya prefer on 2026-03-10?",
            query_time="2026-03-10",
            expected_keywords=["tea"],
            family="as_of_state",
            why="A useful middle layer should answer as-of questions, not only current-state snapshots.",
        ),
        Case(
            qid="sd3",
            question="What is Maya's latest preference now?",
            query_time="2026-04-12",
            expected_keywords=["coffee"],
            family="latest_state",
            why="Latest-state queries should use an explicit active slot instead of scanning timeline text.",
        ),
        Case(
            qid="sd4",
            question="How did Project Alpha change over time?",
            query_time="2026-05-20",
            expected_keywords=["pilot_planned", "delayed", "launched"],
            family="evolution",
            why="State-delta should preserve evolution quality rather than improving latest-state at the cost of longitudinal recall.",
        ),
    ]


def score(memory_name: str, memory: Any, cases: list[Case]) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    correct = 0
    for case in cases:
        answer, topic = memory.answer(case)
        passed = all(token.lower() in answer.lower() for token in case.expected_keywords)
        if passed:
            correct += 1
        runs.append(
            {
                "qid": case.qid,
                "question": case.question,
                "topic": topic,
                "family": case.family,
                "answer": answer,
                "passed": passed,
                "why": case.why,
            }
        )
    return {
        "variant": memory_name,
        "correct": correct,
        "total": len(cases),
        "accuracy": correct / len(cases),
        "runs": runs,
    }


def run() -> dict[str, Any]:
    observations = build_observations()
    cases = build_cases()
    timeline = TimelineOnlyMemory(observations)
    state_delta = StateDeltaMemory(observations)
    payload = {
        "cases": [case.__dict__ for case in cases],
        "summary": [
            score("timeline_only_dossier", timeline, cases),
            score("state_delta_dossier", state_delta, cases),
        ],
        "state_delta_snapshot": {
            topic: {
                "latest_state": dossier.latest_state,
                "state_history": dossier.state_history,
            }
            for topic, dossier in state_delta.dossiers.items()
        },
    }
    return payload


def render(payload: dict[str, Any]) -> str:
    summary_rows = []
    for row in payload["summary"]:
        summary_rows.append(
            "<tr>"
            f"<td>{esc(row['variant'])}</td>"
            f"<td>{row['correct']}/{row['total']}</td>"
            f"<td>{row['accuracy']:.2%}</td>"
            "</tr>"
        )

    detail_sections = []
    for row in payload["summary"]:
        cards = []
        for run in row["runs"]:
            cards.append(
                "<div class='card'>"
                f"<h4>{esc(run['qid'])} {'✅' if run['passed'] else '❌'}</h4>"
                f"<p><b>Topic:</b> {esc(run['topic'])}</p>"
                f"<p><b>Question:</b> {esc(run['question'])}</p>"
                f"<p><b>Answer:</b><br>{esc(run['answer'])}</p>"
                f"<p class='muted'>{esc(run['why'])}</p>"
                "</div>"
            )
        detail_sections.append(
            "<section class='panel'>"
            f"<h3>{esc(row['variant'])}</h3>"
            f"<div class='grid'>{''.join(cards)}</div>"
            "</section>"
        )

    snapshots = []
    for topic, block in payload["state_delta_snapshot"].items():
        snapshots.append(
            "<div class='mini'>"
            f"<h4>{esc(topic)}</h4>"
            f"<p><b>latest_state</b></p><pre>{esc(json.dumps(block['latest_state'], ensure_ascii=False, indent=2))}</pre>"
            f"<p><b>state_history</b></p><pre>{esc(json.dumps(block['state_history'], ensure_ascii=False, indent=2))}</pre>"
            "</div>"
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EchoMemory Nano Topic Dossier State Delta Ablation</title>
  <style>
    :root {{ --bg:#f6f8fc;--panel:#fff;--line:#dbe3ee;--text:#172233;--muted:#617186;--blue:#245cff; }}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.72 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}}
    .page{{max-width:1220px;margin:0 auto;padding:24px 18px 56px}}
    .hero,.panel,.card,.mini{{background:var(--panel);border:1px solid var(--line);border-radius:12px}}
    .hero,.panel{{padding:20px;margin-bottom:16px}}
    .hero{{background:linear-gradient(135deg,#fff 0%,#eef4ff 100%)}}
    .grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}
    .snapshot{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}
    .card,.mini{{padding:14px}}
    h1,h2,h3,h4{{margin:0 0 10px;line-height:1.3}} h1{{font-size:28px}} h2{{font-size:20px}} h3{{font-size:17px}} h4{{font-size:15px}}
    p{{margin:8px 0}} .muted{{color:var(--muted)}}
    table{{width:100%;border-collapse:collapse;margin-top:10px}} th,td{{border:1px solid var(--line);padding:10px;text-align:left;vertical-align:top}} th{{background:#f4f7fd}}
    code{{background:#f3f6fb;border:1px solid #e0e7f1;border-radius:4px;padding:1px 5px;font-size:12px}}
    pre{{white-space:pre-wrap;word-break:break-word;background:#f7f9fc;border:1px solid #e5eaf2;border-radius:8px;padding:10px;font-size:12px}}
    ul{{margin:8px 0 0 18px;padding:0}} li{{margin:6px 0}}
    @media (max-width:980px){{.grid,.snapshot{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>EchoMemory Nano: Topic Dossier State-Delta Ablation</h1>
      <p class="muted">
        这个极小实验专门隔离一个问题：<b>topic dossier 只是 timeline，够不够？</b>
        这里不碰 benchmark 关键词，也不碰 topic canonicalization，只固定 topic 分组，然后只比较两种中间层：
        <code>timeline_only_dossier</code> 和 <code>state_delta_dossier</code>。
      </p>
      <ul>
        <li><b>timeline-only</b>：topic 下面只存按 write-time 排的时间线。</li>
        <li><b>state-delta</b>：topic 下面显式维护 <code>latest_state</code>、<code>state_history</code>、<code>valid_from</code>、<code>valid_until</code>、<code>superseded_by</code>。</li>
        <li><b>目标</b>：证明 state-delta 不是数据集技巧，而是解决最新状态 / as-of 状态 / retrospective mention 混淆的通用结构。</li>
      </ul>
    </section>

    <section class="panel">
      <h2>1. Summary</h2>
      <table>
        <thead><tr><th>Variant</th><th>QA Correct</th><th>Accuracy</th></tr></thead>
        <tbody>{''.join(summary_rows)}</tbody>
      </table>
    </section>

    <section class="panel">
      <h2>2. Why this matters</h2>
      <ul>
        <li>如果 dossier 只有 timeline，它很难稳住“当前状态”和“历史状态”之间的边界。</li>
        <li>一旦出现 <b>later retrospective mention</b>，write-time 排序就会把旧状态错误地顶到最前面。</li>
        <li>state-delta 的价值不只在 latest-state；它还让 <b>as-of query</b> 和 <b>evolution query</b> 变成同一套结构上的不同读取方式。</li>
      </ul>
    </section>

    {''.join(detail_sections)}

    <section class="panel">
      <h2>3. What state-delta actually stores</h2>
      <div class="snapshot">{''.join(snapshots)}</div>
    </section>
  </div>
</body>
</html>"""


def main() -> None:
    payload = run()
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_HTML.write_text(render(payload), encoding="utf-8")
    print(json.dumps({"json": str(OUT_JSON), "html": str(OUT_HTML)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
